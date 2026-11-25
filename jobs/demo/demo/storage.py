"""Envio dos Parquet ao Supabase Storage (o mesmo jeito do snapshot)."""

import io
import time
from urllib.parse import quote

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

# erro 5xx ou queda de conexão costuma ser passageiro: tenta de novo depois de 10 s e de 30 s
ESPERAS = (10, 30)


def para_parquet(tabela: pa.Table) -> bytes:
    buffer = io.BytesIO()
    pq.write_table(tabela, buffer, compression="zstd")
    return buffer.getvalue()


def enviar(url: str, chave: str, bucket: str, caminho: str, dados: bytes) -> None:
    destino = f"{url}/storage/v1/object/{bucket}/{quote(caminho)}"
    headers = {
        "Authorization": f"Bearer {chave}",
        "apikey": chave,
        "x-upsert": "true",
        "Content-Type": "application/octet-stream",
    }
    motivo = ""
    for tentativa in range(len(ESPERAS) + 1):
        if tentativa:
            time.sleep(ESPERAS[tentativa - 1])
        try:
            resposta = httpx.post(destino, headers=headers, content=dados, timeout=300)
        except httpx.TransportError as exc:
            motivo = type(exc).__name__
            continue
        if resposta.status_code < 300:
            return
        motivo = f"HTTP {resposta.status_code} {' '.join(resposta.text.split())[:200]}"
        if resposta.status_code < 500:
            break
    raise RuntimeError(f"falha ao enviar {caminho}: {motivo}")

"""Inventário do bucket: lista os Parquet e descreve colunas, tipos e nulos de cada um.

Uso (em jobs/carga, com SUPABASE_URL e SUPABASE_SECRET_KEY no .env ou no ambiente):

    uv run python -m carga.inspecionar                 # prefixo dataflow → inventario_bucket.md
    uv run python -m carga.inspecionar dataflow saida.md

Só lê o Storage; não conecta no banco e não grava nada no bucket. Não imprime
linhas de dados, só a estrutura e os valores distintos das colunas de empresa
(que viram o cfg.mapa_empresa).
"""

import io
import sys
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from pydantic import ValidationError

from carga import storage
from carga.config import ConfigStorage
from carga.erros import ErroCarga
from carga.esquema import ddl_tabela, nome_tabela_mart
from carga.validacao import COLUNA_DATA_SNAPSHOT

LIMITE_PAGINA = 1000
MAX_VALORES_EMPRESA = 50


@dataclass(frozen=True)
class Objeto:
    caminho: str
    tamanho: int | None


def listar(config: ConfigStorage, prefixo: str, cliente: httpx.Client) -> Iterator[Objeto]:
    """Percorre o bucket a partir do prefixo (o Storage lista uma "pasta" por vez)."""
    chave = config.supabase_secret_key.get_secret_value()
    url = f"{config.supabase_url}/storage/v1/object/list/{config.supabase_bucket}"
    headers = {"apikey": chave, "Authorization": f"Bearer {chave}"}

    pastas = [prefixo.strip("/")]
    while pastas:
        pasta = pastas.pop(0)
        offset = 0
        while True:
            resposta = cliente.post(
                url,
                headers=headers,
                json={
                    "prefix": pasta,
                    "limit": LIMITE_PAGINA,
                    "offset": offset,
                    "sortBy": {"column": "name", "order": "asc"},
                },
            )
            if not resposta.is_success:
                raise ErroCarga(
                    f"falha ao listar {config.supabase_bucket}/{pasta}: "
                    f"HTTP {resposta.status_code} {resposta.text[:200]}"
                )
            itens: list[dict[str, Any]] = resposta.json()
            for item in itens:
                caminho = f"{pasta}/{item['name']}" if pasta else item["name"]
                if item.get("id") is None:  # pasta
                    pastas.append(caminho)
                else:
                    metadados = item.get("metadata") or {}
                    yield Objeto(caminho=caminho, tamanho=metadados.get("size"))
            if len(itens) < LIMITE_PAGINA:
                break
            offset += LIMITE_PAGINA


def _e_coluna_de_empresa(nome: str) -> bool:
    return "empresa" in nome.lower()


def descrever(caminho: str, dados: bytes) -> str:
    """Seção em Markdown com a estrutura de um Parquet."""
    tabela = pq.read_table(io.BytesIO(dados))
    linhas = [f"### `{caminho}`", ""]

    datas = "-"
    if COLUNA_DATA_SNAPSHOT in tabela.column_names:
        distintas = sorted(
            str(d) for d in pc.unique(tabela.column(COLUNA_DATA_SNAPSHOT)).to_pylist()
        )
        datas = ", ".join(distintas[:5]) + (" …" if len(distintas) > 5 else "")
    linhas += [
        f"- linhas: {tabela.num_rows:,}".replace(",", "."),
        f"- tamanho: {len(dados) / 1024:,.0f} KB".replace(",", "."),
        f"- {COLUNA_DATA_SNAPSHOT}: {datas}",
        "",
        "| # | coluna | tipo | nulos |",
        "|---|---|---|---|",
    ]
    for i, campo in enumerate(tabela.schema, start=1):
        nulos = tabela.column(campo.name).null_count
        linhas.append(f"| {i} | `{campo.name}` | {campo.type} | {nulos} |")

    try:
        sugerido = ddl_tabela(nome_tabela_mart(caminho), tabela.schema)
    except ValueError as exc:
        sugerido = f"-- não foi possível sugerir: {exc}"
    linhas += ["", "SQL sugerido para a migration:", "", "```sql", sugerido.rstrip(), "```"]

    for campo in tabela.schema:
        if not _e_coluna_de_empresa(campo.name):
            continue
        contagem = sorted(
            Counter(tabela.column(campo.name).to_pylist()).items(), key=lambda v: str(v[0])
        )
        linhas += ["", f"Valores distintos de `{campo.name}` ({len(contagem)}):", ""]
        for valor, n in contagem[:MAX_VALORES_EMPRESA]:
            linhas.append(f"- `{valor!r}`: {n} linhas")
        if len(contagem) > MAX_VALORES_EMPRESA:
            linhas.append(f"- … e mais {len(contagem) - MAX_VALORES_EMPRESA}")

    return "\n".join(linhas) + "\n"


def inventario(
    config: ConfigStorage,
    prefixo: str,
    cliente: httpx.Client,
    baixar: Callable[[str], bytes] | None = None,
) -> str:
    baixar = baixar or (lambda caminho: storage.baixar(config, caminho, cliente))
    objetos = [o for o in listar(config, prefixo, cliente) if o.caminho.endswith(".parquet")]

    partes = [
        f"# Inventário do bucket `{config.supabase_bucket}/{prefixo.strip('/')}`",
        "",
        f"{len(objetos)} arquivo(s) Parquet.",
        "",
    ]
    partes += [f"- `{o.caminho}`" for o in objetos]
    partes.append("")
    for objeto in objetos:
        try:
            partes.append(descrever(objeto.caminho, baixar(objeto.caminho)))
        except (ErroCarga, pa.ArrowInvalid, OSError) as exc:
            partes.append(f"### `{objeto.caminho}`\n\nERRO ao ler: {exc}\n")
    return "\n".join(partes)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    prefixo = args[0] if args else "dataflow"
    destino = Path(args[1] if len(args) > 1 else "inventario_bucket.md")
    try:
        config = ConfigStorage()  # valores vêm do ambiente / .env
    except ValidationError as exc:
        print(f"Configuração inválida:\n{exc}", file=sys.stderr)
        return 2

    try:
        with httpx.Client(timeout=storage.TIMEOUT) as cliente:
            texto = inventario(config, prefixo, cliente)
    except ErroCarga as exc:
        print(f"Inventário não gerado: {exc}", file=sys.stderr)
        return 2

    destino.write_text(texto, encoding="utf-8")
    print(f"Inventário gravado em {destino.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

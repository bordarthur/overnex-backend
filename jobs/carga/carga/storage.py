"""Leitura do Supabase Storage. Só a carga usa a chave secret."""

import json
from urllib.parse import quote

import httpx

from carga.config import Settings
from carga.erros import ErroCarga

TIMEOUT = httpx.Timeout(30.0, read=300.0)


def _status_real(resposta: httpx.Response) -> int:
    """O Storage às vezes responde 400 com o status de verdade no corpo ({"statusCode": "404"})."""
    if resposta.status_code != 400:
        return resposta.status_code
    try:
        corpo = json.loads(resposta.text)
        return int(corpo.get("statusCode", 400))
    except (ValueError, TypeError, AttributeError):
        return 400


def baixar(settings: Settings, caminho: str, cliente: httpx.Client | None = None) -> bytes:
    """GET /storage/v1/object/<bucket>/<caminho>. Levanta ErroCarga com mensagem clara."""
    alvo = f"{settings.supabase_bucket}/{caminho}"
    url = f"{settings.supabase_url}/storage/v1/object/{quote(alvo)}"
    chave = settings.supabase_secret_key.get_secret_value()
    headers = {"apikey": chave, "Authorization": f"Bearer {chave}"}

    try:
        if cliente is None:
            with httpx.Client(timeout=TIMEOUT) as c:
                resposta = c.get(url, headers=headers)
        else:
            resposta = cliente.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise ErroCarga(f"falha de rede ao baixar {alvo}: {exc}") from exc

    if resposta.is_success:
        return resposta.content

    status = _status_real(resposta)
    if status == 404:
        raise ErroCarga(f"arquivo não existe no bucket: {alvo} (o snapshot dessa tabela rodou?)")
    if status in (401, 403):
        raise ErroCarga(
            f"acesso negado ao Storage (HTTP {status}) ao baixar {alvo}: "
            "confira SUPABASE_SECRET_KEY (chave secret do projeto) e SUPABASE_URL"
        )
    raise ErroCarga(
        f"erro do Storage ao baixar {alvo}: HTTP {resposta.status_code} {resposta.text[:200]}"
    )

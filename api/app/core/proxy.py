"""Segredo do proxy: só o Worker da Cloudflare fala com a API publicada.

O Worker do front repassa `/api/*` à API com o cabeçalho `X-Proxy-Segredo` e sobrescreve o
`X-Forwarded-For` com o IP de quem acessa (`CF-Connecting-IP`). Quem chama a API direto (pelo
endereço `*.azurewebsites.net`) não tem o segredo e recebe 403, então o uvicorn pode confiar no
`X-Forwarded-For` de qualquer origem (`--forwarded-allow-ips="*"`): pedido com IP forjado não
passa daqui.

Middleware ASGI puro (sem BaseHTTPMiddleware): roda antes das rotas e não lê o corpo.
"""

import hmac
import json

from starlette.types import ASGIApp, Receive, Scope, Send

CABECALHO = b"x-proxy-segredo"
# Livre: o App Service e quem publica conferem a API por aqui, sem o segredo.
CAMINHOS_LIVRES = frozenset({"/health"})
# A mesma resposta da negação de acesso das telas: não diz o motivo.
_CORPO_NEGADO = json.dumps({"detail": "Acesso negado."}, separators=(",", ":")).encode()


class SegredoDoProxy:
    def __init__(self, app: ASGIApp, segredo: str) -> None:
        if not segredo:
            raise ValueError("SegredoDoProxy precisa de um segredo.")
        self.app = app
        self._segredo = segredo.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # lifespan passa direto
        if scope["type"] not in ("http", "websocket") or scope["path"] in CAMINHOS_LIVRES:
            await self.app(scope, receive, send)
            return
        if self._confere(scope):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            # a API não tem websocket; se um dia tiver, segue a mesma regra
            await send({"type": "websocket.close", "code": 1008})
            return
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(_CORPO_NEGADO)).encode()),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": _CORPO_NEGADO})

    def _confere(self, scope: Scope) -> bool:
        valores = [v for k, v in scope["headers"] if k.lower() == CABECALHO]
        # repetido não vale: o Worker manda um só
        if len(valores) != 1:
            return False
        # tempo constante: não dá para descobrir o segredo pelo tempo da resposta
        return hmac.compare_digest(valores[0], self._segredo)

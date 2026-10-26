"""Cliente HTTP do Supabase Auth (GoTrue), só com as chamadas que o Overnex usa.

As rotas de /api/auth usam a chave publishable; o CLI de cadastro usa a chave secreta
(convite e exclusão de usuário). Nada aqui grava senha ou token em log.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import httpx

logger = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(10.0, connect=5.0)

TipoLink = Literal["invite", "recovery"]


class GoTrueIndisponivel(Exception):
    """GoTrue fora do ar, lento demais (timeout) ou com erro 5xx."""


class GoTrueErro(Exception):
    """O GoTrue recusou o pedido (4xx). `codigo` é o error_code dele (ex.: invalid_credentials)."""

    def __init__(self, status: int, codigo: str | None, corpo: dict[str, Any]) -> None:
        super().__init__(f"GoTrue {status} ({codigo})")
        self.status = status
        self.codigo = codigo
        self.corpo = corpo


@dataclass(frozen=True)
class SessaoGoTrue:
    access_token: str
    refresh_token: str
    expira_em: datetime
    id_autenticacao: UUID
    email: str | None


def _parece_jwt(chave: str) -> bool:
    # chaves legadas (anon, service_role) são JWT; as novas (sb_publishable_…, sb_secret_…)
    # não, e só podem ir no header apikey
    return chave.startswith("eyJ")


def _sessao(dados: dict[str, Any]) -> SessaoGoTrue:
    try:
        if dados.get("expires_at"):
            expira_em = datetime.fromtimestamp(int(dados["expires_at"]), tz=UTC)
        else:
            expira_em = datetime.now(UTC) + timedelta(seconds=int(dados["expires_in"]))
        usuario = dados["user"]
        return SessaoGoTrue(
            access_token=str(dados["access_token"]),
            refresh_token=str(dados["refresh_token"]),
            expira_em=expira_em,
            id_autenticacao=UUID(str(usuario["id"])),
            email=usuario.get("email"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise GoTrueIndisponivel("resposta de sessão inesperada do GoTrue") from exc


class GoTrue:
    def __init__(
        self,
        supabase_url: str,
        apikey: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._apikey = apikey
        self._http = httpx.Client(
            base_url=f"{supabase_url}/auth/v1", timeout=TIMEOUT, transport=transport
        )

    def close(self) -> None:
        self._http.close()

    def _pedir(
        self,
        metodo: str,
        caminho: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        access_token: str | None = None,
    ) -> dict[str, Any]:
        headers = {"apikey": self._apikey}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        elif _parece_jwt(self._apikey):
            headers["Authorization"] = f"Bearer {self._apikey}"
        try:
            resposta = self._http.request(
                metodo, caminho, json=json, params=params, headers=headers
            )
        except httpx.HTTPError as exc:
            logger.error("GoTrue indisponível em %s %s: %s", metodo, caminho, type(exc).__name__)
            raise GoTrueIndisponivel(str(type(exc).__name__)) from exc

        if resposta.status_code >= 500:
            logger.error("GoTrue respondeu %s em %s %s", resposta.status_code, metodo, caminho)
            raise GoTrueIndisponivel(f"GoTrue {resposta.status_code}")
        try:
            corpo = resposta.json() if resposta.content else {}
        except ValueError:
            corpo = {}
        if not isinstance(corpo, dict):
            corpo = {}
        if resposta.status_code >= 400:
            # formato novo: {"code", "error_code", "msg"}; antigo: {"error", "error_description"}
            codigo = corpo.get("error_code") or corpo.get("error")
            raise GoTrueErro(resposta.status_code, codigo, corpo)
        return corpo

    # --- rotas de /api/auth (chave publishable) ------------------------------------

    def entrar(self, email: str, senha: str) -> SessaoGoTrue:
        dados = self._pedir(
            "POST",
            "/token",
            params={"grant_type": "password"},
            json={"email": email, "password": senha},
        )
        return _sessao(dados)

    def renovar(self, refresh_token: str) -> SessaoGoTrue:
        dados = self._pedir(
            "POST",
            "/token",
            params={"grant_type": "refresh_token"},
            json={"refresh_token": refresh_token},
        )
        return _sessao(dados)

    def sair(self, access_token: str) -> None:
        # scope=local: encerra só esta sessão (os outros aparelhos do usuário continuam)
        self._pedir("POST", "/logout", params={"scope": "local"}, access_token=access_token)

    def recuperar(self, email: str, redirect_to: str) -> None:
        self._pedir("POST", "/recover", params={"redirect_to": redirect_to}, json={"email": email})

    def verificar(self, token_hash: str, tipo: TipoLink) -> SessaoGoTrue:
        dados = self._pedir("POST", "/verify", json={"type": tipo, "token_hash": token_hash})
        return _sessao(dados)

    def definir_senha(self, access_token: str, senha: str) -> None:
        self._pedir("PUT", "/user", json={"password": senha}, access_token=access_token)

    # --- cadastro pela LANN (chave secreta; só o CLI) -----------------------------------

    def convidar(self, email: str, redirect_to: str, dados: dict[str, Any]) -> UUID:
        corpo = self._pedir(
            "POST",
            "/invite",
            params={"redirect_to": redirect_to},
            json={"email": email, "data": dados},
        )
        try:
            return UUID(str(corpo["id"]))
        except (KeyError, ValueError) as exc:
            raise GoTrueIndisponivel("resposta de convite inesperada do GoTrue") from exc

    def apagar_usuario(self, id_autenticacao: UUID) -> None:
        self._pedir("DELETE", f"/admin/users/{id_autenticacao}")

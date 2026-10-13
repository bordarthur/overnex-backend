"""Autenticação: valida o access token do Microsoft Entra ID."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Any
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Ambiente, Settings, get_settings

logger = logging.getLogger(__name__)

PREFIXO_FAKE = "fake:"

_bearer = HTTPBearer(auto_error=False)

# Recebe o token e devolve a chave pública que o assinou.
ObterChave = Callable[[str], Any]


@dataclass(frozen=True)
class UsuarioToken:
    object_id: UUID
    email: str | None = None
    nome: str | None = None


def _nao_autenticado() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Não autenticado.",
        headers={"WWW-Authenticate": "Bearer"},
    )


@lru_cache
def _jwks_client(tenant_id: str) -> jwt.PyJWKClient:
    url = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=3600, timeout=5)


def _chave_do_tenant(tenant_id: str) -> ObterChave:
    def obter(token: str) -> Any:
        return _jwks_client(tenant_id).get_signing_key_from_jwt(token).key

    return obter


def validar_token_entra(token: str, settings: Settings, obter_chave: ObterChave) -> UsuarioToken:
    """Valida assinatura (JWKS do tenant), iss, aud, exp e tid; devolve o usuário do token."""
    tenant = settings.entra_tenant_id
    try:
        chave = obter_chave(token)
        claims: dict[str, Any] = jwt.decode(
            token,
            key=chave,
            algorithms=["RS256"],
            audience=settings.entra_audience,
            # v2.0 e v1.0, conforme accessTokenAcceptedVersion do app registration
            issuer=[
                f"https://login.microsoftonline.com/{tenant}/v2.0",
                f"https://sts.windows.net/{tenant}/",
            ],
            options={"require": ["exp", "iat", "iss", "aud", "oid", "tid"]},
            leeway=60,
        )
    except jwt.PyJWKClientConnectionError as exc:
        logger.error("JWKS do Entra indisponível: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Autenticação indisponível."
        ) from exc
    except (jwt.InvalidTokenError, jwt.PyJWKClientError) as exc:
        logger.info("Token recusado: %s", exc)
        raise _nao_autenticado() from exc

    if claims.get("tid") != tenant:
        raise _nao_autenticado()
    try:
        object_id = UUID(str(claims["oid"]))
    except ValueError as exc:
        raise _nao_autenticado() from exc

    email = claims.get("preferred_username") or claims.get("email") or claims.get("upn")
    return UsuarioToken(object_id=object_id, email=email, nome=claims.get("name"))


def _usuario_fake(token: str) -> UsuarioToken:
    try:
        return UsuarioToken(object_id=UUID(token.removeprefix(PREFIXO_FAKE)), nome="Usuário fake")
    except ValueError as exc:
        raise _nao_autenticado() from exc


def get_usuario(
    credenciais: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> UsuarioToken:
    """Dependência: usuário autenticado ou 401.

    Com AMBIENTE=dev e AUTH_FAKE=1, aceita também `Authorization: Bearer fake:<object_id>`.
    """
    if credenciais is None or not credenciais.credentials:
        raise _nao_autenticado()
    token = credenciais.credentials

    if token.startswith(PREFIXO_FAKE):
        # Settings já impede AUTH_FAKE fora de dev; a checagem dupla é proposital.
        if settings.auth_fake and settings.ambiente is Ambiente.DEV:
            return _usuario_fake(token)
        raise _nao_autenticado()

    if not settings.entra_configurado:
        raise _nao_autenticado()
    return validar_token_entra(token, settings, _chave_do_tenant(settings.entra_tenant_id))

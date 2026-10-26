"""Autenticação: valida o access token do Supabase Auth ou do Microsoft Entra ID.

O provedor vem de AUTH_PROVEDOR (padrão: supabase). O do Entra fica desligado por
configuração até a decisão sobre ele (ver TODO.md).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Any
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Ambiente, ProvedorAuth, Settings, get_settings

logger = logging.getLogger(__name__)

PREFIXO_FAKE = "fake:"

# O projeto assina com chave assimétrica ES256 (JWKS); algoritmo fixo, nunca "none".
ALGORITMOS_SUPABASE = ["ES256"]
AUDIENCIA_SUPABASE = "authenticated"

_bearer = HTTPBearer(auto_error=False)

# Recebe o token e devolve a chave pública que o assinou.
ObterChave = Callable[[str], Any]


@dataclass(frozen=True)
class UsuarioToken:
    # `sub` do Supabase (ou `oid` do Entra); é o seg.usuario.id_autenticacao.
    id_autenticacao: UUID
    email: str | None = None


def _nao_autenticado() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Não autenticado.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _indisponivel() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Autenticação indisponível."
    )


@lru_cache
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=3600, timeout=5)


def _chave_do_jwks(url: str) -> ObterChave:
    def obter(token: str) -> Any:
        return _jwks_client(url).get_signing_key_from_jwt(token).key

    return obter


def _chave_do_tenant(tenant_id: str) -> ObterChave:
    return _chave_do_jwks(f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys")


def _chave_do_supabase(supabase_url: str) -> ObterChave:
    return _chave_do_jwks(f"{supabase_url}/auth/v1/.well-known/jwks.json")


def validar_token_supabase(token: str, settings: Settings, obter_chave: ObterChave) -> UsuarioToken:
    """Valida assinatura (JWKS do projeto), iss, aud, exp, iat, sub e role."""
    try:
        chave = obter_chave(token)
        claims: dict[str, Any] = jwt.decode(
            token,
            key=chave,
            algorithms=ALGORITMOS_SUPABASE,
            audience=AUDIENCIA_SUPABASE,
            issuer=f"{settings.supabase_url}/auth/v1",
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            leeway=60,
        )
    except jwt.PyJWKClientConnectionError as exc:
        logger.error("JWKS do Supabase indisponível: %s", exc)
        raise _indisponivel() from exc
    except (jwt.InvalidTokenError, jwt.PyJWKClientError) as exc:
        logger.info("Token recusado: %s", exc)
        raise _nao_autenticado() from exc

    # role = authenticated; login anônimo (desligado no projeto) também seria recusado
    if claims.get("role") != "authenticated" or claims.get("is_anonymous") is True:
        raise _nao_autenticado()
    try:
        id_autenticacao = UUID(str(claims["sub"]))
    except ValueError as exc:
        raise _nao_autenticado() from exc
    return UsuarioToken(id_autenticacao=id_autenticacao, email=claims.get("email"))


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
        raise _indisponivel() from exc
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
    return UsuarioToken(id_autenticacao=object_id, email=email)


def _usuario_fake(token: str) -> UsuarioToken:
    try:
        return UsuarioToken(id_autenticacao=UUID(token.removeprefix(PREFIXO_FAKE)))
    except ValueError as exc:
        raise _nao_autenticado() from exc


def get_usuario(
    credenciais: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> UsuarioToken:
    """Dependência: usuário autenticado ou 401.

    Com AMBIENTE=dev e AUTH_FAKE=1, aceita também `Authorization: Bearer fake:<id_autenticacao>`.
    """
    if credenciais is None or not credenciais.credentials:
        raise _nao_autenticado()
    token = credenciais.credentials

    if token.startswith(PREFIXO_FAKE):
        # Settings já impede AUTH_FAKE fora de dev; a checagem dupla é proposital.
        if settings.auth_fake and settings.ambiente is Ambiente.DEV:
            return _usuario_fake(token)
        raise _nao_autenticado()

    if settings.auth_provedor is ProvedorAuth.SUPABASE:
        if not settings.supabase_configurado:
            raise _nao_autenticado()
        return validar_token_supabase(token, settings, _chave_do_supabase(settings.supabase_url))

    if not settings.entra_configurado:
        raise _nao_autenticado()
    return validar_token_entra(token, settings, _chave_do_tenant(settings.entra_tenant_id))

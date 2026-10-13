"""Autenticação e configuração — sem banco."""

import time
import uuid
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import ValidationError

from app.core.config import Ambiente, Settings, get_settings
from app.main import create_app
from app.seguranca.auth import get_usuario, validar_token_entra

TENANT = "11111111-2222-4333-8444-555555555555"
AUDIENCE = "api://overnex"
OID = uuid.UUID("0d000000-0000-4000-8000-00000000000a")

_chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_outra_chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _settings(**kwargs: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": "postgresql://x@localhost/x",
        "entra_tenant_id": TENANT,
        "entra_audience": AUDIENCE,
        "ambiente": Ambiente.PROD,
        "auth_fake": False,
    }
    return Settings(**(base | kwargs))


def _token(chave: rsa.RSAPrivateKey = _chave, **claims: Any) -> str:
    agora = int(time.time())
    payload: dict[str, Any] = {
        "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "aud": AUDIENCE,
        "tid": TENANT,
        "oid": str(OID),
        "iat": agora,
        "nbf": agora,
        "exp": agora + 600,
        "preferred_username": "ana@alfa.example",
    }
    payload |= claims
    return jwt.encode({k: v for k, v in payload.items() if v is not None}, chave, algorithm="RS256")


def _obter_chave(_token: str) -> Any:
    return _chave.public_key()


def _cred(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


# --- Settings -----------------------------------------------------------------


@pytest.mark.parametrize("ambiente", [Ambiente.PROD, Ambiente.HOMOLOG])
def test_auth_fake_fora_de_dev_recusa_iniciar(ambiente: Ambiente) -> None:
    with pytest.raises(ValidationError, match="AUTH_FAKE"):
        _settings(ambiente=ambiente, auth_fake=True)


def test_sem_entra_e_sem_fake_recusa_iniciar() -> None:
    with pytest.raises(ValidationError, match="ENTRA_TENANT_ID"):
        _settings(entra_tenant_id="", auth_fake=False)


def test_create_app_recusa_auth_fake_em_prod(monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.setenv("AMBIENTE", "prod")
    monkeypatch.setenv("AUTH_FAKE", "1")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError, match="AUTH_FAKE"):
            create_app()
    finally:
        get_settings.cache_clear()


# --- Token do Entra -------------------------------------------------------------


def test_token_valido() -> None:
    usuario = validar_token_entra(_token(), _settings(), _obter_chave)
    assert usuario.object_id == OID
    assert usuario.email == "ana@alfa.example"


def test_token_v1_aceito() -> None:
    token = _token(iss=f"https://sts.windows.net/{TENANT}/")
    assert validar_token_entra(token, _settings(), _obter_chave).object_id == OID


@pytest.mark.parametrize(
    "claims",
    [
        {"exp": int(time.time()) - 3600},  # expirado (além do leeway)
        {"aud": "api://outra"},
        {"iss": "https://login.microsoftonline.com/outro-tenant/v2.0"},
        {"tid": "outro-tenant"},
        {"oid": None},
        {"oid": "nao-e-uuid"},
    ],
)
def test_token_recusado(claims: dict[str, Any]) -> None:
    with pytest.raises(HTTPException) as exc:
        validar_token_entra(_token(**claims), _settings(), _obter_chave)
    assert exc.value.status_code == 401


def test_assinatura_de_outra_chave_recusada() -> None:
    with pytest.raises(HTTPException) as exc:
        validar_token_entra(_token(chave=_outra_chave), _settings(), _obter_chave)
    assert exc.value.status_code == 401


def test_algoritmo_none_recusado() -> None:
    token = jwt.encode({"oid": str(OID), "aud": AUDIENCE}, key="", algorithm="none")
    with pytest.raises(HTTPException) as exc:
        validar_token_entra(token, _settings(), _obter_chave)
    assert exc.value.status_code == 401


# --- get_usuario ---------------------------------------------------------------


def test_sem_credencial_401() -> None:
    with pytest.raises(HTTPException) as exc:
        get_usuario(None, _settings())
    assert exc.value.status_code == 401


def test_fake_aceito_em_dev() -> None:
    settings = _settings(ambiente=Ambiente.DEV, auth_fake=True)
    assert get_usuario(_cred(f"fake:{OID}"), settings).object_id == OID


def test_fake_recusado_sem_auth_fake() -> None:
    with pytest.raises(HTTPException) as exc:
        get_usuario(_cred(f"fake:{OID}"), _settings(ambiente=Ambiente.DEV))
    assert exc.value.status_code == 401


def test_fake_malformado_401() -> None:
    settings = _settings(ambiente=Ambiente.DEV, auth_fake=True)
    with pytest.raises(HTTPException) as exc:
        get_usuario(_cred("fake:qualquer"), settings)
    assert exc.value.status_code == 401

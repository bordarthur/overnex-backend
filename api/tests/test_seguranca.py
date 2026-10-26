"""Autenticação e configuração — sem banco."""

import time
import uuid
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import SecretStr, ValidationError

from app.core.config import Ambiente, ProvedorAuth, Settings, get_settings
from app.main import create_app
from app.seguranca import auth
from app.seguranca.auth import get_usuario, validar_token_entra, validar_token_supabase

TENANT = "11111111-2222-4333-8444-555555555555"
AUDIENCE = "api://overnex"
OID = uuid.UUID("0d000000-0000-4000-8000-00000000000a")
SUPABASE_URL = "https://projeto-teste.supabase.co"
SUB = uuid.UUID("5b000000-0000-4000-8000-00000000000a")

_chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_outra_chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
# o projeto assina com ES256 (P-256)
_chave_ec = ec.generate_private_key(ec.SECP256R1())
_outra_chave_ec = ec.generate_private_key(ec.SECP256R1())


def _settings(**kwargs: Any) -> Settings:
    """Settings com o Entra ligado (testes do token do Entra)."""
    base: dict[str, Any] = {
        "database_url": "postgresql://x@localhost/x",
        "auth_provedor": ProvedorAuth.ENTRA,
        "entra_tenant_id": TENANT,
        "entra_audience": AUDIENCE,
        "ambiente": Ambiente.PROD,
        "auth_fake": False,
    }
    return Settings(**(base | kwargs))


def _settings_supabase(**kwargs: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": "postgresql://x@localhost/x",
        "auth_provedor": ProvedorAuth.SUPABASE,
        "supabase_url": SUPABASE_URL,
        "supabase_anon_key": "sb_publishable_teste",
        "front_url": "https://overnex.teste",
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


def _token_supabase(chave: Any = _chave_ec, algoritmo: str = "ES256", **claims: Any) -> str:
    agora = int(time.time())
    payload: dict[str, Any] = {
        "iss": f"{SUPABASE_URL}/auth/v1",
        "aud": "authenticated",
        "sub": str(SUB),
        "role": "authenticated",
        "email": "ana@alfa.example",
        "iat": agora,
        "exp": agora + 3600,
        "is_anonymous": False,
        "session_id": "11111111-0000-4000-8000-000000000001",
    }
    payload |= claims
    return jwt.encode(
        {k: v for k, v in payload.items() if v is not None},
        chave,
        algorithm=algoritmo,
        headers={"kid": "chave-teste"},
    )


def _obter_chave(_token: str) -> Any:
    return _chave.public_key()


def _obter_chave_ec(_token: str) -> Any:
    return _chave_ec.public_key()


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


def test_provedor_padrao_e_supabase() -> None:
    settings = Settings(
        database_url=SecretStr("postgresql://x@localhost/x"),
        supabase_url=SUPABASE_URL + "/",
        supabase_anon_key=SecretStr("sb_publishable_teste"),
        front_url="https://overnex.teste/",
    )
    assert settings.auth_provedor is ProvedorAuth.SUPABASE
    assert settings.supabase_url == SUPABASE_URL  # sem a barra final
    assert settings.front_url == "https://overnex.teste"
    assert settings.cookie_seguro is True


@pytest.mark.parametrize("faltando", ["supabase_url", "supabase_anon_key", "front_url"])
@pytest.mark.parametrize("auth_fake", [False, True])
def test_supabase_sem_configuracao_recusa_iniciar(faltando: str, auth_fake: bool) -> None:
    with pytest.raises(ValidationError, match="SUPABASE_URL"):
        _settings_supabase(ambiente=Ambiente.DEV, auth_fake=auth_fake, **{faltando: ""})


@pytest.mark.parametrize("ambiente", [Ambiente.PROD, Ambiente.HOMOLOG])
def test_cookie_sem_secure_fora_de_dev_recusa_iniciar(ambiente: Ambiente) -> None:
    with pytest.raises(ValidationError, match="COOKIE_SEGURO"):
        _settings_supabase(ambiente=ambiente, cookie_seguro=False)


def test_cookie_sem_secure_em_dev_aceito() -> None:
    assert _settings_supabase(ambiente=Ambiente.DEV, cookie_seguro=False).cookie_seguro is False


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
    assert usuario.id_autenticacao == OID
    assert usuario.email == "ana@alfa.example"


def test_token_v1_aceito() -> None:
    token = _token(iss=f"https://sts.windows.net/{TENANT}/")
    assert validar_token_entra(token, _settings(), _obter_chave).id_autenticacao == OID


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


# --- Token do Supabase ----------------------------------------------------------


def test_supabase_token_valido() -> None:
    usuario = validar_token_supabase(_token_supabase(), _settings_supabase(), _obter_chave_ec)
    assert usuario.id_autenticacao == SUB
    assert usuario.email == "ana@alfa.example"


def test_supabase_expirado_dentro_do_leeway_aceito() -> None:
    token = _token_supabase(exp=int(time.time()) - 30)
    usuario = validar_token_supabase(token, _settings_supabase(), _obter_chave_ec)
    assert usuario.id_autenticacao == SUB


@pytest.mark.parametrize(
    "claims",
    [
        {"exp": int(time.time()) - 3600},  # expirado (além do leeway)
        {"aud": "anon"},
        {"aud": "outra"},
        {"iss": "https://outro-projeto.supabase.co/auth/v1"},
        {"iss": SUPABASE_URL},  # sem /auth/v1
        {"role": "anon"},
        {"role": "service_role"},
        {"role": None},
        {"is_anonymous": True},
        {"sub": None},
        {"sub": "nao-e-uuid"},
        {"iat": None},
        {"exp": None},
    ],
)
def test_supabase_token_recusado(claims: dict[str, Any]) -> None:
    with pytest.raises(HTTPException) as exc:
        validar_token_supabase(_token_supabase(**claims), _settings_supabase(), _obter_chave_ec)
    assert exc.value.status_code == 401
    assert exc.value.detail == "Não autenticado."


def test_supabase_assinatura_de_outra_chave_recusada() -> None:
    token = _token_supabase(chave=_outra_chave_ec)
    with pytest.raises(HTTPException) as exc:
        validar_token_supabase(token, _settings_supabase(), _obter_chave_ec)
    assert exc.value.status_code == 401


def test_supabase_algoritmo_none_recusado() -> None:
    claims = {"sub": str(SUB), "aud": "authenticated", "role": "authenticated"}
    token = jwt.encode(claims, key="", algorithm="none")
    with pytest.raises(HTTPException) as exc:
        validar_token_supabase(token, _settings_supabase(), _obter_chave_ec)
    assert exc.value.status_code == 401


@pytest.mark.parametrize(
    ("chave", "algoritmo"),
    [
        (_chave, "RS256"),  # algoritmo fora da lista, mesmo com claims corretos
        ("segredo-legado-hs256-com-32-bytes!!", "HS256"),  # segredo legado não vale
    ],
)
def test_supabase_outro_algoritmo_recusado(chave: Any, algoritmo: str) -> None:
    token = _token_supabase(chave=chave, algoritmo=algoritmo)
    with pytest.raises(HTTPException) as exc:
        validar_token_supabase(token, _settings_supabase(), _obter_chave)
    assert exc.value.status_code == 401


def test_supabase_jwks_fora_do_ar_503() -> None:
    def jwks_caiu(_token: str) -> Any:
        raise jwt.PyJWKClientConnectionError("timeout")

    with pytest.raises(HTTPException) as exc:
        validar_token_supabase(_token_supabase(), _settings_supabase(), jwks_caiu)
    assert exc.value.status_code == 503
    assert exc.value.detail == "Autenticação indisponível."


def test_supabase_kid_desconhecido_401() -> None:
    def sem_chave(_token: str) -> Any:
        raise jwt.PyJWKClientError("Unable to find a signing key that matches")

    with pytest.raises(HTTPException) as exc:
        validar_token_supabase(_token_supabase(), _settings_supabase(), sem_chave)
    assert exc.value.status_code == 401


# --- get_usuario ---------------------------------------------------------------


def test_sem_credencial_401() -> None:
    with pytest.raises(HTTPException) as exc:
        get_usuario(None, _settings())
    assert exc.value.status_code == 401


def test_get_usuario_usa_o_supabase_por_padrao(monkeypatch: pytest.MonkeyPatch) -> None:
    urls: list[str] = []

    def chave_do_supabase(url: str) -> auth.ObterChave:
        urls.append(url)
        return _obter_chave_ec

    monkeypatch.setattr(auth, "_chave_do_supabase", chave_do_supabase)
    usuario = get_usuario(_cred(_token_supabase()), _settings_supabase())
    assert usuario.id_autenticacao == SUB
    assert urls == [SUPABASE_URL]


def test_get_usuario_supabase_recusa_token_do_entra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "_chave_do_supabase", lambda _url: _obter_chave_ec)
    with pytest.raises(HTTPException) as exc:
        get_usuario(_cred(_token()), _settings_supabase())
    assert exc.value.status_code == 401


def test_get_usuario_entra_quando_configurado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "_chave_do_tenant", lambda _tenant: _obter_chave)
    assert get_usuario(_cred(_token()), _settings()).id_autenticacao == OID
    with pytest.raises(HTTPException) as exc:
        get_usuario(_cred(_token_supabase()), _settings())
    assert exc.value.status_code == 401


def test_fake_aceito_em_dev() -> None:
    settings = _settings(ambiente=Ambiente.DEV, auth_fake=True)
    assert get_usuario(_cred(f"fake:{OID}"), settings).id_autenticacao == OID


def test_fake_aceito_em_dev_com_supabase() -> None:
    settings = _settings_supabase(ambiente=Ambiente.DEV, auth_fake=True)
    assert get_usuario(_cred(f"fake:{OID}"), settings).id_autenticacao == OID


def test_fake_recusado_sem_auth_fake() -> None:
    with pytest.raises(HTTPException) as exc:
        get_usuario(_cred(f"fake:{OID}"), _settings(ambiente=Ambiente.DEV))
    assert exc.value.status_code == 401


def test_fake_malformado_401() -> None:
    settings = _settings(ambiente=Ambiente.DEV, auth_fake=True)
    with pytest.raises(HTTPException) as exc:
        get_usuario(_cred("fake:qualquer"), settings)
    assert exc.value.status_code == 401


def test_erro_de_configuracao_nao_mostra_valores() -> None:
    with pytest.raises(ValidationError) as exc:
        _settings_supabase(
            ambiente=Ambiente.DEV,
            database_url="postgresql://api:senha-do-banco-123@host/db",
            supabase_anon_key="",
            supabase_url="https://projeto-secreto.supabase.co",
        )
    assert "SUPABASE_URL" in str(exc.value)
    for valor in ("senha-do-banco-123", "projeto-secreto"):
        assert valor not in str(exc.value)

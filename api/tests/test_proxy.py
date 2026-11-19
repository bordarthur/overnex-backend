"""Segredo do proxy (X-Proxy-Segredo, app/core/proxy.py) — sem banco."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.core.config import Ambiente, Settings
from app.core.db import get_pool
from app.main import create_app

SEGREDO = "segredo-do-proxy-com-mais-de-32-caracteres"
NEGADO = {"detail": "Acesso negado."}
JSON = {"Content-Type": "application/json"}
# rota que não precisa de banco nem do Supabase: sem access token, só apaga o cookie (204)
SAIR = "/api/auth/sair"


def _settings(**kwargs: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": SecretStr("postgresql://x@localhost/x"),
        "supabase_url": "https://projeto-teste.supabase.co",
        "supabase_anon_key": SecretStr("sb_publishable_teste"),
        "front_url": "https://overnex.teste",
        "ambiente": Ambiente.DEV,
        "auth_fake": False,
    }
    return Settings(**(base | kwargs))


class _ConexaoFalsa:
    def execute(self, _sql: str) -> None:
        return None


class _PoolFalso:
    @contextmanager
    def connection(self, timeout: float | None = None) -> Iterator[_ConexaoFalsa]:
        yield _ConexaoFalsa()


def _cliente(segredo: str) -> TestClient:
    app = create_app(_settings(proxy_segredo=SecretStr(segredo)))
    # /health consulta o banco: aqui, um pool que sempre responde
    app.dependency_overrides[get_pool] = _PoolFalso
    # sem o "with": o lifespan (que abre o pool de verdade) não roda
    return TestClient(app)


@pytest.fixture
def cliente() -> TestClient:
    return _cliente(SEGREDO)


# --- configuração -------------------------------------------------------------------


def test_prod_sem_segredo_recusa_iniciar() -> None:
    with pytest.raises(ValidationError, match="PROXY_SEGREDO"):
        _settings(ambiente=Ambiente.PROD)


def test_prod_com_segredo_sobe() -> None:
    settings = _settings(ambiente=Ambiente.PROD, proxy_segredo=SecretStr(SEGREDO))
    assert settings.proxy_segredo.get_secret_value() == SEGREDO


@pytest.mark.parametrize("ambiente", [Ambiente.DEV, Ambiente.HOMOLOG])
def test_fora_de_prod_o_segredo_e_opcional(ambiente: Ambiente) -> None:
    assert _settings(ambiente=ambiente).proxy_segredo.get_secret_value() == ""


def test_segredo_curto_recusa_iniciar() -> None:
    with pytest.raises(ValidationError, match="PROXY_SEGREDO") as erro:
        _settings(proxy_segredo=SecretStr("curto"))
    # a mensagem não mostra o valor
    assert "curto" not in str(erro.value)


def test_create_app_recusa_prod_sem_segredo(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("AMBIENTE", "prod")
    monkeypatch.setenv("AUTH_FAKE", "0")
    monkeypatch.delenv("PROXY_SEGREDO", raising=False)
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError, match="PROXY_SEGREDO"):
            create_app()
    finally:
        get_settings.cache_clear()


# --- checagem -----------------------------------------------------------------------


def test_sem_cabecalho_403(cliente: TestClient) -> None:
    r = cliente.post(SAIR, json={}, headers=JSON)
    assert r.status_code == 403
    assert r.json() == NEGADO
    assert r.headers["cache-control"] == "no-store"


def test_segredo_errado_403(cliente: TestClient) -> None:
    r = cliente.post(SAIR, json={}, headers=JSON | {"X-Proxy-Segredo": SEGREDO + "x"})
    assert r.status_code == 403
    assert r.json() == NEGADO


def test_segredo_vazio_403(cliente: TestClient) -> None:
    r = cliente.post(SAIR, json={}, headers=JSON | {"X-Proxy-Segredo": ""})
    assert r.status_code == 403
    assert r.json() == NEGADO


def test_cabecalho_repetido_403(cliente: TestClient) -> None:
    headers = [
        ("Content-Type", "application/json"),
        ("X-Proxy-Segredo", SEGREDO),
        ("X-Proxy-Segredo", SEGREDO),
    ]
    r = cliente.post(SAIR, content=b"{}", headers=headers)
    assert r.status_code == 403
    assert r.json() == NEGADO


def test_rota_que_nao_existe_tambem_403(cliente: TestClient) -> None:
    # sem o segredo, nem o 404 aparece: quem chama direto não descobre as rotas
    r = cliente.get("/api/nao-existe")
    assert r.status_code == 403
    assert r.json() == NEGADO


def test_segredo_certo_passa(cliente: TestClient) -> None:
    r = cliente.post(SAIR, json={}, headers=JSON | {"X-Proxy-Segredo": SEGREDO})
    assert r.status_code == 204


def test_nome_do_cabecalho_sem_diferenca_de_maiusculas(cliente: TestClient) -> None:
    r = cliente.post(SAIR, json={}, headers=JSON | {"x-proxy-segredo": SEGREDO})
    assert r.status_code == 204


def test_health_livre_sem_segredo(cliente: TestClient) -> None:
    r = cliente.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_sem_segredo_configurado_a_checagem_fica_desligada() -> None:
    r = _cliente("").post(SAIR, json={}, headers=JSON)
    assert r.status_code == 204

"""GET /api/sessao com perfil, clientes e cliente escolhido — sem banco.

A sessão é falsa: a checagem de acesso lê os fatos de uma conexão que só responde a
seg.contexto_acesso, e as consultas da moldura (uma ida ao banco) são trocadas por linhas.
"""

from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.core.db import Sessao, get_pool, get_sessao
from app.main import create_app
from app.seguranca.auth import UsuarioToken
from app.sessao import router as sessao_router
from app.telas import preparo

USUARIO = UUID("d1000000-0000-4000-8000-00000000000a")
CLIENTE_A = UUID("c1000000-0000-4000-8000-00000000000a")
CLIENTE_B = UUID("c1000000-0000-4000-8000-00000000000b")
EMPRESA_A1 = UUID("e1000000-0000-4000-8000-0000000000a1")

LINHA_A = {
    "id": CLIENTE_A,
    "nome": "Padrão LANN Alfa",
    "logo_url": None,
    "slug": "padrao-lann-alfa",
}
LINHA_B = {
    "id": CLIENTE_B,
    "nome": "Padrão LANN Beta",
    "logo_url": None,
    "slug": "padrao-lann-beta",
}
ALFA = {"nome": "Padrão LANN Alfa", "sigla": "PA", "logoUrl": None, "slug": "padrao-lann-alfa"}
BETA = {"nome": "Padrão LANN Beta", "sigla": "PB", "logoUrl": None, "slug": "padrao-lann-beta"}


class Resultado:
    def __init__(self, linha: dict[str, Any]) -> None:
        self.linha = linha

    def fetchone(self) -> dict[str, Any]:
        return self.linha


class ConexaoFalsa:
    """Só responde à consulta dos fatos de acesso; guarda o slug que a rota mandou."""

    def __init__(self, estado: dict[str, Any]) -> None:
        self.estado = estado

    def execute(self, consulta: str, params: Any = None) -> Resultado:
        assert "seg.contexto_acesso" in consulta
        self.estado["slug_pedido"] = params[2]
        return Resultado(self.estado["fatos"])


def _fatos(**mudancas: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "usuario_id": USUARIO,
        "ativo": True,
        "vinculos": [{"cliente_id": str(CLIENTE_A), "papel": "gestor"}],
        "perfis_tela": {},
        "empresas": [{"cliente_id": str(CLIENTE_A), "empresa_id": str(EMPRESA_A1)}],
        "perfil": "cliente_principal",
        "cliente_id": CLIENTE_A,
    }
    return base | mudancas


@pytest.fixture
def estado(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    e: dict[str, Any] = {
        "fatos": _fatos(),
        "clientes": [LINHA_A],
        "telas": ["COM-01", "VG-01"],
        "agendadas": [],
        "consultas": {},
    }

    def em_uma_ida(conn: Any, consultas: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        e["consultas"] = consultas
        linhas: dict[str, list[dict[str, Any]]] = {
            "usuario": [{"nome": "Ana Alfa", "email": "ana@alfa.example"}],
            "clientes": e["clientes"],
            "empresas": [{"id": EMPRESA_A1, "nome": "Alfa Indústria"}],
            "telas": [{"codigo_tela": t} for t in e["telas"]],
            "carga": [
                {"data_snapshot": date(2026, 10, 12), "fim": datetime(2026, 10, 12, 12, tzinfo=UTC)}
            ],
            "meses": [{"mes": date(2026, 9, 1)}],
        }
        return {nome: linhas[nome] for nome in consultas}

    def agendar(pool: Any, usuario: Any, telas: Any, settings: Any, cliente: Any = None) -> None:
        e["agendadas"].append((list(telas), cliente))

    monkeypatch.setattr(sessao_router, "em_uma_ida", em_uma_ida)
    monkeypatch.setattr(sessao_router, "existe", lambda conn, nome: True)
    monkeypatch.setattr(preparo, "agendar", agendar)
    return e


@pytest.fixture
def client(estado: dict[str, Any]) -> Iterator[TestClient]:
    app = create_app()

    def sessao() -> Sessao:
        s = Sessao(
            conn=ConexaoFalsa(estado),  # type: ignore[arg-type]
            usuario=UsuarioToken(id_autenticacao=UUID(int=1)),
        )
        estado.setdefault("sessoes", []).append(s)
        return s

    app.dependency_overrides[get_sessao] = sessao
    app.dependency_overrides[get_pool] = lambda: None
    return TestClient(app)


def test_cliente_de_um_vinculo_recebe_o_cliente_sem_pedir(
    client: TestClient, estado: dict[str, Any]
) -> None:
    r = client.get("/api/sessao")

    assert r.status_code == 200
    assert r.json() == {
        "usuario": {"nome": "Ana Alfa", "email": "ana@alfa.example"},
        "perfil": {"id": "cliente_principal", "rotulo": "Cliente principal"},
        "gestao": False,
        "clientes": [ALFA],
        "cliente": ALFA,
        "papel": "gestor",
        "empresas": [{"id": str(EMPRESA_A1), "nome": "Alfa Indústria"}],
        "telas": ["COM-01", "VG-01"],
        "periodos": [
            {"id": "2026-10", "rotulo": "Outubro de 2026", "dadosAte": "2026-10-12"},
            {"id": "2026-09", "rotulo": "Setembro de 2026", "dadosAte": "2026-09-30"},
        ],
        "aviso": None,
    }
    assert estado["slug_pedido"] is None
    # telas do perfil da pessoa (não as do admin), e a pré-montagem com o slug do cliente
    assert estado["consultas"]["telas"] == (
        sessao_router.SQL_TELAS,
        (USUARIO, CLIENTE_A),
    )
    assert estado["agendadas"] == [(["COM-01", "VG-01"], "padrao-lann-alfa")]


def test_admin_sem_cliente_recebe_a_lista_e_a_gestao(
    client: TestClient, estado: dict[str, Any]
) -> None:
    estado["fatos"] = _fatos(vinculos=[], empresas=[], perfil="admin", cliente_id=None)
    estado["clientes"] = [LINHA_A, LINHA_B]

    r = client.get("/api/sessao")

    assert r.status_code == 200
    assert r.json() == {
        "usuario": {"nome": "Ana Alfa", "email": "ana@alfa.example"},
        "perfil": {"id": "admin", "rotulo": "Admin BORD"},
        "gestao": True,
        "clientes": [ALFA, BETA],
        "cliente": None,
        "papel": None,
        "empresas": [],
        "telas": [],
        "periodos": [],
        "aviso": None,
    }
    # sem cliente, só o usuário e a lista de clientes vão ao banco, e nada é pré-montado
    assert set(estado["consultas"]) == {"usuario", "clientes"}
    assert estado["agendadas"] == []


def test_admin_escolhe_o_cliente_pelo_slug(client: TestClient, estado: dict[str, Any]) -> None:
    estado["fatos"] = _fatos(vinculos=[], perfil="admin", cliente_id=CLIENTE_B)
    estado["clientes"] = [LINHA_A, LINHA_B]
    estado["telas"] = ["PES-01", "VG-01"]

    r = client.get("/api/sessao", params={"cliente": "padrao-lann-beta"})

    assert r.status_code == 200
    corpo = r.json()
    assert estado["slug_pedido"] == "padrao-lann-beta"
    assert (corpo["cliente"], corpo["papel"], corpo["gestao"]) == (BETA, "admin", True)
    assert corpo["clientes"] == [ALFA, BETA]
    assert corpo["telas"] == ["PES-01", "VG-01"]
    # as telas do admin são as ligadas no cliente, sem passar por perfil
    assert estado["consultas"]["telas"] == (sessao_router.SQL_TELAS_ADMIN, (CLIENTE_B,))
    assert estado["agendadas"] == [(["PES-01", "VG-01"], "padrao-lann-beta")]


def test_consultor_com_dois_clientes_e_sem_slug_escolhe_depois(
    client: TestClient, estado: dict[str, Any]
) -> None:
    estado["fatos"] = _fatos(
        vinculos=[
            {"cliente_id": str(CLIENTE_A), "papel": "gestor"},
            {"cliente_id": str(CLIENTE_B), "papel": "gestor"},
        ],
        perfil="consultor",
        cliente_id=None,
    )
    estado["clientes"] = [LINHA_A, LINHA_B]

    corpo = client.get("/api/sessao").json()

    assert (corpo["gestao"], corpo["cliente"], corpo["telas"]) == (False, None, [])
    assert corpo["clientes"] == [ALFA, BETA]
    assert corpo["aviso"] is None


def test_ativo_sem_empresa_recebe_o_aviso(client: TestClient, estado: dict[str, Any]) -> None:
    estado["fatos"] = _fatos(vinculos=[], empresas=[], perfil="cliente_usuario", cliente_id=None)
    estado["clientes"] = []

    r = client.get("/api/sessao")

    assert r.status_code == 200
    corpo = r.json()
    assert (corpo["clientes"], corpo["cliente"]) == ([], None)
    assert (
        corpo["aviso"] == "Sua conta ainda não tem empresa. Fale com a BORD para liberar o acesso."
    )


def test_slug_fora_do_alcance_403(client: TestClient, estado: dict[str, Any]) -> None:
    # o banco não escolheu cliente para o slug pedido
    estado["fatos"] = _fatos(cliente_id=None)

    r = client.get("/api/sessao", params={"cliente": "padrao-lann-beta"})

    assert r.status_code == 403
    assert r.json() == {"detail": "Acesso negado."}
    (log,) = estado["sessoes"][0].logs
    assert log[4:7] == ("sessao", "negado", 2)


@pytest.mark.parametrize("slug", ["Com Espaço", "MAIUSCULO", "a--b", "x" * 81])
def test_slug_fora_do_formato_422(client: TestClient, slug: str) -> None:
    assert client.get("/api/sessao", params={"cliente": slug}).status_code == 422

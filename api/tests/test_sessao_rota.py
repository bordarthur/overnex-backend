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
# as linhas de cfg.setor (migration 34)
SETORES = [
    {"id": "distribuidora", "rotulo": "Distribuidora", "ordem": 1},
    {"id": "industria", "rotulo": "Indústria", "ordem": 2},
    {"id": "varejo", "rotulo": "Varejo", "ordem": 3},
    {"id": "servicos", "rotulo": "Serviços", "ordem": 4},
]


def _unidade(n: int, nome: str, setor: str | None, grupo: str | None) -> dict[str, Any]:
    return {
        "id": UUID(f"0e000000-0000-4000-8000-00000000a{n:03d}"),
        "nome": nome,
        "setor": setor,
        "grupo": grupo,
    }


# a tabela do Leão de Ouro (pedido do front), fora de ordem
LEAO_DE_OURO = [
    _unidade(6, "Usaflex 2", "varejo", "Usaflex"),
    _unidade(3, "Lupo 1", "varejo", "Lupo"),
    _unidade(1, "Leao 1", "varejo", "Leao"),
    _unidade(5, "Usaflex 1", "varejo", "Usaflex"),
    _unidade(2, "Leao 2", "varejo", "Leao"),
    _unidade(4, "Lupo 2", "varejo", "Lupo"),
]


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
        "empresas": [{"id": EMPRESA_A1, "nome": "Alfa Indústria", "setor": None, "grupo": None}],
    }

    def em_uma_ida(conn: Any, consultas: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        e["consultas"] = consultas
        linhas: dict[str, list[dict[str, Any]]] = {
            "usuario": [{"nome": "Ana Alfa", "email": "ana@alfa.example"}],
            "clientes": e["clientes"],
            "empresas": e["empresas"],
            "setores": SETORES,
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
        "negocios": None,
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
        "negocios": None,
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


@pytest.mark.parametrize(
    ("setores", "telas"),
    [
        (["distribuidora"], ["COM-01", "EST-01", "VG-01"]),
        (["distribuidora", "varejo"], ["COM-01", "EST-01", "VG-01"]),
        (["industria"], ["COM-01", "VG-01"]),
        # nenhuma unidade liberada com setor
        ([], ["COM-01", "VG-01"]),
    ],
)
def test_painel_de_outro_setor_fica_fora_do_menu_e_da_pre_montagem(
    client: TestClient, estado: dict[str, Any], setores: list[str], telas: list[str]
) -> None:
    # o Estoque (EST-01) ligado e liberado, mas só vale na Distribuidora
    estado["fatos"] = _fatos(setores=setores)
    estado["telas"] = ["COM-01", "EST-01", "VG-01"]

    corpo = client.get("/api/sessao").json()

    assert corpo["telas"] == telas
    assert estado["agendadas"] == [(telas, "padrao-lann-alfa")]


def test_admin_tambem_nao_ve_painel_de_outro_setor(
    client: TestClient, estado: dict[str, Any]
) -> None:
    estado["fatos"] = _fatos(vinculos=[], perfil="admin", cliente_id=CLIENTE_A, setores=[])
    estado["telas"] = ["EST-01", "VG-01"]

    assert client.get("/api/sessao").json()["telas"] == ["VG-01"]


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

    # o consultor abre a Gestão, só de leitura (desde 25/11/2026)
    assert (corpo["gestao"], corpo["cliente"], corpo["telas"]) == (True, None, [])
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


# --- unidades e a árvore da bandeja "Negócios" (migration 35) ------------------------------


def _ids(*n: int) -> list[str]:
    return [f"0e000000-0000-4000-8000-00000000a{i:03d}" for i in n]


def test_leao_de_ouro_tem_a_arvore_por_setor_e_grupo(
    client: TestClient, estado: dict[str, Any]
) -> None:
    estado["empresas"] = LEAO_DE_OURO
    estado["telas"] = ["COM-01", "VG-01"]

    corpo = client.get("/api/sessao").json()

    # na ordem da árvore: setor, grupo e nome
    assert [e["nome"] for e in corpo["empresas"]] == [
        "Leao 1",
        "Leao 2",
        "Lupo 1",
        "Lupo 2",
        "Usaflex 1",
        "Usaflex 2",
    ]
    assert corpo["negocios"] == {
        "setores": [
            {
                "id": "varejo",
                "rotulo": "Varejo",
                "grupos": [
                    {"id": "varejo/Leao", "rotulo": "Leao", "unidades": _ids(1, 2)},
                    {"id": "varejo/Lupo", "rotulo": "Lupo", "unidades": _ids(3, 4)},
                    {"id": "varejo/Usaflex", "rotulo": "Usaflex", "unidades": _ids(5, 6)},
                ],
            }
        ],
        # nenhuma tela liberada é de um setor só
        "telasPorSetor": {},
    }


def test_arvore_com_setor_sem_setor_e_sem_grupo_na_ordem(
    client: TestClient, estado: dict[str, Any]
) -> None:
    estado["empresas"] = [
        _unidade(1, "Loja 10", "varejo", None),
        _unidade(2, "Loja 2", "varejo", None),
        _unidade(3, "Depósito", None, None),
        _unidade(4, "CD Norte", "distribuidora", "Atacado"),
        _unidade(5, "Lupo 1", "varejo", "Lupo"),
    ]
    estado["fatos"] = _fatos(setores=["distribuidora", "varejo"])
    estado["telas"] = ["EST-01", "VG-01"]

    corpo = client.get("/api/sessao").json()

    # setor pela ordem de cfg.setor, sem setor no fim; grupo, sem grupo no fim do setor;
    # nome com ordem natural ("Loja 2" antes de "Loja 10")
    assert [e["nome"] for e in corpo["empresas"]] == [
        "CD Norte",
        "Lupo 1",
        "Loja 2",
        "Loja 10",
        "Depósito",
    ]
    arvore = corpo["negocios"]
    assert [(s["id"], s["rotulo"]) for s in arvore["setores"]] == [
        ("distribuidora", "Distribuidora"),
        ("varejo", "Varejo"),
        ("sem-setor", "Sem setor"),
    ]
    assert [(g["id"], g["rotulo"]) for g in arvore["setores"][1]["grupos"]] == [
        ("varejo/Lupo", "Lupo"),
        ("varejo/sem-grupo", "Sem grupo"),
    ]
    assert arvore["setores"][1]["grupos"][1]["unidades"] == _ids(2, 1)
    assert arvore["setores"][2]["grupos"] == [
        {"id": "sem-setor/sem-grupo", "rotulo": "Sem grupo", "unidades": _ids(3)}
    ]
    # toda unidade em exatamente um grupo
    todas = [u for s in arvore["setores"] for g in s["grupos"] for u in g["unidades"]]
    assert sorted(todas) == sorted(e["id"] for e in corpo["empresas"])
    # só as telas liberadas que são de um setor
    assert arvore["telasPorSetor"] == {"EST-01": ["distribuidora"]}


@pytest.mark.parametrize(
    "unidades",
    [
        # um par setor + grupo só, com várias unidades: o chip "Unidade"
        [_unidade(1, "Leao 1", "varejo", "Leao"), _unidade(2, "Leao 2", "varejo", "Leao")],
        # sem setor e sem grupo também é um negócio só
        [_unidade(1, "A", None, None), _unidade(2, "B", None, None)],
        [],
    ],
)
def test_um_negocio_so_vem_null(
    client: TestClient, estado: dict[str, Any], unidades: list[dict[str, Any]]
) -> None:
    estado["empresas"] = unidades
    corpo = client.get("/api/sessao").json()
    assert corpo["negocios"] is None
    assert len(corpo["empresas"]) == len(unidades)

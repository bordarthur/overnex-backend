"""GET /api/negocios (valores da bandeja "Negócios") e a árvore — sem banco.

A checagem lê os fatos de uma conexão falsa; as consultas da rota (uma ida ao banco) são
trocadas por linhas. O cliente do exemplo é o Leão de Ouro do pedido do front: 6 unidades
do Varejo em 3 grupos (os ids do mock do front, 0e000000-…-a001 a a006).
"""

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.core.db import Sessao, get_sessao
from app.main import create_app
from app.negocios import router as negocios_router
from app.negocios.arvore import natural, rotulo_valores
from app.seguranca.auth import UsuarioToken

USUARIO = UUID("d1000000-0000-4000-8000-00000000000a")
CLIENTE = UUID("c1000000-0000-4000-8000-0000000000d0")
SETORES = [
    {"id": "distribuidora", "rotulo": "Distribuidora", "ordem": 1},
    {"id": "industria", "rotulo": "Indústria", "ordem": 2},
    {"id": "varejo", "rotulo": "Varejo", "ordem": 3},
    {"id": "servicos", "rotulo": "Serviços", "ordem": 4},
]


def _id(n: int) -> UUID:
    return UUID(f"0e000000-0000-4000-8000-00000000a{n:03d}")


def _unidade(n: int, nome: str, grupo: str, setor: str = "varejo") -> dict[str, Any]:
    return {"id": _id(n), "nome": nome, "setor": setor, "grupo": grupo}


LEAO_DE_OURO = [
    _unidade(1, "Leao 1", "Leao"),
    _unidade(2, "Leao 2", "Leao"),
    _unidade(3, "Lupo 1", "Lupo"),
    _unidade(4, "Lupo 2", "Lupo"),
    _unidade(5, "Usaflex 1", "Usaflex"),
    _unidade(6, "Usaflex 2", "Usaflex"),
]
OUTUBRO = date(2026, 10, 1)
SETEMBRO = date(2026, 9, 1)


def _faturado(mes: date, valores: dict[int, int]) -> list[dict[str, Any]]:
    return [{"mes": mes, "empresa_id": _id(n), "valor": Decimal(v)} for n, v in valores.items()]


class Resultado:
    def __init__(self, linha: dict[str, Any]) -> None:
        self.linha = linha

    def fetchone(self) -> dict[str, Any]:
        return self.linha


class ConexaoFalsa:
    def __init__(self, estado: dict[str, Any]) -> None:
        self.estado = estado

    def execute(self, consulta: str, params: Any = None) -> Resultado:
        assert "seg.contexto_acesso" in consulta
        return Resultado(self.estado["fatos"])


@pytest.fixture
def estado(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    e: dict[str, Any] = {
        "fatos": {
            "usuario_id": USUARIO,
            "ativo": True,
            "vinculos": [{"cliente_id": str(CLIENTE), "papel": "gestor"}],
            "perfis_tela": {},
            "empresas": [],
            "perfil": "cliente_principal",
            "cliente_id": CLIENTE,
        },
        "unidades": LEAO_DE_OURO,
        "faturado": [
            *_faturado(OUTUBRO, {1: 410000, 2: 380000, 3: 160000, 4: 150000, 5: 210000}),
            *_faturado(
                SETEMBRO, {1: 900000, 2: 850000, 3: 300000, 4: 310000, 5: 400000, 6: 420000}
            ),
        ],
        "consultas": {},
    }

    def em_uma_ida(conn: Any, consultas: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        e["consultas"] = consultas
        linhas: dict[str, list[dict[str, Any]]] = {
            "unidades": e["unidades"],
            "setores": SETORES,
            "carga": [
                {"data_snapshot": date(2026, 10, 12), "fim": datetime(2026, 10, 12, 12, tzinfo=UTC)}
            ],
            "meses": [{"mes": SETEMBRO}, {"mes": OUTUBRO}],
            "faturado": e["faturado"],
        }
        return {nome: linhas[nome] for nome in consultas}

    monkeypatch.setattr(negocios_router, "em_uma_ida", em_uma_ida)
    monkeypatch.setattr(negocios_router, "existe", lambda conn, nome: True)
    return e


@pytest.fixture
def client(estado: dict[str, Any]) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_sessao] = lambda: Sessao(
        conn=ConexaoFalsa(estado),  # type: ignore[arg-type]
        usuario=UsuarioToken(id_autenticacao=UUID(int=1)),
    )
    return TestClient(app)


def _brl(valor: int) -> dict[str, Any]:
    return {"valor": valor, "unidade": "brl"}


def test_mes_da_carga_com_os_totais_inteiros(client: TestClient) -> None:
    r = client.get("/api/negocios")

    assert r.status_code == 200
    # a unidade sem venda no mês conta zero; o setor e o grupo são a soma das unidades
    assert r.json() == {
        "rotulo": "Valores: faturado de 1º a 12/10",
        "setores": {"varejo": _brl(1310000)},
        "grupos": {
            "varejo/Leao": _brl(790000),
            "varejo/Lupo": _brl(310000),
            "varejo/Usaflex": _brl(210000),
        },
        "unidades": {
            str(_id(1)): _brl(410000),
            str(_id(2)): _brl(380000),
            str(_id(3)): _brl(160000),
            str(_id(4)): _brl(150000),
            str(_id(5)): _brl(210000),
            str(_id(6)): _brl(0),
        },
    }


def test_mes_fechado(client: TestClient) -> None:
    corpo = client.get("/api/negocios", params={"mes": "2026-09"}).json()
    assert corpo["rotulo"] == "Valores: faturado em setembro de 2026"
    assert corpo["grupos"]["varejo/Usaflex"] == _brl(820000)


@pytest.mark.parametrize(("mes", "status"), [("2025-01", 422), ("2026-13", 422)])
def test_mes_fora_da_lista_ou_do_formato_422(client: TestClient, mes: str, status: int) -> None:
    assert client.get("/api/negocios", params={"mes": mes}).status_code == status


def test_so_as_unidades_liberadas(client: TestClient, estado: dict[str, Any]) -> None:
    # o RLS devolve só as unidades da pessoa: o faturado das outras não entra
    estado["unidades"] = [u for u in LEAO_DE_OURO if u["grupo"] != "Usaflex"]
    corpo = client.get("/api/negocios").json()
    assert set(corpo["grupos"]) == {"varejo/Leao", "varejo/Lupo"}
    assert corpo["setores"] == {"varejo": _brl(1100000)}


@pytest.mark.parametrize(
    "unidades",
    [
        # um negócio só (o chip "Unidade" no front)
        [_unidade(1, "Leao 1", "Leao"), _unidade(2, "Leao 2", "Leao")],
        [],
    ],
)
def test_um_negocio_so_mapas_vazios(
    client: TestClient, estado: dict[str, Any], unidades: list[dict[str, Any]]
) -> None:
    estado["unidades"] = unidades
    corpo = client.get("/api/negocios").json()
    assert (corpo["setores"], corpo["grupos"], corpo["unidades"]) == ({}, {}, {})


def test_sem_faturado_por_unidade_mapas_vazios(client: TestClient, estado: dict[str, Any]) -> None:
    estado["faturado"] = []
    corpo = client.get("/api/negocios").json()
    assert corpo == {
        "rotulo": "Sem faturado para mostrar.",
        "setores": {},
        "grupos": {},
        "unidades": {},
    }


def test_sem_cliente_escolhido_mapas_vazios(client: TestClient, estado: dict[str, Any]) -> None:
    estado["fatos"] = {**estado["fatos"], "perfil": "admin", "vinculos": [], "cliente_id": None}
    r = client.get("/api/negocios")
    assert r.status_code == 200
    assert r.json()["unidades"] == {}
    assert estado["consultas"] == {}


def test_cliente_fora_do_alcance_403(client: TestClient, estado: dict[str, Any]) -> None:
    estado["fatos"] = {**estado["fatos"], "cliente_id": None}
    r = client.get("/api/negocios", params={"cliente": "leao-de-ouro"})
    assert (r.status_code, r.json()) == (403, {"detail": "Acesso negado."})


def test_ordem_natural() -> None:
    nomes = ["Lupo 10", "lupo 2", "Lupo 1", "Ápice", "abc"]
    assert sorted(nomes, key=natural) == ["abc", "Ápice", "Lupo 1", "lupo 2", "Lupo 10"]


@pytest.mark.parametrize(
    ("mes", "dados_ate", "esperado"),
    [
        (OUTUBRO, date(2026, 10, 12), "Valores: faturado de 1º a 12/10"),
        (OUTUBRO, date(2026, 10, 1), "Valores: faturado em 01/10"),
        (SETEMBRO, date(2026, 10, 12), "Valores: faturado em setembro de 2026"),
        (SETEMBRO, None, "Valores: faturado em setembro de 2026"),
    ],
)
def test_rotulo_dos_valores(mes: date, dados_ate: date | None, esperado: str) -> None:
    assert rotulo_valores(mes, dados_ate) == esperado

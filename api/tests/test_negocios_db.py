"""GET /api/negocios e a árvore de GET /api/sessao contra o banco local (migration 35).

Alfa (Ana; carga de 12/10/2026): duas unidades da Distribuidora em grupos diferentes
(Atacado: Alfa Comércio; Fábrica: Alfa Indústria). O faturado por unidade vem do resumo
mensal; a linha sem unidade (tabela sem coluna de empresa) não entra em grupo nenhum.
"""

from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from tests.conftest import (
    CLIENTE_A,
    EMPRESA_A1,
    EMPRESA_A2,
    OID_A,
    OID_ADMIN,
    OID_B,
    USUARIO_A,
    auth,
)

pytestmark = pytest.mark.db

NEGOCIOS = "/api/negocios"


@pytest.fixture
def faturado_da_alfa(admin_conn: psycopg.Connection[tuple[Any, ...]]) -> Iterator[None]:
    admin_conn.execute(
        """
        insert into mart.resumo_mensal
            (cliente_id, empresa_id, tabela, medida, mes, valor, data_snapshot)
        values (%(c)s, %(a1)s, 'f_vendas_teste', 'faturado', '2026-10-01', 1000, '2026-10-12'),
               (%(c)s, %(a2)s, 'f_vendas_teste', 'faturado', '2026-10-01', 250, '2026-10-12'),
               (%(c)s, null, 'f_vendas_teste', 'faturado', '2026-10-01', 9999, '2026-10-12'),
               (%(c)s, %(a1)s, 'f_vendas_teste', 'faturado', '2026-09-01', 700, '2026-10-12'),
               -- não é venda: não entra
               (%(c)s, %(a2)s, 'f_receitas_teste', 'faturado', '2026-10-01', 5, '2026-10-12')
        """,
        {"c": CLIENTE_A, "a1": EMPRESA_A1, "a2": EMPRESA_A2},
    )
    yield
    admin_conn.execute(
        "delete from mart.resumo_mensal where cliente_id = %s and tabela like '%%_teste'",
        (CLIENTE_A,),
    )


@pytest.mark.usefixtures("faturado_da_alfa")
def test_valores_da_bandeja(client: TestClient) -> None:
    r = client.get(NEGOCIOS, headers=auth(OID_A))

    assert r.status_code == 200
    assert r.json() == {
        "rotulo": "Valores: faturado de 1º a 12/10",
        "setores": {"distribuidora": {"valor": 1250, "unidade": "brl"}},
        "grupos": {
            "distribuidora/Atacado": {"valor": 250, "unidade": "brl"},
            "distribuidora/Fábrica": {"valor": 1000, "unidade": "brl"},
        },
        "unidades": {
            EMPRESA_A1: {"valor": 1000, "unidade": "brl"},
            EMPRESA_A2: {"valor": 250, "unidade": "brl"},
        },
    }

    setembro = client.get(NEGOCIOS, params={"mes": "2026-09"}, headers=auth(OID_A)).json()
    assert setembro["rotulo"] == "Valores: faturado em setembro de 2026"
    assert setembro["unidades"][EMPRESA_A2] == {"valor": 0, "unidade": "brl"}


@pytest.fixture
def ana_so_com_a_industria(admin_conn: psycopg.Connection[tuple[Any, ...]]) -> Iterator[None]:
    admin_conn.execute(
        "delete from seg.usuario_empresa where usuario_id = %s and empresa_id = %s",
        (USUARIO_A, EMPRESA_A2),
    )
    yield
    admin_conn.execute(
        "insert into seg.usuario_empresa (usuario_id, cliente_id, empresa_id) values (%s, %s, %s)",
        (USUARIO_A, CLIENTE_A, EMPRESA_A2),
    )


@pytest.mark.usefixtures("faturado_da_alfa", "ana_so_com_a_industria")
def test_so_as_unidades_liberadas(client: TestClient) -> None:
    # com uma unidade só, um negócio só: sem árvore e sem valores
    sessao = client.get("/api/sessao", headers=auth(OID_A)).json()
    assert ([e["id"] for e in sessao["empresas"]], sessao["negocios"]) == ([EMPRESA_A1], None)
    assert client.get(NEGOCIOS, headers=auth(OID_A)).json()["unidades"] == {}
    # a unidade de fora do vínculo dá 403 nas telas
    r = client.get("/api/telas/visao-geral", params={"unidades": EMPRESA_A2}, headers=auth(OID_A))
    assert r.status_code == 403


@pytest.mark.usefixtures("faturado_da_alfa")
def test_admin_e_outro_cliente(client: TestClient) -> None:
    admin = client.get(NEGOCIOS, params={"cliente": "padrao-lann-alfa"}, headers=auth(OID_ADMIN))
    assert admin.json()["setores"] == {"distribuidora": {"valor": 1250, "unidade": "brl"}}
    # o Bruno não abre a Alfa
    fora = client.get(NEGOCIOS, params={"cliente": "padrao-lann-alfa"}, headers=auth(OID_B))
    assert (fora.status_code, fora.json()) == (403, {"detail": "Acesso negado."})

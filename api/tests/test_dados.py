"""GET /api/dados/{tabela} contra o banco local (seed: mart.f_estoque_nilo_maia)."""

from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from tests.conftest import (
    CLIENTE_A,
    EMPRESA_A1,
    EMPRESA_A2,
    EMPRESA_B1,
    OID_A,
    OID_B,
    USUARIO_A,
    auth,
)

pytestmark = pytest.mark.db

ESTOQUE = "/api/dados/f_estoque_nilo_maia"


def _coluna(corpo: dict[str, Any], nome: str) -> list[Any]:
    i = [c["nome"] for c in corpo["colunas"]].index(nome)
    return [linha[i] for linha in corpo["linhas"]]


def test_le_as_linhas_do_proprio_cliente(client: TestClient) -> None:
    r = client.get(ESTOQUE, headers=auth(OID_A))

    assert r.status_code == 200
    corpo = r.json()
    nomes = [c["nome"] for c in corpo["colunas"]]
    assert nomes[:3] == ["_linha", "_empresa_id", "en_filial"]
    assert "_cliente_id" not in nomes
    assert "EMPRESA" in nomes  # nome preservado
    assert {"nome": "CUSTOFIN", "tipo": "numeric"} in corpo["colunas"]
    assert _coluna(corpo, "en_codigoproduto") == ["P001", "P002", "P001"]
    assert _coluna(corpo, "CUSTOFIN") == [12.5, 7.25, 12.5]
    assert set(_coluna(corpo, "_empresa_id")) == {EMPRESA_A1, EMPRESA_A2}
    assert corpo["proximo"] is None
    assert corpo["atualizado_em"] is None  # o seed não tem carga desta tabela


def test_outro_cliente_ve_so_o_dele(client: TestClient) -> None:
    corpo = client.get(ESTOQUE, headers=auth(OID_B)).json()
    assert _coluna(corpo, "en_codigoproduto") == ["B900"]
    assert _coluna(corpo, "_empresa_id") == [EMPRESA_B1]


def test_filtra_por_empresa(client: TestClient) -> None:
    r = client.get(ESTOQUE, params={"empresa": EMPRESA_A2}, headers=auth(OID_A))
    assert r.status_code == 200
    assert r.json()["empresa_id"] == EMPRESA_A2
    assert _coluna(r.json(), "_empresa_id") == [EMPRESA_A2]


def test_empresa_de_outro_cliente_403(client: TestClient) -> None:
    r = client.get(ESTOQUE, params={"empresa": EMPRESA_B1}, headers=auth(OID_A))
    assert r.status_code == 403
    assert r.json() == {"detail": "Acesso negado."}


def test_paginacao(client: TestClient) -> None:
    primeira = client.get(ESTOQUE, params={"limite": 2}, headers=auth(OID_A)).json()
    assert _coluna(primeira, "_linha") == [1, 2]
    assert primeira["proximo"] == 2

    segunda = client.get(
        ESTOQUE, params={"limite": 2, "apos": primeira["proximo"]}, headers=auth(OID_A)
    ).json()
    assert _coluna(segunda, "_linha") == [3]
    assert segunda["proximo"] is None


def test_tabela_nao_registrada_para_o_cliente_403(client: TestClient) -> None:
    # existe no mart, mas não há fonte do cliente A apontando para ela
    r = client.get("/api/dados/f_vendas_nilo_maia", headers=auth(OID_A))
    assert r.status_code == 403


@pytest.mark.parametrize("nome", ["carga", "Mart", "f_estoque_nilo_maia;drop", "1abc"])
def test_nome_invalido_ou_fora_do_registro(client: TestClient, nome: str) -> None:
    r = client.get(f"/api/dados/{nome}", headers=auth(OID_A))
    assert r.status_code in (403, 422)


def test_sem_token_401(client: TestClient) -> None:
    assert client.get(ESTOQUE).status_code == 401


@pytest.fixture
def estoque_com_dado_nominal(
    admin_conn: psycopg.Connection[tuple[object, ...]],
) -> Iterator[None]:
    admin_conn.execute(
        "update cfg.fonte_carga set contem_dado_nominal = true "
        "where cliente_id = %s and tabela_destino = 'f_estoque_nilo_maia'",
        (CLIENTE_A,),
    )
    yield
    admin_conn.execute(
        "update cfg.fonte_carga set contem_dado_nominal = false "
        "where cliente_id = %s and tabela_destino = 'f_estoque_nilo_maia'",
        (CLIENTE_A,),
    )


@pytest.mark.usefixtures("estoque_com_dado_nominal")
def test_dado_nominal_sem_permissao_403(
    client: TestClient, admin_conn: psycopg.Connection[tuple[object, ...]]
) -> None:
    r = client.get(ESTOQUE, headers=auth(OID_A))

    assert r.status_code == 403
    linha = admin_conn.execute(
        """
        select codigo_tela, etapa_negada from seg.log_acesso
         where usuario_id = %s order by id desc limit 1
        """,
        (USUARIO_A,),
    ).fetchone()
    assert linha == ("tabela:f_estoque_nilo_maia", 6)

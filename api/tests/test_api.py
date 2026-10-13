"""Testes da API contra o banco local (supabase start + supabase db reset)."""

from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from tests.conftest import (
    EMPRESA_A1,
    EMPRESA_A2,
    EMPRESA_B1,
    OID_A,
    OID_B,
    OID_DESCONHECIDO,
    USUARIO_A,
    auth,
)

pytestmark = pytest.mark.db

FIN_01 = "/api/telas/fin-01"


def test_health_com_banco_no_ar(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_sem_token_401(client: TestClient) -> None:
    r = client.get(FIN_01, params={"empresa": EMPRESA_A1})
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


def test_token_invalido_401(client: TestClient) -> None:
    r = client.get(
        FIN_01, params={"empresa": EMPRESA_A1}, headers={"Authorization": "Bearer nao.e.jwt"}
    )
    assert r.status_code == 401


def test_isolamento_empresa_de_outro_cliente_403(
    client: TestClient, admin_conn: psycopg.Connection[tuple[Any, ...]]
) -> None:
    r = client.get(FIN_01, params={"empresa": EMPRESA_B1}, headers=auth(OID_A))

    assert r.status_code == 403
    # nenhum dado: só a mensagem genérica
    assert r.json() == {"detail": "Acesso negado."}

    # a negação ficou registrada (commit mesmo com 403)
    linha = admin_conn.execute(
        """
        select resultado, etapa_negada
          from seg.log_acesso
         where usuario_id = %s and empresa_id = %s
         order by id desc
         limit 1
        """,
        (USUARIO_A, EMPRESA_B1),
    ).fetchone()
    assert linha == ("negado", 5)


def test_usuario_desconhecido_403(client: TestClient) -> None:
    r = client.get(FIN_01, params={"empresa": EMPRESA_A1}, headers=auth(OID_DESCONHECIDO))
    assert r.status_code == 403
    assert r.json() == {"detail": "Acesso negado."}


@pytest.mark.parametrize(
    ("oid", "empresa", "esperado"),
    [
        (OID_A, EMPRESA_A1, "2026-10-12"),
        (OID_A, EMPRESA_A2, "2026-10-12"),
        (OID_B, EMPRESA_B1, "2026-10-11"),
    ],
)
def test_fin_01_atualizado_em_do_proprio_cliente(
    client: TestClient, oid: str, empresa: str, esperado: str
) -> None:
    r = client.get(FIN_01, params={"empresa": empresa}, headers=auth(oid))

    assert r.status_code == 200
    corpo = r.json()
    assert corpo["codigo_tela"] == "FIN-01"
    assert corpo["empresa_id"] == empresa
    assert corpo["atualizado_em"] == esperado
    assert corpo["mes"] == "2026-09-01"
    assert len(corpo["visuais"][0]["pontos"]) == 12
    assert isinstance(corpo["hero"]["valor"], float)


def test_fin_01_mes_invalido_422(client: TestClient) -> None:
    r = client.get(FIN_01, params={"empresa": EMPRESA_A1, "mes": "2026-13"}, headers=auth(OID_A))
    assert r.status_code == 422
    assert "2026-13" not in r.text

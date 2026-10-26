"""Conferência do cadastro depois do login (app.auth.cadastro) contra o banco local."""

from collections.abc import Iterator
from typing import Any
from uuid import UUID

import psycopg
import pytest

from app.auth.cadastro import UsuarioCadastrado, get_verificar_cadastro
from app.core.config import get_settings
from app.core.db import Pool, criar_pool
from tests.conftest import OID_A, OID_DESCONHECIDO, USUARIO_A

pytestmark = pytest.mark.db

RECURSO = "auth:teste"


@pytest.fixture(scope="module")
def pool() -> Iterator[Pool]:
    p = criar_pool(get_settings())
    p.open(wait=True, timeout=10)
    yield p
    p.close()


def _ultimo_log(conn: psycopg.Connection[tuple[Any, ...]], id_autenticacao: str) -> Any:
    return conn.execute(
        """
        select usuario_id::text, resultado, etapa_negada, motivo
          from seg.log_acesso
         where id_autenticacao = %s and codigo_tela = %s
         order by id desc
         limit 1
        """,
        (id_autenticacao, RECURSO),
    ).fetchone()


def test_usuario_ativo(pool: Pool, admin_conn: psycopg.Connection[tuple[Any, ...]]) -> None:
    cadastro = get_verificar_cadastro(pool)(UUID(OID_A), RECURSO)

    assert cadastro == UsuarioCadastrado(nome="Ana Alfa", email="ana@alfa.example")
    assert _ultimo_log(admin_conn, OID_A) == (USUARIO_A, "permitido", None, None)


def test_usuario_desconhecido(pool: Pool, admin_conn: psycopg.Connection[tuple[Any, ...]]) -> None:
    assert get_verificar_cadastro(pool)(UUID(OID_DESCONHECIDO), RECURSO) is None
    assert _ultimo_log(admin_conn, OID_DESCONHECIDO) == (
        None,
        "negado",
        1,
        "usuário inexistente ou inativo",
    )


def test_usuario_inativo(pool: Pool, admin_conn: psycopg.Connection[tuple[Any, ...]]) -> None:
    admin_conn.execute("update seg.usuario set ativo = false where id = %s", (USUARIO_A,))
    try:
        assert get_verificar_cadastro(pool)(UUID(OID_A), RECURSO) is None
    finally:
        admin_conn.execute("update seg.usuario set ativo = true where id = %s", (USUARIO_A,))
    assert _ultimo_log(admin_conn, OID_A) == (
        USUARIO_A,
        "negado",
        1,
        "usuário inexistente ou inativo",
    )

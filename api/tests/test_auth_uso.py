"""Último uso da sessão de login: o commit de cada pedido autenticado e seg.sessao_uso.

O limite é o da pessoa (seg.limite_sessao, migration 28): a Ana do seed é Cliente
principal, com 4 h. Os testes `db` rodam contra o Supabase local.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

from app.auth.uso import UsoSessao
from app.core.config import Settings
from app.core.db import INSERIR_LOG_ACESSO, USAR_SESSAO, Pool, Sessao, _gravar_logs_e_confirmar
from app.seguranca.auth import UsuarioToken
from tests.conftest import OID_A

SESSAO_ID = UUID("5e000000-0000-4000-8000-0000000000a1")
LIMITE_ANA = timedelta(hours=4)


class _Cursor:
    def __init__(self, chamadas: list[tuple[str, Any]]) -> None:
        self._chamadas = chamadas

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def execute(self, sql: str, params: Any) -> None:
        self._chamadas.append((sql, params))

    def executemany(self, sql: str, params: Any) -> None:
        self._chamadas.append((sql, list(params)))


class _Conexao:
    """Só o que _gravar_logs_e_confirmar usa; guarda os comandos e se fez commit."""

    def __init__(self) -> None:
        self.chamadas: list[tuple[str, Any]] = []
        self.pipelines = 0
        self.commits = 0

    @contextmanager
    def pipeline(self) -> Iterator[None]:
        self.pipelines += 1
        yield

    def cursor(self) -> _Cursor:
        return _Cursor(self.chamadas)

    def commit(self) -> None:
        self.commits += 1


def _sessao(
    sessao_id: UUID | None = SESSAO_ID, registrar_uso: bool = True
) -> tuple[Sessao, _Conexao]:
    conn = _Conexao()
    usuario = UsuarioToken(id_autenticacao=UUID(OID_A), sessao_id=sessao_id)
    return Sessao(conn=conn, usuario=usuario, registrar_uso=registrar_uso), conn  # type: ignore[arg-type]


def test_commit_registra_o_uso_da_sessao_na_mesma_ida() -> None:
    sessao, conn = _sessao()
    sessao.logs.append(("log",))

    _gravar_logs_e_confirmar(sessao)

    assert conn.chamadas == [
        (INSERIR_LOG_ACESSO, [("log",)]),
        (USAR_SESSAO, (SESSAO_ID, UUID(OID_A))),
    ]
    assert (conn.pipelines, conn.commits) == (1, 1)


def test_commit_sem_log_registra_o_uso() -> None:
    sessao, conn = _sessao()

    _gravar_logs_e_confirmar(sessao)

    assert conn.chamadas == [(USAR_SESSAO, (SESSAO_ID, UUID(OID_A)))]
    assert conn.commits == 1


@pytest.mark.parametrize(
    ("sessao_id", "registrar_uso"),
    [
        # token sem session_id (fake, Entra)
        (None, True),
        # pré-montagem em segundo plano: não é uso do usuário
        (SESSAO_ID, False),
    ],
)
def test_commit_sem_sessao_de_login_nao_registra(
    sessao_id: UUID | None, registrar_uso: bool
) -> None:
    sessao, conn = _sessao(sessao_id, registrar_uso)

    _gravar_logs_e_confirmar(sessao)

    assert conn.chamadas == []
    assert (conn.pipelines, conn.commits) == (0, 1)


# --- banco local ---------------------------------------------------------------------


@pytest.fixture
def pool() -> Iterator[Pool]:
    from app.core.db import criar_pool

    pool = criar_pool(Settings())
    pool.open()
    yield pool
    pool.close()


def _ultimo_uso_ha(admin: psycopg.Connection[Any], sessao_id: UUID, minutos: int) -> None:
    admin.execute(
        "update seg.sessao_uso set ultimo_uso = now() - make_interval(mins => %s)"
        " where sessao_id = %s",
        (minutos, sessao_id),
    )


def _idade(admin: psycopg.Connection[Any], sessao_id: UUID) -> timedelta | None:
    linha = admin.execute(
        "select now() - ultimo_uso from seg.sessao_uso where sessao_id = %s", (sessao_id,)
    ).fetchone()
    return linha[0] if linha else None


@pytest.mark.db
def test_db_renovar_logo_depois_de_entrar_e_parada(
    pool: Pool, admin_conn: psycopg.Connection[Any]
) -> None:
    uso = UsoSessao(pool)
    sessao_id, id_autenticacao = uuid4(), UUID(OID_A)

    uso.iniciar(sessao_id, id_autenticacao)
    assert uso.renovar(sessao_id, id_autenticacao) is True

    # dentro das 4 h da Ana: ainda renova
    _ultimo_uso_ha(admin_conn, sessao_id, 200)
    assert uso.renovar(sessao_id, id_autenticacao) is True

    _ultimo_uso_ha(admin_conn, sessao_id, 241)
    assert uso.renovar(sessao_id, id_autenticacao) is False
    assert _idade(admin_conn, sessao_id) is None
    # sem registro: continua recusada
    assert uso.renovar(sessao_id, id_autenticacao) is False


@pytest.mark.db
def test_db_pedido_autenticado_conta_como_uso(
    pool: Pool, admin_conn: psycopg.Connection[Any]
) -> None:
    uso = UsoSessao(pool)
    sessao_id, id_autenticacao = uuid4(), UUID(OID_A)
    uso.iniciar(sessao_id, id_autenticacao)
    usuario = UsuarioToken(id_autenticacao=id_autenticacao, sessao_id=sessao_id)

    _ultimo_uso_ha(admin_conn, sessao_id, 200)
    with pool.connection() as conn:
        _gravar_logs_e_confirmar(Sessao(conn=conn, usuario=usuario, registrar_uso=True))
    idade = _idade(admin_conn, sessao_id)
    assert idade is not None
    assert idade < timedelta(minutes=1)

    # parada há mais que o limite: o pedido não a revive
    _ultimo_uso_ha(admin_conn, sessao_id, 300)
    with pool.connection() as conn:
        _gravar_logs_e_confirmar(Sessao(conn=conn, usuario=usuario, registrar_uso=True))
    idade = _idade(admin_conn, sessao_id)
    assert idade is not None
    assert idade > LIMITE_ANA
    assert uso.renovar(sessao_id, id_autenticacao) is False

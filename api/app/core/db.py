"""Pool de conexões e a transação por requisição."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from psycopg import Connection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool

from app.core.config import Settings
from app.core.erros import AcessoNegado
from app.seguranca.auth import UsuarioToken, get_usuario

Conexao = Connection[DictRow]
Pool = ConnectionPool[Conexao]


def criar_pool(settings: Settings) -> Pool:
    """Cria o pool fechado; quem chama abre (lifespan do app)."""
    return ConnectionPool(
        conninfo=settings.database_url.get_secret_value(),
        connection_class=Connection[DictRow],
        # O pooler do Supabase em modo transação não aceita prepared statements.
        kwargs={"prepare_threshold": None, "row_factory": dict_row, "autocommit": False},
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
        check=ConnectionPool.check_connection,
        name="overnex-api",
        open=False,
    )


def get_pool(request: Request) -> Pool:
    pool: Pool = request.app.state.pool
    return pool


@dataclass(frozen=True)
class Sessao:
    """Conexão da requisição, já com app.usuario_id definido na transação."""

    conn: Conexao
    usuario: UsuarioToken
    usuario_id: UUID | None
    usuario_ativo: bool


def get_sessao(
    usuario: Annotated[UsuarioToken, Depends(get_usuario)],
    pool: Annotated[Pool, Depends(get_pool)],
) -> Iterator[Sessao]:
    """Abre UMA transação por requisição e define app.usuario_id nela.

    - sucesso: commit;
    - AcessoNegado: commit (grava o log do acesso negado; nada além dele foi escrito);
    - qualquer outro erro: rollback.
    """
    with pool.connection() as conn:
        try:
            linha = conn.execute(
                "select id, ativo from seg.resolver_usuario(%s)", (usuario.object_id,)
            ).fetchone()
            usuario_id: UUID | None = linha["id"] if linha else None
            ativo = bool(linha and linha["ativo"])
            conn.execute(
                "select set_config('app.usuario_id', %s, true)",
                (str(usuario_id) if usuario_id else "",),
            )
            yield Sessao(conn=conn, usuario=usuario, usuario_id=usuario_id, usuario_ativo=ativo)
        except AcessoNegado:
            conn.commit()
            raise
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()

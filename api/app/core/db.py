"""Pool de conexões e a transação por requisição."""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Request
from psycopg import Connection
from psycopg.rows import DictRow, dict_row
from psycopg.sql import Composed
from psycopg_pool import ConnectionPool

from app.core.config import Settings
from app.core.erros import AcessoNegado
from app.seguranca.auth import UsuarioToken, get_usuario

Conexao = Connection[DictRow]
Pool = ConnectionPool[Conexao]

INSERIR_LOG_ACESSO = """
    insert into seg.log_acesso
        (usuario_id, id_autenticacao, cliente_id, empresa_id, codigo_tela,
         resultado, etapa_negada, motivo)
    values (%s, %s, %s, %s, %s, %s, %s, %s)
"""


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
    """Conexão da requisição. app.usuario_id é definido pela checagem de acesso
    (seg.contexto_acesso, em app/seguranca/acesso.py); antes dela, o RLS não mostra nada."""

    conn: Conexao
    usuario: UsuarioToken
    pool: Pool | None = None
    # linhas de seg.log_acesso da requisição: gravadas junto com o commit (ver get_sessao)
    logs: list[tuple[Any, ...]] = field(default_factory=list)


def _gravar_logs_e_confirmar(conn: Conexao, logs: list[tuple[Any, ...]]) -> None:
    """Insere os logs pendentes e faz o commit numa ida ao banco só (pipeline)."""
    if not logs:
        conn.commit()
        return
    with conn.pipeline():
        with conn.cursor() as cur:
            cur.executemany(INSERIR_LOG_ACESSO, logs)
        conn.commit()


def get_sessao(
    usuario: Annotated[UsuarioToken, Depends(get_usuario)],
    pool: Annotated[Pool, Depends(get_pool)],
) -> Iterator[Sessao]:
    """Abre UMA transação por requisição.

    - sucesso: grava o log de acesso e faz commit;
    - AcessoNegado: idem (grava o log do acesso negado; nada além dele foi escrito);
    - qualquer outro erro: rollback (o log daquela requisição também não fica, como antes).
    """
    with pool.connection() as conn:
        sessao = Sessao(conn=conn, usuario=usuario, pool=pool)
        try:
            yield sessao
        except AcessoNegado:
            _gravar_logs_e_confirmar(conn, sessao.logs)
            raise
        except BaseException:
            conn.rollback()
            raise
        else:
            _gravar_logs_e_confirmar(conn, sessao.logs)


def confirmar_agora(sessao: Sessao) -> None:
    """Grava os logs pendentes e faz o commit já, antes da resposta.

    O get_sessao só confirma depois que a resposta sai (o FastAPI roda o fim das
    dependências com yield depois de mandar a resposta). Quem grava algo que o front lê
    logo depois de receber a resposta chama isto antes de responder. O resto da requisição
    segue em outra transação, sem app.usuario_id.
    """
    _gravar_logs_e_confirmar(sessao.conn, sessao.logs)
    sessao.logs.clear()


@contextmanager
def conexao_paralela(pool: Pool, usuario_id: UUID) -> Iterator[Conexao]:
    """Outra conexão para o mesmo usuário (consultas da mesma tela em paralelo).

    Só leitura: define app.usuario_id na transação (o mesmo RLS da conexão principal) e
    desfaz tudo no fim. Só use com o usuario_id de uma checagem de acesso que passou.
    """
    with pool.connection() as conn:
        try:
            conn.execute(
                "select set_config('app.usuario_id', %s, true)",
                (str(usuario_id),),
            )
            yield conn
        finally:
            conn.rollback()


def em_uma_ida(
    conn: Conexao, consultas: Mapping[str, tuple[str | Composed, Any]]
) -> dict[str, list[DictRow]]:
    """Várias consultas numa ida e volta só (pipeline do psycopg): nome → linhas.

    O banco fica a ~100 ms de ida e volta; consultas pequenas e independentes custam quase
    só a rede, então vão juntas. Rodam em ordem, na transação da conexão: a primeira que
    falhar desfaz a transação e levanta o erro.
    """
    if not consultas:
        return {}
    with conn.pipeline():
        cursores = {nome: conn.execute(q, p) for nome, (q, p) in consultas.items()}
        # o primeiro fetch manda tudo e espera as respostas; os outros já estão aqui
        return {nome: c.fetchall() for nome, c in cursores.items()}

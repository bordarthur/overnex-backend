"""Pool de conexões e a transação por requisição.

Cada ida ao banco custa a latência da rede, então a conexão do pool fica em autocommit: o
psycopg, quando abre a transação sozinho, manda o BEGIN e espera a resposta (uma ida só
para isso, até dentro de pipeline). Aqui o `begin` vai no mesmo pipeline da primeira
consulta (`em_uma_ida`); `commit()` e `rollback()` valem como sempre. Fora de transação,
cada comando é confirmado sozinho, e `set_config(..., true)` não dura: quem precisa do RLS
da requisição começa por `em_uma_ida` (a checagem de acesso faz isso).
"""

import time
import weakref
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Request
from psycopg import Connection
from psycopg.pq import TransactionStatus
from psycopg.rows import DictRow, dict_row
from psycopg.sql import Composed
from psycopg_pool import ConnectionPool

from app.core.config import Settings
from app.core.erros import AcessoNegado
from app.seguranca.auth import UsuarioToken, get_usuario

Conexao = Connection[DictRow]
Pool = ConnectionPool[Conexao]
# (SQL, parâmetros) de uma consulta de em_uma_ida
type Consulta = tuple[str | Composed, Any]

INSERIR_LOG_ACESSO = """
    insert into seg.log_acesso
        (usuario_id, id_autenticacao, cliente_id, empresa_id, codigo_tela,
         resultado, etapa_negada, motivo)
    values (%s, %s, %s, %s, %s, %s, %s, %s)
"""
# último uso da sessão de login, dentro do limite da pessoa (migration 28; ver
# app/auth/uso.py)
USAR_SESSAO = "select seg.usar_sessao(%s, %s)"
SQL_RLS = "select set_config('app.usuario_id', %s, true), set_config('app.cliente_id', %s, true)"


# hora (time.monotonic) em que cada conexão voltou ao pool
_devolvidas: weakref.WeakKeyDictionary[Conexao, float] = weakref.WeakKeyDictionary()


def _marcar_devolucao(conn: Conexao) -> None:
    _devolvidas[conn] = time.monotonic()


def checar_se_parada(segundos: float) -> Callable[[Conexao], None]:
    """Teste da conexão ao sair do pool, só para a que ficou parada mais que `segundos`.

    O teste custa uma ida ao banco por empréstimo. Ele existe porque o pooler (ou a rede)
    fecha conexão parada; a que foi usada há pouco não precisa. Conexão nova, que nunca
    voltou ao pool, é testada. Com `segundos` <= 0, testa sempre.
    """

    def testar(conn: Conexao) -> None:
        devolvida = _devolvidas.get(conn)
        if segundos > 0 and devolvida is not None and time.monotonic() - devolvida < segundos:
            return
        ConnectionPool.check_connection(conn)

    return testar


def criar_pool(settings: Settings) -> Pool:
    """Cria o pool fechado; quem chama abre (lifespan do app)."""
    return ConnectionPool(
        conninfo=settings.database_url.get_secret_value(),
        connection_class=Connection[DictRow],
        # O pooler do Supabase em modo transação não aceita prepared statements.
        # autocommit: o `begin` vai junto com a primeira consulta (ver o topo do módulo).
        kwargs={"prepare_threshold": None, "row_factory": dict_row, "autocommit": True},
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
        check=checar_se_parada(settings.db_teste_parada_segundos),
        reset=_marcar_devolucao,
        name="overnex-api",
        open=False,
    )


def get_pool(request: Request) -> Pool:
    pool: Pool = request.app.state.pool
    return pool


@dataclass(frozen=True)
class Sessao:
    """Conexão da requisição. app.usuario_id e app.cliente_id são definidos pela checagem de
    acesso (seg.contexto_acesso, em app/seguranca/acesso.py); antes dela, o RLS não mostra
    nada."""

    conn: Conexao
    usuario: UsuarioToken
    pool: Pool | None = None
    # linhas de seg.log_acesso da requisição: gravadas junto com o commit (ver get_sessao)
    logs: list[tuple[Any, ...]] = field(default_factory=list)
    # linhas lidas na mesma ida da checagem de acesso, por nome (app/telas/preleitura.py)
    adiantadas: dict[str, list[DictRow]] = field(default_factory=dict)
    # com ele (e o session_id no token), o commit registra o pedido como uso da sessão de
    # login, dentro do limite da pessoa. False = não registra (pré-montagem, testes)
    registrar_uso: bool = False


def _gravar_logs_e_confirmar(sessao: Sessao) -> None:
    """Insere os logs pendentes, registra o uso da sessão de login e faz o commit numa ida
    ao banco só (pipeline)."""
    conn, logs = sessao.conn, sessao.logs
    sessao_id = sessao.usuario.sessao_id
    usar = sessao.registrar_uso and sessao_id is not None
    if not logs and not usar:
        conn.commit()
        return
    with conn.pipeline():
        with conn.cursor() as cur:
            if logs:
                cur.executemany(INSERIR_LOG_ACESSO, logs)
            if usar:
                cur.execute(USAR_SESSAO, (sessao_id, sessao.usuario.id_autenticacao))
        conn.commit()


def get_sessao(
    usuario: Annotated[UsuarioToken, Depends(get_usuario)],
    pool: Annotated[Pool, Depends(get_pool)],
) -> Iterator[Sessao]:
    """Abre UMA transação por requisição.

    - sucesso: grava o log de acesso e o uso da sessão de login e faz commit;
    - AcessoNegado: idem (grava o log do acesso negado; nada além dele foi escrito);
    - qualquer outro erro: rollback (o log daquela requisição também não fica, como antes).
    """
    with pool.connection() as conn:
        sessao = Sessao(conn=conn, usuario=usuario, pool=pool, registrar_uso=True)
        try:
            yield sessao
        except AcessoNegado:
            _gravar_logs_e_confirmar(sessao)
            raise
        except BaseException:
            conn.rollback()
            raise
        else:
            _gravar_logs_e_confirmar(sessao)


def confirmar_agora(sessao: Sessao) -> None:
    """Grava os logs pendentes e faz o commit já, antes da resposta.

    O get_sessao só confirma depois que a resposta sai (o FastAPI roda o fim das
    dependências com yield depois de mandar a resposta). Quem grava algo que o front lê
    logo depois de receber a resposta chama isto antes de responder. O resto da requisição
    segue em outra transação, sem app.usuario_id nem app.cliente_id.
    """
    _gravar_logs_e_confirmar(sessao)
    sessao.logs.clear()


@contextmanager
def conexao_paralela(pool: Pool, usuario_id: UUID, cliente_id: UUID) -> Iterator[Conexao]:
    """Outra conexão para o mesmo usuário e o mesmo cliente escolhido (consultas da mesma
    tela em paralelo).

    Só leitura: define app.usuario_id e app.cliente_id na transação (o mesmo RLS da conexão
    principal) e desfaz tudo no fim. Só use com o usuario_id e o cliente_id de uma checagem
    de acesso que passou.
    """
    with pool.connection() as conn:
        try:
            # o begin e o RLS numa ida só
            em_uma_ida(conn, {"rls": (SQL_RLS, (str(usuario_id), str(cliente_id)))})
            yield conn
        finally:
            conn.rollback()


def sem_transacao(conn: Conexao) -> bool:
    """Conexão do pool (autocommit) ainda sem transação: o `begin` tem que ir junto com a
    primeira consulta. A que não é autocommit (fora do pool) abre a transação sozinha."""
    return (
        bool(getattr(conn, "autocommit", False))
        and conn.info.transaction_status == TransactionStatus.IDLE
    )


def em_uma_ida(conn: Conexao, consultas: Mapping[str, Consulta]) -> dict[str, list[DictRow]]:
    """Várias consultas numa ida e volta só (pipeline do psycopg): nome → linhas.

    O banco fica a ~50 ms de ida e volta; consultas pequenas e independentes custam quase
    só a rede, então vão juntas. Rodam em ordem, na transação da conexão (se ainda não há
    uma, o `begin` vai na frente, na mesma ida): a primeira que falhar desfaz a transação e
    levanta o erro.
    """
    if not consultas:
        return {}
    with conn.pipeline():
        if sem_transacao(conn):
            conn.execute("begin")
        cursores = {nome: conn.execute(q, p) for nome, (q, p) in consultas.items()}
    # Só depois do bloco: a saída do pipeline manda tudo e espera as respostas, numa ida. Um
    # fetch lá dentro pediria as respostas antes (outra ida, medida em 11/11/2026).
    return {nome: c.fetchall() for nome, c in cursores.items()}

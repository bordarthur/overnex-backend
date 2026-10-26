"""Consultas da mesma tela em paralelo, cada uma numa conexão do mesmo usuário.

O banco fica a ~120 ms do Brasil por ida e volta: consultas independentes de uma tela
rodam ao mesmo tempo, e o tempo da tela passa a ser o da mais lenta, não a soma.
"""

import contextvars
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import replace

from app.core.db import conexao_paralela
from app.seguranca.acesso import ContextoConsolidado

# ramos em paralelo (cada um usa uma conexão do pool)
_ramos = ThreadPoolExecutor(max_workers=8, thread_name_prefix="telas")


def em_paralelo[T](
    ctx: ContextoConsolidado, ramos: Sequence[Callable[[ContextoConsolidado], T]]
) -> list[T]:
    """Roda os ramos ao mesmo tempo e devolve os resultados na ordem.

    O primeiro usa a conexão da requisição; cada um dos outros, uma conexão própria do
    mesmo usuário (app.usuario_id da checagem que passou, o mesmo RLS), só leitura. O cache
    por requisição (app/core/cache.py) vai junto: cada thread recebe uma cópia do contexto,
    que aponta para o mesmo dicionário. Sem pool (testes), roda em sequência.
    """
    pool = ctx.sessao.pool
    if pool is None or len(ramos) < 2:
        return [ramo(ctx) for ramo in ramos]

    def em_outra_conexao(ramo: Callable[[ContextoConsolidado], T]) -> T:
        with conexao_paralela(pool, ctx.usuario_id) as conn:
            return ramo(replace(ctx, sessao=replace(ctx.sessao, conn=conn)))

    futuros: list[Future[T]] = [
        _ramos.submit(contextvars.copy_context().run, em_outra_conexao, ramo) for ramo in ramos[1:]
    ]
    try:
        primeiro = ramos[0](ctx)
    finally:
        # espera todos, mesmo com erro: nenhuma conexão fica em uso depois da requisição
        wait(futuros)
    # result() relança o erro de um ramo
    return [primeiro, *(f.result() for f in futuros)]

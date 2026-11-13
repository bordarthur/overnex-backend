"""Saldo inicial de caixa do cliente (cfg.saldo_inicial, migration 22).

Cada linha é o saldo de todas as contas no início de um dia. Lido na pré-leitura
(app/telas/preleitura.py), junto com o resto do cadastro: entra na versão do cadastro, e um
saldo trocado no banco vale na hora, sem esperar o prazo do cache. Sem a migration 22, a
lista fica vazia.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from app.core.cache import existe, memo
from app.core.db import Conexao

SQL_SALDO_INICIAL = """
    select data, saldo
      from cfg.saldo_inicial
     where cliente_id = %s
     order by data
"""

type Saldos = list[tuple[date, Decimal]]


def ler_saldos(conn: Conexao, cliente_id: UUID) -> Saldos:
    """Os saldos cadastrados do cliente, do mais antigo para o mais recente."""

    def ler() -> Saldos:
        if not existe(conn, "cfg.saldo_inicial"):
            return []
        return [(r["data"], r["saldo"]) for r in conn.execute(SQL_SALDO_INICIAL, (cliente_id,))]

    return memo(("saldo_inicial", cliente_id), ler)

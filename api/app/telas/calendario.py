"""Calendário de dias úteis: segunda a sexta, fora dos feriados.

Feriados nacionais: biblioteca `holidays` (BR). Feriados do cliente (estaduais,
municipais, ponto facultativo): cfg.feriado (migration 9).
"""

from datetime import date, timedelta
from uuid import UUID

import holidays

from app.core.cache import existe, memo
from app.core.db import Conexao


def ultimo_dia_do_mes(mes: date) -> date:
    proximo = date(mes.year + mes.month // 12, mes.month % 12 + 1, 1)
    return proximo - timedelta(days=1)


def feriados_nacionais(ano: int) -> set[date]:
    return set(holidays.country_holidays("BR", years=ano))


def feriados(conn: Conexao, cliente_id: UUID, inicio: date, fim: date) -> set[date]:
    """Nacionais + do cliente entre `inicio` e `fim` (inclusive)."""
    nacionais = set().union(*(feriados_nacionais(a) for a in range(inicio.year, fim.year + 1)))
    do_cliente = memo(("feriados", cliente_id), lambda: _feriados_do_cliente(conn, cliente_id))
    return {d for d in nacionais | do_cliente if inicio <= d <= fim}


def _feriados_do_cliente(conn: Conexao, cliente_id: UUID) -> set[date]:
    """Todos os feriados do cliente (cfg.feriado é pequena); vazio sem a migration 9."""
    if not existe(conn, "cfg.feriado"):
        return set()
    return {r["data"] for r in conn.execute(SQL_FERIADOS, (cliente_id,))}


SQL_FERIADOS = "select data from cfg.feriado where cliente_id = %s"


def dias_uteis(inicio: date, fim: date, feriados: set[date]) -> int:
    """Dias úteis de `inicio` a `fim`, inclusive."""
    total = 0
    dia = inicio
    while dia <= fim:
        if dia.weekday() < 5 and dia not in feriados:
            total += 1
        dia += timedelta(days=1)
    return total


def mais_dias_uteis(dia: date, n: int, feriados: set[date]) -> date:
    """O n-ésimo dia útil depois de `dia` (D+n), sem contar o próprio `dia`."""
    while n > 0:
        dia += timedelta(days=1)
        if dia.weekday() < 5 and dia not in feriados:
            n -= 1
    return dia

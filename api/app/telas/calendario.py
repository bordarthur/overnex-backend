"""Calendário de dias úteis: segunda a sexta, fora dos feriados.

Feriados nacionais: biblioteca `holidays` (BR). Feriados do cliente (estaduais,
municipais, ponto facultativo): cfg.feriado (migration 9).
"""

from datetime import date, timedelta
from uuid import UUID

import holidays

from app.core.db import Conexao


def ultimo_dia_do_mes(mes: date) -> date:
    proximo = date(mes.year + mes.month // 12, mes.month % 12 + 1, 1)
    return proximo - timedelta(days=1)


def feriados_nacionais(ano: int) -> set[date]:
    return set(holidays.country_holidays("BR", years=ano))


def feriados(conn: Conexao, cliente_id: UUID, inicio: date, fim: date) -> set[date]:
    """Nacionais + do cliente entre `inicio` e `fim` (inclusive)."""
    nacionais = set().union(*(feriados_nacionais(a) for a in range(inicio.year, fim.year + 1)))
    existe = conn.execute("select to_regclass('cfg.feriado') is not null as ok").fetchone()
    do_cliente: set[date] = set()
    if existe and existe["ok"]:  # sem a migration 9, só os nacionais
        do_cliente = {
            r["data"]
            for r in conn.execute(
                "select data from cfg.feriado where cliente_id = %s and data between %s and %s",
                (cliente_id, inicio, fim),
            )
        }
    return {d for d in nacionais | do_cliente if inicio <= d <= fim}


def dias_uteis(inicio: date, fim: date, feriados: set[date]) -> int:
    """Dias úteis de `inicio` a `fim`, inclusive."""
    total = 0
    dia = inicio
    while dia <= fim:
        if dia.weekday() < 5 and dia not in feriados:
            total += 1
        dia += timedelta(days=1)
    return total

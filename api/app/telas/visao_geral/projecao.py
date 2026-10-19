"""Ritmo e projeção de fechamento do mês, em dias úteis. Função pura.

du_mes        dias úteis do mês
du_passados   dias úteis do dia 1 até o último dia com venda
du_restantes  du_mes − du_passados (0 = mês fechado)
ritmo         faturado / du_passados
projecao      ritmo × du_mes (mês fechado: o próprio faturado)
diferenca     projecao − meta
a_realizar    (meta − faturado) / du_restantes, por dia útil (mês fechado: None)
meta_ate_data meta × du_passados / du_mes
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.telas.calendario import dias_uteis, ultimo_dia_do_mes


@dataclass(frozen=True)
class Projecao:
    du_mes: int
    du_passados: int
    du_restantes: int
    ritmo: Decimal
    projecao: Decimal
    diferenca: Decimal
    # None com o mês fechado; zero ou negativo com a meta já batida
    a_realizar: Decimal | None
    meta_ate_data: Decimal


def projetar_faturado(faturado: Decimal, ultimo_dia: date, feriados: set[date]) -> Decimal | None:
    """Faturado projetado para o mês inteiro (ritmo × dias úteis); None sem ritmo."""
    inicio = ultimo_dia.replace(day=1)
    du_mes = dias_uteis(inicio, ultimo_dia_do_mes(inicio), feriados)
    du_passados = dias_uteis(inicio, ultimo_dia, feriados)
    if du_mes == 0 or du_passados == 0:
        return None
    return faturado / du_passados * du_mes


def projetar(
    faturado: Decimal, meta: Decimal, ultimo_dia: date, feriados: set[date]
) -> Projecao | None:
    """None se o mês não tem dia útil até a última venda (não há ritmo para projetar)."""
    inicio = ultimo_dia.replace(day=1)
    du_mes = dias_uteis(inicio, ultimo_dia_do_mes(inicio), feriados)
    du_passados = dias_uteis(inicio, ultimo_dia, feriados)
    if du_mes == 0 or du_passados == 0:
        return None
    du_restantes = du_mes - du_passados
    ritmo = faturado / du_passados
    projecao = faturado if du_restantes == 0 else ritmo * du_mes
    return Projecao(
        du_mes=du_mes,
        du_passados=du_passados,
        du_restantes=du_restantes,
        ritmo=ritmo,
        projecao=projecao,
        diferenca=projecao - meta,
        a_realizar=None if du_restantes == 0 else (meta - faturado) / du_restantes,
        meta_ate_data=meta * du_passados / du_mes,
    )

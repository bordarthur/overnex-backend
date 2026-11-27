"""Datas: dias úteis (segunda a sexta, fora dos feriados nacionais) e meses."""

from collections.abc import Iterator
from datetime import date, timedelta
from functools import cache

# feriados nacionais de data fixa (mês, dia)
FIXOS = ((1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (11, 20), (12, 25))


def pascoa(ano: int) -> date:
    """Domingo de Páscoa (algoritmo de Meeus para o calendário gregoriano)."""
    a = ano % 19
    b, c = divmod(ano, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    mes, dia = divmod(h + m - 7 * n + 114, 31)
    return date(ano, mes, dia + 1)


@cache
def feriados(ano: int) -> frozenset[date]:
    """Nacionais, mais carnaval (segunda e terça), Sexta-feira Santa e Corpus Christi."""
    p = pascoa(ano)
    moveis = (p - timedelta(days=48), p - timedelta(days=47), p - timedelta(days=2))
    return frozenset([date(ano, m, d) for m, d in FIXOS] + [*moveis, p + timedelta(days=60)])


def util(d: date) -> bool:
    return d.weekday() < 5 and d not in feriados(d.year)


def proximo_util(d: date) -> date:
    """O próprio dia, se útil; senão, o próximo dia útil."""
    while not util(d):
        d += timedelta(days=1)
    return d


def anterior_util(d: date) -> date:
    """O próprio dia, se útil; senão, o dia útil anterior."""
    while not util(d):
        d -= timedelta(days=1)
    return d


def mais_dias_uteis(d: date, n: int) -> date:
    """O n-ésimo dia útil depois de `d`."""
    while n > 0:
        d += timedelta(days=1)
        if util(d):
            n -= 1
    return d


def inicio_mes(d: date) -> date:
    return d.replace(day=1)


def somar_meses(m: date, n: int) -> date:
    """Primeiro dia do mês `n` meses depois do mês de `m` (n pode ser negativo)."""
    total = m.year * 12 + m.month - 1 + n
    return date(total // 12, total % 12 + 1, 1)


def ultimo_dia(m: date) -> date:
    return somar_meses(m, 1) - timedelta(days=1)


def dia_do_mes(m: date, dia: int) -> date:
    """O dia `dia` do mês de `m`; no mês mais curto, o último dia."""
    return m.replace(day=min(dia, ultimo_dia(m).day))


def meses_entre(m: date, n: date) -> int:
    """Quantos meses de `m` até `n` (pelo mês, sem olhar o dia)."""
    return (n.year - m.year) * 12 + n.month - m.month


def meses(de: date, ate: date) -> Iterator[date]:
    """Primeiro dia de cada mês, do mês de `de` ao mês de `ate`, inclusive."""
    m = inicio_mes(de)
    while m <= ate:
        yield m
        m = somar_meses(m, 1)


@cache
def dias_uteis(m: date) -> tuple[date, ...]:
    """Os dias úteis do mês de `m`."""
    d = inicio_mes(m)
    fim = ultimo_dia(m)
    dias = []
    while d <= fim:
        if util(d):
            dias.append(d)
        d += timedelta(days=1)
    return tuple(dias)


def dia_util_do_mes(m: date, n: int) -> date:
    """O n-ésimo dia útil do mês (1 = o primeiro)."""
    return dias_uteis(m)[n - 1]

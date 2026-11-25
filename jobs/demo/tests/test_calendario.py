from datetime import date

from demo.calendario import (
    dia_do_mes,
    dia_util_do_mes,
    feriados,
    meses_entre,
    pascoa,
    proximo_util,
    somar_meses,
    util,
)


def test_pascoa_e_feriados_moveis() -> None:
    assert pascoa(2026) == date(2026, 4, 5)
    assert pascoa(2025) == date(2025, 4, 20)
    f = feriados(2026)
    assert {date(2026, 2, 16), date(2026, 2, 17), date(2026, 4, 3), date(2026, 6, 4)} <= f
    assert date(2026, 11, 20) in f


def test_dias_uteis() -> None:
    assert not util(date(2026, 11, 21))  # sábado
    assert not util(date(2026, 11, 20))  # Consciência Negra
    assert proximo_util(date(2026, 11, 20)) == date(2026, 11, 23)
    assert dia_util_do_mes(date(2026, 1, 1), 1) == date(2026, 1, 2)


def test_meses() -> None:
    assert somar_meses(date(2026, 11, 25), 2) == date(2027, 1, 1)
    assert somar_meses(date(2026, 1, 1), -1) == date(2025, 12, 1)
    assert meses_entre(date(2025, 11, 1), date(2026, 2, 1)) == 3
    assert dia_do_mes(date(2026, 2, 1), 30) == date(2026, 2, 28)

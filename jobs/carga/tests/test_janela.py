"""Janela de datas — funções puras."""

from datetime import date, datetime
from typing import Any

import pyarrow as pa
import pytest

from carga.erros import ErroCarga
from carga.janela import datas_da_coluna, filtrar, inicio_da_janela, limites_da_janela

SNAPSHOT = date(2026, 10, 14)


@pytest.mark.parametrize(
    ("snapshot", "meses", "esperado"),
    [
        (date(2026, 10, 14), 3, date(2026, 8, 1)),
        (date(2026, 10, 1), 1, date(2026, 10, 1)),
        (date(2026, 2, 28), 3, date(2025, 12, 1)),  # vira o ano
        (date(2026, 1, 31), 12, date(2025, 2, 1)),
        (date(2026, 3, 15), 15, date(2025, 1, 1)),
    ],
)
def test_inicio_da_janela(snapshot: date, meses: int, esperado: date) -> None:
    assert inicio_da_janela(snapshot, meses) == esperado


def test_meses_invalido() -> None:
    with pytest.raises(ValueError, match=">= 1"):
        inicio_da_janela(SNAPSHOT, 0)


def _tabela(valores: list[Any], tipo: pa.DataType | None = None) -> pa.Table:
    return pa.table({"Data": pa.array(valores, tipo), "x": pa.array(range(len(valores)))})


@pytest.mark.parametrize(
    "valores",
    [
        ["2026-08-01", "2026-07-31"],
        ["2026-08-01 00:00:00", "2026-07-31 23:59:59.997"],
        ["01/08/2026", "31/07/2026 10:00"],
        ["2026/08/01", "2026/07/31"],
        [" 2026-08-01 ", "2026-07-31"],
    ],
)
def test_texto_em_varios_formatos(valores: list[str]) -> None:
    datas = datas_da_coluna(_tabela(valores), "Data")
    assert datas.to_pylist() == [date(2026, 8, 1), date(2026, 7, 31)]


def test_date_e_timestamp() -> None:
    assert datas_da_coluna(_tabela([date(2026, 8, 1)], pa.date32()), "Data").to_pylist() == [
        date(2026, 8, 1)
    ]
    ts = _tabela([datetime(2026, 8, 1, 15, 30)], pa.timestamp("us"))
    assert datas_da_coluna(ts, "Data").to_pylist() == [date(2026, 8, 1)]


def test_filtrar_mantem_janela_e_datas_vazias() -> None:
    tabela = _tabela(["2026-10-01", "2026-08-01", "2026-07-31", None, "", "2025-01-01"])

    filtrada, fora, inicio, fim = filtrar(tabela, "Data", SNAPSHOT, 3)

    assert (inicio, fim) == (date(2026, 8, 1), None)
    assert fora == 2
    assert filtrada.column("x").to_pylist() == [0, 1, 3, 4]


def test_formato_nao_reconhecido() -> None:
    with pytest.raises(ErroCarga, match=r"formato não reconhecido.*'ontem'"):
        datas_da_coluna(_tabela(["2026-08-01", "ontem"]), "Data")


def test_mistura_de_formatos_nao_passa() -> None:
    # cada coluna precisa ter um formato só (evita confundir dia e mês)
    with pytest.raises(ErroCarga, match="formato não reconhecido"):
        datas_da_coluna(_tabela(["2026-08-01", "01/08/2026"]), "Data")


def test_coluna_inexistente_ou_de_outro_tipo() -> None:
    with pytest.raises(ErroCarga, match="não está no arquivo"):
        datas_da_coluna(_tabela(["2026-08-01"]), "Outra")
    with pytest.raises(ErroCarga, match="precisa ser data, timestamp ou texto"):
        datas_da_coluna(_tabela([1.5], pa.float64()), "Data")


@pytest.mark.parametrize(
    ("snapshot", "meses", "esperado"),
    [
        (date(2026, 10, 16), 1, (date(2026, 9, 1), date(2026, 10, 1))),  # só setembro
        (date(2026, 10, 16), 3, (date(2026, 7, 1), date(2026, 10, 1))),
        (date(2026, 1, 5), 1, (date(2025, 12, 1), date(2026, 1, 1))),  # vira o ano
        (date(2026, 3, 1), 2, (date(2026, 1, 1), date(2026, 3, 1))),
    ],
)
def test_limites_meses_fechados(snapshot: date, meses: int, esperado: tuple[date, date]) -> None:
    assert limites_da_janela(snapshot, meses, meses_fechados=True) == esperado


def test_limites_sem_meses_fechados_nao_tem_fim() -> None:
    assert limites_da_janela(SNAPSHOT, 1) == (date(2026, 10, 1), None)


def test_filtrar_so_mes_anterior_fechado() -> None:
    tabela = _tabela(["2026-10-01", "2026-09-30", "2026-09-01", "2026-08-31", None])

    filtrada, fora, inicio, fim = filtrar(tabela, "Data", SNAPSHOT, 1, meses_fechados=True)

    assert (inicio, fim) == (date(2026, 9, 1), date(2026, 10, 1))
    assert fora == 2  # 01/10 (mês do snapshot) e 31/08 (antes da janela)
    assert filtrada.column("x").to_pylist() == [1, 2, 4]

"""Regras de data e de colunas — funções puras."""

from datetime import date, datetime

import pytest

from carga.erros import ErroCarga
from carga.validacao import (
    conferir_colunas,
    conferir_data_esperada,
    conferir_snapshot_novo,
    data_do_snapshot,
)

D = date(2026, 10, 13)


def test_data_unica() -> None:
    assert data_do_snapshot([D]) == D


def test_data_misturada() -> None:
    with pytest.raises(ErroCarga, match=r"misturado.*2026-10-12, 2026-10-13"):
        data_do_snapshot([D, date(2026, 10, 12)])


def test_arquivo_sem_linhas() -> None:
    with pytest.raises(ErroCarga, match="sem linhas"):
        data_do_snapshot([])


def test_linha_sem_data() -> None:
    with pytest.raises(ErroCarga, match="sem _data_snapshot"):
        data_do_snapshot([D, None])


def test_datetime_nao_vale_como_data() -> None:
    with pytest.raises(ErroCarga, match="tipo data"):
        data_do_snapshot([datetime(2026, 10, 13, 9, 0)])


def test_data_esperada() -> None:
    conferir_data_esperada(D, None)
    conferir_data_esperada(D, D)
    with pytest.raises(ErroCarga, match=r"2026-10-13.*DATA_ESPERADA é 2026-10-14"):
        conferir_data_esperada(D, date(2026, 10, 14))


@pytest.mark.parametrize(
    ("ultima_ok", "exigir", "falha"),
    [
        (None, True, False),  # primeira carga da fonte
        (date(2026, 10, 12), True, False),  # mais novo
        (D, True, True),  # igual: snapshot não foi renovado
        (date(2026, 10, 14), True, True),  # mais velho
        (D, False, False),  # sem exigir, igual passa
        (date(2026, 10, 14), False, False),
    ],
)
def test_snapshot_novo(ultima_ok: date | None, exigir: bool, falha: bool) -> None:
    if falha:
        with pytest.raises(ErroCarga, match="não foi renovado"):
            conferir_snapshot_novo(D, ultima_ok, exigir_snapshot_novo=exigir)
    else:
        conferir_snapshot_novo(D, ultima_ok, exigir_snapshot_novo=exigir)


def test_colunas() -> None:
    conferir_colunas(["Empresa", "Data", "X"], ["Empresa", "Data"])
    with pytest.raises(ErroCarga) as exc:
        conferir_colunas(["Empresa", "Data", "_data_snapshot"], ["Empresa", "Data", "Valor"])
    assert str(exc.value) == (
        "colunas faltando: Valor. Colunas do arquivo: Empresa, Data, _data_snapshot"
    )

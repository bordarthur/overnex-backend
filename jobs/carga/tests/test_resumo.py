"""Resumo mensal (carga/resumo.py) — sem banco."""

from datetime import date
from decimal import Decimal

import pyarrow as pa
import pytest

from carga.erros import ErroCarga
from carga.resumo import MODELOS, Total, calcular, modelo_da_tabela

VENDAS = MODELOS["f_vendas_"]


def _vendas(**extra: list[object]) -> pa.Table:
    colunas: dict[str, list[object]] = {
        "en_datemicao": [
            "28/08/2026 00:00:00",
            "01/09/2026 00:00:00",
            "01/09/2026 00:00:00",
            "15/09/2026 10:30:00",
            None,
        ],
        "en_VALTOT": [999.0, 600.1, 400.2, 1000.0, 5000.0],
        "en_numdoc": ["P0", "P1", "P1", "P2", "P3"],
        "en_codcli": ["C9", "C1", "C1", "C2", "C3"],
    }
    return pa.table(colunas | extra)


def test_modelo_pelo_prefixo() -> None:
    assert modelo_da_tabela("f_vendas_nilo_maia") is VENDAS
    assert modelo_da_tabela("f_estoque_nilo_maia") is None


def test_totais_por_mes_sem_empresa() -> None:
    totais = calcular(_vendas(), VENDAS, None)

    agosto, setembro = date(2026, 8, 1), date(2026, 9, 1)
    assert totais == [
        Total(None, "clientes", agosto, Decimal("1.00")),
        Total(None, "faturado", agosto, Decimal("999.00")),
        Total(None, "pedidos", agosto, Decimal("1.00")),
        # linha sem data fica de fora; float somado sem erro binário
        Total(None, "clientes", setembro, Decimal("2.00")),
        Total(None, "faturado", setembro, Decimal("2000.30")),
        Total(None, "pedidos", setembro, Decimal("2.00")),
    ]


def test_totais_por_empresa() -> None:
    dados = _vendas(EMPRESA=["A", "A", "B", "B", "B"])
    totais = calcular(dados, VENDAS, "EMPRESA")

    faturado = {(t.empresa, t.mes): t.valor for t in totais if t.medida == "faturado"}
    assert faturado == {
        ("A", date(2026, 8, 1)): Decimal("999.00"),
        ("A", date(2026, 9, 1)): Decimal("600.10"),
        ("B", date(2026, 9, 1)): Decimal("1400.20"),
    }


def test_data_em_formato_desconhecido_falha() -> None:
    dados = _vendas().set_column(
        0, "en_datemicao", pa.array(["set/2026", "x", "y", "z", None], pa.string())
    )
    with pytest.raises(ErroCarga, match="formato não reconhecido"):
        calcular(dados, VENDAS, None)

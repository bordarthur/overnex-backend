"""As colunas e os tipos de cada arquivo são os da tabela do mart (migrations)."""

from datetime import date

import pyarrow as pa
import pytest

from demo.tabelas import ESQUEMAS, esquema, montar
from tests.sql import colunas_do_mart


@pytest.mark.parametrize("tabela", sorted(ESQUEMAS))
def test_colunas_iguais_as_do_mart(tabela: str) -> None:
    mart = colunas_do_mart()
    assert tabela in mart, f"mart.{tabela} não existe nas migrations"
    esperado = {c: t for c, t in mart[tabela].items() if c != "_data_snapshot"}
    assert dict(ESQUEMAS[tabela]) == esperado


def test_tipos_arrow() -> None:
    s = esquema("f_vendas_pj")
    assert s.field("en_dataemissao").type == pa.date32()
    assert s.field("en_valortotal").type == pa.float64()
    assert s.field("EMPRESA").type == pa.string()
    assert s.field("_data_snapshot").type == pa.date32()


def test_montar_preenche_ausentes_e_data_do_snapshot() -> None:
    t = montar("f_orcamento_pj", [{"Valor": 10.0, "Id_Descritivo": "100.01.02"}], date(2026, 1, 2))
    assert t.column_names[-1] == "_data_snapshot"
    linha = t.to_pylist()[0]
    assert linha["Valor"] == 10.0
    assert linha["Cenario"] is None
    assert linha["_data_snapshot"] == date(2026, 1, 2)

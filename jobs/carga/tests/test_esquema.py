"""Tipos e DDL das tabelas de dados — funções puras."""

import pyarrow as pa
import pytest

from carga.esquema import ddl_tabela, nome_tabela_mart, tipo_postgres


@pytest.mark.parametrize(
    ("tipo", "esperado"),
    [
        (pa.string(), "text"),
        (pa.large_string(), "text"),
        (pa.float64(), "numeric"),
        (pa.float32(), "numeric"),
        (pa.int64(), "bigint"),
        (pa.int32(), "bigint"),
        (pa.decimal128(18, 2), "numeric(18, 2)"),
        (pa.bool_(), "boolean"),
        (pa.date32(), "date"),
        (pa.timestamp("us"), "timestamp"),
        (pa.timestamp("us", tz="UTC"), "timestamptz"),
        (pa.time64("us"), "time"),
        (pa.binary(), "bytea"),
    ],
)
def test_tipo_postgres(tipo: pa.DataType, esperado: str) -> None:
    assert tipo_postgres(tipo) == esperado


def test_tipo_sem_equivalente() -> None:
    with pytest.raises(ValueError, match="sem equivalente"):
        tipo_postgres(pa.list_(pa.int64()))


def test_nome_tabela_mart() -> None:
    assert nome_tabela_mart("dataflow/dbo_f_vendas_x/dbo_f_vendas_x.parquet") == "f_vendas_x"
    assert nome_tabela_mart("dataflow/fin_contas/fin_contas.parquet") == "fin_contas"
    with pytest.raises(ValueError, match="inválido"):
        nome_tabela_mart("dataflow/1abc/1abc.parquet")


def test_ddl_preserva_nomes_e_usa_colunas_de_sistema() -> None:
    campos: list[tuple[str, pa.DataType]] = [
        ("en_VALTOT", pa.float64()),
        ("Data", pa.string()),
        ('a"b', pa.int64()),
    ]
    esquema = pa.schema(campos)
    ddl = ddl_tabela("f_x", esquema)
    assert ddl.startswith("create table mart.f_x (\n    _linha      bigint not null,\n")
    assert '"en_VALTOT" numeric' in ddl
    assert '"Data"      text' in ddl
    assert '"a""b"      bigint' in ddl
    assert "primary key (_cliente_id, _linha)" in ddl
    assert "references cfg.empresa (id, cliente_id)" in ddl
    assert ddl.endswith("select mart.preparar_tabela_dados('f_x');\n")


def test_ddl_recusa_colisao_com_colunas_de_sistema() -> None:
    with pytest.raises(ValueError, match="colidem"):
        ddl_tabela("f_x", pa.schema([("_linha", pa.int64())]))

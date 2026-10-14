"""Tabelas de dados do mart: tipos e DDL a partir do esquema do Parquet.

Cada Parquet de dados vira uma tabela em mart com AS MESMAS colunas (mesmos nomes,
entre aspas, e tipos equivalentes) e três colunas de sistema:

    _linha       posição da linha no arquivo (1, 2, …): ordem estável e paginação
    _cliente_id  dono dos dados (RLS por cliente)
    _empresa_id  empresa, quando a fonte tem coluna de empresa (RLS por empresa)

Usado pelo inventário (SQL sugerido) e para escrever as migrations.
"""

import re

import pyarrow as pa

COLUNAS_SISTEMA = ("_linha", "_cliente_id", "_empresa_id")
NOME_TABELA = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def tipo_postgres(tipo: pa.DataType) -> str:
    """Tipo do Postgres equivalente ao do Parquet.

    Ponto flutuante vira numeric: o valor chega pela representação decimal mais curta
    do double e as somas no Postgres ficam exatas.
    """
    if pa.types.is_string(tipo) or pa.types.is_large_string(tipo):
        return "text"
    if pa.types.is_boolean(tipo):
        return "boolean"
    if pa.types.is_integer(tipo):
        return "bigint"
    if pa.types.is_floating(tipo):
        return "numeric"
    if isinstance(tipo, pa.Decimal128Type | pa.Decimal256Type):
        return f"numeric({tipo.precision}, {tipo.scale})"
    if pa.types.is_date(tipo):
        return "date"
    if isinstance(tipo, pa.TimestampType):
        return "timestamptz" if tipo.tz else "timestamp"
    if pa.types.is_time(tipo):
        return "time"
    if pa.types.is_binary(tipo) or pa.types.is_large_binary(tipo):
        return "bytea"
    raise ValueError(f"tipo do Parquet sem equivalente definido: {tipo}")


def nome_tabela_mart(caminho_bucket: str) -> str:
    """'dataflow/dbo_f_vendas_x/dbo_f_vendas_x.parquet' → 'f_vendas_x' (sem o schema dbo_)."""
    arquivo = caminho_bucket.rsplit("/", 1)[-1].removesuffix(".parquet")
    nome = arquivo.removeprefix("dbo_")
    if not NOME_TABELA.match(nome):
        raise ValueError(f"nome de tabela inválido para o mart: {nome!r}")
    return nome


def _ident(nome: str) -> str:
    return '"' + nome.replace('"', '""') + '"'


def ddl_tabela(nome: str, esquema: pa.Schema) -> str:
    """create table mart.<nome> + preparo (RLS, políticas, grants e índice)."""
    if not NOME_TABELA.match(nome):
        raise ValueError(f"nome de tabela inválido para o mart: {nome!r}")
    repetidas = [c for c in esquema.names if c in COLUNAS_SISTEMA]
    if repetidas:
        raise ValueError(f"colunas do arquivo colidem com as de sistema: {repetidas}")

    largura = max(len(_ident(c)) for c in esquema.names) if esquema.names else 0
    colunas = [
        f"    {_ident(campo.name).ljust(largura)} {tipo_postgres(campo.type)}" for campo in esquema
    ]
    return (
        f"create table mart.{nome} (\n"
        "    _linha      bigint not null,\n"
        "    _cliente_id uuid   not null references cfg.cliente (id),\n"
        "    _empresa_id uuid,\n" + ",\n".join(colunas) + ",\n"
        "    primary key (_cliente_id, _linha),\n"
        "    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)\n"
        ");\n"
        f"select mart.preparar_tabela_dados('{nome}');\n"
    )

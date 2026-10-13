"""Carga da fonte 'lancamento': Parquet → mart.lancamento.

1. baixa o Parquet da fonte;
2. valida a data do snapshot (falhou → para sem tocar no mart);
3. valida as colunas e os tipos;
4. em UMA transação: temp table + COPY, Empresa → empresa_id por cfg.mapa_empresa,
   apaga TODAS as empresas mapeadas do cliente e insere a agregação mensal;
5. commit só se tudo deu certo. Até o commit, quem está no painel vê o dado anterior.

TODO(regra): aplicar cfg.regra_lancamento (reclassificar/excluir) antes da agregação.
"""

import io
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from carga.config import Settings
from carga.db import Conexao
from carga.erros import ErroCarga
from carga.fontes import Fonte
from carga.validacao import (
    COLUNA_DATA_SNAPSHOT,
    conferir_colunas,
    conferir_data_esperada,
    conferir_snapshot_novo,
    data_do_snapshot,
)

COLUNAS = ("Empresa", "Data", "Id_Descritivo", "Valor")
LOTE_COPY = 50_000
MAX_LISTADOS = 20

Baixar = Callable[[str], bytes]


@dataclass
class Progresso:
    """O que já se sabe da fonte; vai para mart.carga_fonte mesmo se der erro depois."""

    data_snapshot: date | None = None
    linhas_lidas: int | None = None
    linhas_gravadas: int | None = None


def abrir_parquet(dados: bytes) -> pq.ParquetFile:
    try:
        return pq.ParquetFile(io.BytesIO(dados))
    except (pa.ArrowInvalid, OSError) as exc:
        raise ErroCarga(f"o arquivo não é um Parquet válido: {exc}") from exc


def ler_data_snapshot(arquivo: pq.ParquetFile) -> date:
    if COLUNA_DATA_SNAPSHOT not in arquivo.schema_arrow.names:
        raise ErroCarga(f"o arquivo não tem a coluna {COLUNA_DATA_SNAPSHOT}")
    coluna = arquivo.read(columns=[COLUNA_DATA_SNAPSHOT]).column(0)
    if not pa.types.is_date(coluna.type):
        raise ErroCarga(f"{COLUNA_DATA_SNAPSHOT} precisa ser do tipo data, veio {coluna.type}")
    return data_do_snapshot(pc.unique(coluna).to_pylist())


def preparar(tabela: pa.Table) -> pa.Table:
    """Confere tipos e nulos das 4 colunas e devolve a tabela pronta para o COPY.

    Valor não é convertido aqui: segue como Decimal/float/int e vira numeric no Postgres.
    """
    erros: list[str] = []

    empresa = tabela.column("Empresa")
    if not (pa.types.is_string(empresa.type) or pa.types.is_large_string(empresa.type)):
        erros.append(f"Empresa precisa ser texto, veio {empresa.type}")

    data = tabela.column("Data")
    if pa.types.is_timestamp(data.type):
        data = pc.cast(data, pa.date32(), safe=False)
    elif not pa.types.is_date(data.type):
        erros.append(f"Data precisa ser data ou timestamp, veio {data.type}")

    id_descritivo = tabela.column("Id_Descritivo")
    tipo_id = id_descritivo.type
    if not (
        pa.types.is_integer(tipo_id)
        or (isinstance(tipo_id, pa.Decimal128Type | pa.Decimal256Type) and tipo_id.scale == 0)
    ):
        erros.append(f"Id_Descritivo precisa ser inteiro, veio {tipo_id}")

    valor = tabela.column("Valor")
    tipo_valor = valor.type
    if pa.types.is_floating(tipo_valor):
        if pc.any(pc.invert(pc.is_finite(valor))).as_py():
            erros.append("Valor tem NaN ou infinito")
    elif not (pa.types.is_decimal(tipo_valor) or pa.types.is_integer(tipo_valor)):
        erros.append(f"Valor precisa ser numérico, veio {tipo_valor}")

    nulos = [
        f"{nome} ({tabela.column(nome).null_count})"
        for nome in COLUNAS
        if tabela.column(nome).null_count
    ]
    if nulos:
        erros.append(f"valores nulos em: {', '.join(nulos)}")

    if erros:
        raise ErroCarga("; ".join(erros))
    return pa.table(
        {"Empresa": empresa, "Data": data, "Id_Descritivo": id_descritivo, "Valor": valor}
    )


def ultima_carga_ok(conn: Conexao, fonte_id: int) -> date | None:
    linha = conn.execute(
        """
        select data_snapshot
          from mart.carga_fonte
         where fonte_id = %s and status = 'ok'
         order by inicio desc, id desc
         limit 1
        """,
        (fonte_id,),
    ).fetchone()
    return linha[0] if linha else None


def _listar(valores: list[str]) -> str:
    texto = ", ".join(repr(v) for v in valores[:MAX_LISTADOS])
    if len(valores) > MAX_LISTADOS:
        texto += f" (e mais {len(valores) - MAX_LISTADOS})"
    return texto


def gravar(conn: Conexao, fonte: Fonte, tabela: pa.Table) -> int:
    """Troca os lançamentos do cliente em UMA transação. Devolve as linhas gravadas no mart."""
    with conn.transaction():
        conn.execute(
            """
            create temp table tmp_lancamento (
                empresa       text   not null,
                data          date   not null,
                id_descritivo bigint not null,
                valor         text   not null
            ) on commit drop
            """
        )
        with (
            conn.cursor() as cur,
            cur.copy(
                "copy tmp_lancamento (empresa, data, id_descritivo, valor) from stdin"
            ) as copy,
        ):
            for lote in tabela.to_batches(max_chunksize=LOTE_COPY):
                colunas = [lote.column(i).to_pylist() for i in range(lote.num_columns)]
                for linha in zip(*colunas, strict=True):
                    copy.write_row(linha)

        sem_mapa = [
            r[0]
            for r in conn.execute(
                """
                select distinct t.empresa
                  from tmp_lancamento t
                 where not exists (
                        select 1 from cfg.mapa_empresa m
                         where m.cliente_id = %s and m.empresa_origem = t.empresa)
                 order by 1
                """,
                (fonte.cliente_id,),
            ).fetchall()
        ]
        if sem_mapa:
            raise ErroCarga(
                f"Empresa sem mapeamento em cfg.mapa_empresa para o cliente {fonte.cliente_id}: "
                f"{_listar(sem_mapa)}"
            )

        # TODAS as empresas mapeadas do cliente, inclusive as que sumiram do arquivo.
        conn.execute(
            """
            delete from mart.lancamento
             where empresa_id in (select empresa_id from cfg.mapa_empresa where cliente_id = %s)
            """,
            (fonte.cliente_id,),
        )

        # Valor vira numeric no Postgres; a soma nunca é feita em float.
        return conn.execute(
            """
            insert into mart.lancamento (empresa_id, mes, id_descritivo, valor)
            select m.empresa_id,
                   date_trunc('month', t.data)::date,
                   t.id_descritivo::integer,
                   round(sum(t.valor::numeric), 2)
              from tmp_lancamento t
              join cfg.mapa_empresa m
                on m.cliente_id = %s and m.empresa_origem = t.empresa
             group by 1, 2, 3
            """,
            (fonte.cliente_id,),
        ).rowcount


def carregar(
    conn: Conexao,
    settings: Settings,
    fonte: Fonte,
    baixar: Baixar,
    progresso: Progresso,
) -> None:
    arquivo = abrir_parquet(baixar(fonte.caminho_bucket))
    progresso.linhas_lidas = arquivo.metadata.num_rows

    # data do snapshot: falhou → para sem tocar no mart
    data = ler_data_snapshot(arquivo)
    progresso.data_snapshot = data
    conferir_data_esperada(data, settings.data_esperada)
    conferir_snapshot_novo(
        data,
        ultima_carga_ok(conn, fonte.id),
        exigir_snapshot_novo=settings.exigir_snapshot_novo,
    )

    conferir_colunas(arquivo.schema_arrow.names, COLUNAS)
    tabela = preparar(arquivo.read(columns=list(COLUNAS)))

    progresso.linhas_gravadas = gravar(conn, fonte, tabela)

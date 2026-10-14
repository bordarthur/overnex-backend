"""O que toda fonte faz com o arquivo antes de tocar no mart: baixar, abrir e validar a data."""

import io
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
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
    conferir_data_esperada,
    conferir_snapshot_novo,
    data_do_snapshot,
)

MAX_LISTADOS = 20

Baixar = Callable[[str], bytes]


@dataclass
class Progresso:
    """O que já se sabe da fonte; vai para mart.carga_fonte mesmo se der erro depois."""

    data_snapshot: date | None = None
    linhas_lidas: int | None = None
    linhas_gravadas: int | None = None
    # avisos de uma carga que deu certo (ex.: coluna nova ignorada)
    avisos: list[str] = field(default_factory=list)


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


def baixar_e_validar_data(
    conn: Conexao,
    settings: Settings,
    fonte: Fonte,
    baixar: Baixar,
    progresso: Progresso,
) -> pq.ParquetFile:
    """Baixa e abre o arquivo da fonte e aplica as regras de data. Falhou → nada no mart."""
    arquivo = abrir_parquet(baixar(fonte.caminho_bucket))
    progresso.linhas_lidas = arquivo.metadata.num_rows

    data = ler_data_snapshot(arquivo)
    progresso.data_snapshot = data
    conferir_data_esperada(data, settings.data_esperada)
    conferir_snapshot_novo(
        data,
        ultima_carga_ok(conn, fonte.id),
        exigir_snapshot_novo=settings.exigir_snapshot_novo,
    )
    return arquivo


def listar_valores(valores: Sequence[object]) -> str:
    texto = ", ".join(repr(v) for v in valores[:MAX_LISTADOS])
    if len(valores) > MAX_LISTADOS:
        texto += f" (e mais {len(valores) - MAX_LISTADOS})"
    return texto

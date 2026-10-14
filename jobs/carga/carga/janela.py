"""Janela de datas: só os últimos N meses de uma coluna entram no mart.

Funções puras. A referência é o _data_snapshot do arquivo, nunca "hoje": a mesma
entrada dá sempre o mesmo resultado, em qualquer horário ou fuso.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc

from carga.erros import ErroCarga

# Formatos aceitos para datas em texto, comparados com os 10 primeiros caracteres
# (a hora, se houver, é ignorada): 2026-10-14, 2026-10-14 00:00:00, 14/10/2026 08:30…
FORMATOS = ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d")


def inicio_da_janela(data_snapshot: date, meses: int) -> date:
    """Dia 1 do mês do snapshot menos (meses - 1). Ex.: 14/10/2026 e 3 → 01/08/2026."""
    if meses < 1:
        raise ValueError("meses precisa ser >= 1")
    indice = data_snapshot.year * 12 + (data_snapshot.month - 1) - (meses - 1)
    return date(indice // 12, indice % 12 + 1, 1)


def _texto_para_data(coluna: pa.ChunkedArray[Any], nome: str) -> pa.ChunkedArray[Any]:
    limpo = pc.utf8_trim_whitespace(coluna)
    # texto vazio vale como data vazia
    vazio = pc.equal(limpo, pa.scalar("", pa.string()))
    limpo = pc.if_else(vazio, pa.scalar(None, pa.string()), limpo)
    preenchidas = len(limpo) - limpo.null_count
    dez = pc.utf8_slice_codeunits(limpo, 0, 10)

    for formato in FORMATOS:
        datas = pc.strptime(dez, format=formato, unit="s", error_is_null=True)
        if len(datas) - datas.null_count == preenchidas:
            convertidas: pa.ChunkedArray[Any] = pc.cast(datas, pa.date32())
            return convertidas

    exemplos = [v for v in pc.unique(limpo).to_pylist() if v is not None][:3]
    raise ErroCarga(
        f"a coluna de data {nome!r} tem valores em formato não reconhecido "
        f"(aceitos: AAAA-MM-DD, DD/MM/AAAA, AAAA/MM/DD). Exemplos: {exemplos}"
    )


def datas_da_coluna(tabela: pa.Table, nome: str) -> pa.ChunkedArray[Any]:
    """A coluna convertida para date32 (nulos continuam nulos)."""
    if nome not in tabela.column_names:
        raise ErroCarga(
            f"a coluna de data {nome!r} (cfg.fonte_carga.coluna_data) não está no arquivo"
        )
    coluna = tabela.column(nome)
    tipo = coluna.type
    if pa.types.is_date(tipo):
        return pc.cast(coluna, pa.date32())
    if pa.types.is_timestamp(tipo):
        return pc.cast(coluna, pa.date32(), safe=False)
    if pa.types.is_string(tipo) or pa.types.is_large_string(tipo):
        return _texto_para_data(coluna, nome)
    raise ErroCarga(f"a coluna de data {nome!r} precisa ser data, timestamp ou texto; veio {tipo}")


def filtrar(
    tabela: pa.Table, coluna: str, data_snapshot: date, meses: int
) -> tuple[pa.Table, int, date]:
    """Mantém as linhas com data >= início da janela ou com data vazia.

    Devolve (tabela filtrada, linhas descartadas, início da janela).
    """
    inicio = inicio_da_janela(data_snapshot, meses)
    datas = datas_da_coluna(tabela, coluna)
    manter = pc.or_kleene(
        pc.is_null(datas), pc.greater_equal(datas, pa.scalar(inicio, pa.date32()))
    )
    filtrada = tabela.filter(manter)
    return filtrada, tabela.num_rows - filtrada.num_rows, inicio

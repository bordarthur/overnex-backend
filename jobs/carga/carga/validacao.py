"""Validações do arquivo. Funções puras: nenhuma usa "hoje" nem fuso horário."""

from collections.abc import Iterable, Sequence
from datetime import date, datetime

from carga.erros import ErroCarga

COLUNA_DATA_SNAPSHOT = "_data_snapshot"


def data_do_snapshot(valores_distintos: Iterable[date | None]) -> date:
    """A data única do arquivo. Todas as linhas precisam ter o MESMO _data_snapshot."""
    valores = list(valores_distintos)
    if not valores:
        raise ErroCarga("arquivo sem linhas: nada a carregar (mart mantido)")
    if any(v is None for v in valores):
        raise ErroCarga(f"há linhas sem {COLUNA_DATA_SNAPSHOT}")
    # datetime é subclasse de date; aqui só vale data pura
    if any(isinstance(v, datetime) or not isinstance(v, date) for v in valores):
        raise ErroCarga(f"{COLUNA_DATA_SNAPSHOT} precisa ser do tipo data")
    datas = sorted({v for v in valores if v is not None})
    if len(datas) > 1:
        lista = ", ".join(d.isoformat() for d in datas[:10])
        raise ErroCarga(f"{COLUNA_DATA_SNAPSHOT} misturado no arquivo: {lista}")
    return datas[0]


def conferir_data_esperada(data_snapshot: date, data_esperada: date | None) -> None:
    if data_esperada is not None and data_snapshot != data_esperada:
        raise ErroCarga(
            f"{COLUNA_DATA_SNAPSHOT} do arquivo é {data_snapshot.isoformat()}, "
            f"mas DATA_ESPERADA é {data_esperada.isoformat()}"
        )


def conferir_snapshot_novo(
    data_snapshot: date, ultima_ok: date | None, *, exigir_snapshot_novo: bool
) -> None:
    """Com EXIGIR_SNAPSHOT_NOVO, o arquivo precisa ser mais novo que a última carga ok da fonte.

    Arquivo que não foi renovado = o snapshot daquela tabela falhou hoje.
    """
    if not exigir_snapshot_novo or ultima_ok is None:
        return
    if data_snapshot <= ultima_ok:
        raise ErroCarga(
            f"snapshot não foi renovado: {COLUNA_DATA_SNAPSHOT} {data_snapshot.isoformat()} "
            f"não é mais novo que o da última carga ok ({ultima_ok.isoformat()})"
        )


def conferir_colunas(presentes: Sequence[str], esperadas: Sequence[str]) -> None:
    faltando = [c for c in esperadas if c not in presentes]
    if faltando:
        raise ErroCarga(
            f"colunas faltando: {', '.join(faltando)}. "
            f"Colunas do arquivo: {', '.join(presentes) or '(nenhuma)'}"
        )

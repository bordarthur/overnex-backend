"""Fontes da carga, lidas de cfg.fonte_carga."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from psycopg.rows import class_row

from carga.db import Conexao

TIPOS_SUPORTADOS = frozenset({"lancamento"})


@dataclass(frozen=True)
class Fonte:
    id: int
    cliente_id: UUID
    tipo: str
    caminho_bucket: str


def listar_fontes_ativas(conn: Conexao) -> list[Fonte]:
    with conn.cursor(row_factory=class_row(Fonte)) as cur:
        return cur.execute(
            """
            select id, cliente_id, tipo, caminho_bucket
              from cfg.fonte_carga
             where ativo
             order by id
            """
        ).fetchall()


def fontes_em_conflito(fontes: Sequence[Fonte]) -> set[int]:
    """Ids das fontes que dividem (cliente, tipo) com outra fonte ativa.

    A carga de 'lancamento' substitui TODAS as empresas mapeadas do cliente; com
    duas fontes do mesmo tipo, a segunda apagaria a primeira. Nenhuma delas roda.
    """
    contagem = Counter((f.cliente_id, f.tipo) for f in fontes)
    return {f.id for f in fontes if contagem[(f.cliente_id, f.tipo)] > 1}

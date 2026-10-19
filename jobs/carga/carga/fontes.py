"""Fontes da carga, lidas de cfg.fonte_carga."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from psycopg.rows import class_row

from carga.db import Conexao

# lancamento: formato agregado (Empresa, Data, Id_Descritivo, Valor) → mart.lancamento
# tabela:     cópia do Parquet com as mesmas colunas → mart.<tabela_destino>
TIPOS_SUPORTADOS = frozenset({"lancamento", "tabela"})


@dataclass(frozen=True)
class Fonte:
    id: int
    cliente_id: UUID
    tipo: str
    caminho_bucket: str
    # só no tipo tabela
    tabela_destino: str | None = None
    # coluna do arquivo com a empresa (vira _empresa_id); None = dados do cliente inteiro
    coluna_empresa: str | None = None
    # janela de datas: só os últimos N meses desta coluna entram (ambos None = tudo)
    coluna_data: str | None = None
    meses_retidos: int | None = None
    # True = só meses fechados: a janela termina no mês anterior ao do snapshot
    meses_fechados: bool = False


def listar_fontes_ativas(conn: Conexao) -> list[Fonte]:
    with conn.cursor(row_factory=class_row(Fonte)) as cur:
        return cur.execute(
            """
            select id, cliente_id, tipo, caminho_bucket, tabela_destino, coluna_empresa,
                   coluna_data, meses_retidos, meses_fechados
              from cfg.fonte_carga
             where ativo
             order by id
            """
        ).fetchall()


def fontes_em_conflito(fontes: Sequence[Fonte]) -> set[int]:
    """Ids das fontes que gravariam no mesmo lugar que outra fonte ativa do cliente.

    Cada carga substitui todos os dados do cliente no destino (mart.lancamento ou
    mart.<tabela_destino>); com duas fontes no mesmo destino, a segunda apagaria a
    primeira. Nenhuma delas roda.
    """
    contagem = Counter(destino(f) for f in fontes)
    return {f.id for f in fontes if contagem[destino(f)] > 1}


def destino(fonte: Fonte) -> tuple[UUID, str]:
    if fonte.tipo == "tabela":
        return fonte.cliente_id, f"mart.{fonte.tabela_destino}"
    return fonte.cliente_id, fonte.tipo

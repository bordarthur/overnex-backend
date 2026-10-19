"""Resumo mensal: totais por mês de uma tabela de movimento, gravados em mart.resumo_mensal.

Os totais saem do arquivo INTEIRO, antes da janela de datas: a tabela do mart pode guardar
só 1 mês, mas a Visão geral pede tendência de 6 meses e acumulado do ano.

As medidas de cada tipo de tabela ficam em MODELOS, pelo prefixo do nome (as colunas são
as do DW padrão). Tabela sem modelo não gera resumo.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import pyarrow as pa
import pyarrow.compute as pc

from carga import janela
from carga.arquivo import listar_valores
from carga.db import Conexao


@dataclass(frozen=True)
class Medida:
    nome: str
    coluna: str
    agregacao: Literal["sum", "count_distinct"]


@dataclass(frozen=True)
class Modelo:
    coluna_data: str
    medidas: tuple[Medida, ...]

    @property
    def colunas(self) -> list[str]:
        return [self.coluna_data, *(m.coluna for m in self.medidas)]


MODELOS: Mapping[str, Modelo] = {
    "f_vendas_": Modelo(
        coluna_data="en_datemicao",
        medidas=(
            Medida("faturado", "en_VALTOT", "sum"),
            Medida("pedidos", "en_numdoc", "count_distinct"),
            Medida("clientes", "en_codcli", "count_distinct"),
        ),
    ),
}


@dataclass(frozen=True)
class Total:
    # valor da coluna de empresa no arquivo; None = tabela sem empresa
    empresa: str | None
    medida: str
    mes: date
    valor: Decimal


def modelo_da_tabela(tabela: str) -> Modelo | None:
    for prefixo, modelo in MODELOS.items():
        if tabela.startswith(prefixo):
            return modelo
    return None


def _decimal(valor: object) -> Decimal:
    # str() evita levar o erro binário do float para o Decimal
    return Decimal(str(valor)).quantize(Decimal("0.01"))


def calcular(dados: pa.Table, modelo: Modelo, coluna_empresa: str | None) -> list[Total]:
    """Totais por mês (e por empresa, se houver). Linhas sem data ficam de fora."""
    datas = janela.datas_da_coluna(dados, modelo.coluna_data)
    colunas: dict[str, Any] = {"_ano": pc.year(datas), "_mes": pc.month(datas)}
    chaves = ["_ano", "_mes"]
    if coluna_empresa:
        colunas["_empresa"] = dados.column(coluna_empresa)
        chaves.append("_empresa")
    for m in modelo.medidas:
        colunas[m.coluna] = dados.column(m.coluna)

    tabela = pa.table(colunas).filter(pc.is_valid(datas))
    agregado = tabela.group_by(chaves).aggregate([(m.coluna, m.agregacao) for m in modelo.medidas])

    totais: list[Total] = []
    for linha in agregado.to_pylist():
        mes = date(linha["_ano"], linha["_mes"], 1)
        empresa = linha.get("_empresa") if coluna_empresa else None
        for m in modelo.medidas:
            valor = linha[f"{m.coluna}_{m.agregacao}"]
            totais.append(Total(empresa, m.nome, mes, _decimal(valor or 0)))
    return sorted(totais, key=lambda t: (t.mes, t.empresa or "", t.medida))


def tabela_existe(conn: Conexao) -> bool:
    """False enquanto a migration 8 não foi aplicada: a carga segue sem o resumo."""
    linha = conn.execute("select to_regclass('mart.resumo_mensal') is not null").fetchone()
    return bool(linha and linha[0])


def gravar(
    conn: Conexao,
    cliente_id: UUID,
    tabela: str,
    totais: Sequence[Total],
    data_snapshot: date,
    avisos: list[str],
) -> int:
    """Substitui o resumo do cliente nesta tabela. Rodar dentro da transação da carga."""
    empresas: dict[str, UUID] = {
        r[0]: r[1]
        for r in conn.execute(
            "select empresa_origem, empresa_id from cfg.mapa_empresa where cliente_id = %s",
            (cliente_id,),
        ).fetchall()
    }
    # dois valores do arquivo podem apontar para a mesma empresa: os totais se somam
    somados: dict[tuple[UUID | None, str, date], Decimal] = {}
    sem_mapa: set[str] = set()
    for t in totais:
        empresa_id = None
        if t.empresa is not None:
            empresa_id = empresas.get(t.empresa)
            if empresa_id is None:
                # meses fora da janela podem ter empresas que a tabela já não traz
                sem_mapa.add(t.empresa)
                continue
        chave = (empresa_id, t.medida, t.mes)
        somados[chave] = somados.get(chave, Decimal(0)) + t.valor
    linhas = [
        (cliente_id, empresa_id, tabela, medida, mes, valor, data_snapshot)
        for (empresa_id, medida, mes), valor in somados.items()
    ]
    if sem_mapa:
        avisos.append(
            "resumo mensal sem as empresas sem mapeamento em cfg.mapa_empresa: "
            f"{listar_valores(sorted(sem_mapa))}"
        )

    conn.execute(
        "delete from mart.resumo_mensal where cliente_id = %s and tabela = %s",
        (cliente_id, tabela),
    )
    with conn.cursor() as cur:
        cur.executemany(
            """
            insert into mart.resumo_mensal
                (cliente_id, empresa_id, tabela, medida, mes, valor, data_snapshot)
            values (%s, %s, %s, %s, %s, %s, %s)
            """,
            linhas,
        )
    return len(linhas)

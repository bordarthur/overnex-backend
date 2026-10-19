"""Resumo mensal: totais por mês de uma tabela de movimento, gravados em mart.resumo_mensal.

Os totais saem do arquivo INTEIRO, antes da janela de datas: a tabela do mart pode guardar
só 1 mês, mas a Visão geral pede tendência de 6 meses e acumulado do ano.

As medidas de cada tipo de tabela ficam em MODELOS, pelo prefixo do nome (as colunas são
as do DW padrão). Tabela sem modelo não gera resumo. Uma medida com `por` é aberta pelos
valores dessa coluna (mart.resumo_mensal.dimensao, migration 10); valor vazio na coluna
vira SEM_DIMENSAO.
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
    # count_distinct com mais de uma coluna conta as combinações distintas
    colunas: tuple[str, ...]
    agregacao: Literal["sum", "count_distinct"]
    # coluna de abertura (ex.: en_ramo); None = total do mês
    por: str | None = None


@dataclass(frozen=True)
class Modelo:
    coluna_data: str
    medidas: tuple[Medida, ...]

    @property
    def colunas(self) -> list[str]:
        return list(
            dict.fromkeys(
                [
                    self.coluna_data,
                    *(c for m in self.medidas for c in m.colunas),
                    *(m.por for m in self.medidas if m.por),
                ]
            )
        )


MODELOS: Mapping[str, Modelo] = {
    "f_vendas_": Modelo(
        coluna_data="en_datemicao",
        medidas=(
            Medida("faturado", ("en_VALTOT",), "sum"),
            Medida("pedidos", ("en_numdoc",), "count_distinct"),
            Medida("clientes", ("en_codcli",), "count_distinct"),
            # pares cliente × produto: ÷ clientes = produtos distintos por cliente
            Medida("clientes_produtos", ("en_codcli", "en_codigo"), "count_distinct"),
            # en_precus é o custo total da linha (não o unitário)
            Medida("custo", ("en_precus",), "sum"),
            # CMV por ramo do cliente (seção Custos)
            Medida("faturado_ramo", ("en_VALTOT",), "sum", por="en_ramo"),
            Medida("custo_ramo", ("en_precus",), "sum", por="en_ramo"),
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
    # valor da coluna de abertura; "" = total do mês
    dimensao: str = ""


SEM_DIMENSAO = "(vazio)"


def modelo_da_tabela(tabela: str) -> Modelo | None:
    for prefixo, modelo in MODELOS.items():
        if tabela.startswith(prefixo):
            return modelo
    return None


def _decimal(valor: object) -> Decimal:
    # str() evita levar o erro binário do float para o Decimal
    return Decimal(str(valor)).quantize(Decimal("0.01"))


def _coluna_da_medida(dados: pa.Table, m: Medida) -> Any:
    if len(m.colunas) == 1:
        return dados.column(m.colunas[0])
    # combinação das colunas em um texto só; nulo em qualquer uma = nulo (não conta)
    partes: list[Any] = [pc.cast(dados.column(c), pa.string()) for c in m.colunas]
    # separador que não aparece em código de cliente nem de produto
    return pc.binary_join_element_wise(*partes, "\x1f")


def calcular(dados: pa.Table, modelo: Modelo, coluna_empresa: str | None) -> list[Total]:
    """Totais por mês (e por empresa, se houver). Linhas sem data ficam de fora."""
    datas = janela.datas_da_coluna(dados, modelo.coluna_data)
    validas = pc.is_valid(datas)
    totais: list[Total] = []
    # um group_by por coluna de abertura (None = sem abertura)
    for por in dict.fromkeys(m.por for m in modelo.medidas):
        medidas = [m for m in modelo.medidas if m.por == por]
        colunas: dict[str, Any] = {"_ano": pc.year(datas), "_mes": pc.month(datas)}
        chaves = ["_ano", "_mes"]
        if coluna_empresa:
            colunas["_empresa"] = dados.column(coluna_empresa)
            chaves.append("_empresa")
        if por:
            texto: Any = pc.cast(dados.column(por), pa.string())
            colunas["_dim"] = pc.fill_null(texto, SEM_DIMENSAO)
            chaves.append("_dim")
        for m in medidas:
            colunas[f"_m_{m.nome}"] = _coluna_da_medida(dados, m)

        agregado = (
            pa.table(colunas)
            .filter(validas)
            .group_by(chaves)
            .aggregate([(f"_m_{m.nome}", m.agregacao) for m in medidas])
        )
        for linha in agregado.to_pylist():
            mes = date(linha["_ano"], linha["_mes"], 1)
            empresa = linha.get("_empresa") if coluna_empresa else None
            dimensao = (linha["_dim"] or SEM_DIMENSAO) if por else ""
            for m in medidas:
                valor = linha[f"_m_{m.nome}_{m.agregacao}"]
                totais.append(Total(empresa, m.nome, mes, _decimal(valor or 0), dimensao))
    return sorted(totais, key=lambda t: (t.mes, t.empresa or "", t.medida, t.dimensao))


def _tem_dimensao(conn: Conexao) -> bool:
    """False enquanto a migration 10 não foi aplicada."""
    linha = conn.execute(
        """
        select exists (
            select 1 from information_schema.columns
             where table_schema = 'mart' and table_name = 'resumo_mensal'
               and column_name = 'dimensao'
        )
        """
    ).fetchone()
    return bool(linha and linha[0])


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
    com_dimensao = _tem_dimensao(conn)
    if not com_dimensao and any(t.dimensao for t in totais):
        avisos.append(
            "resumo mensal sem as medidas abertas (ex.: por ramo): "
            "falta mart.resumo_mensal.dimensao (migration 10)"
        )
        totais = [t for t in totais if not t.dimensao]
    # dois valores do arquivo podem apontar para a mesma empresa: os totais se somam
    somados: dict[tuple[UUID | None, str, str, date], Decimal] = {}
    sem_mapa: set[str] = set()
    for t in totais:
        empresa_id = None
        if t.empresa is not None:
            empresa_id = empresas.get(t.empresa)
            if empresa_id is None:
                # meses fora da janela podem ter empresas que a tabela já não traz
                sem_mapa.add(t.empresa)
                continue
        chave = (empresa_id, t.medida, t.dimensao, t.mes)
        somados[chave] = somados.get(chave, Decimal(0)) + t.valor
    linhas: list[tuple[object, ...]]
    if com_dimensao:
        sql_insert = (
            "insert into mart.resumo_mensal (cliente_id, empresa_id, tabela, medida, dimensao,"
            " mes, valor, data_snapshot) values (%s, %s, %s, %s, %s, %s, %s, %s)"
        )
        linhas = [
            (cliente_id, empresa_id, tabela, medida, dimensao, mes, valor, data_snapshot)
            for (empresa_id, medida, dimensao, mes), valor in somados.items()
        ]
    else:
        sql_insert = (
            "insert into mart.resumo_mensal (cliente_id, empresa_id, tabela, medida,"
            " mes, valor, data_snapshot) values (%s, %s, %s, %s, %s, %s, %s)"
        )
        linhas = [
            (cliente_id, empresa_id, tabela, medida, mes, valor, data_snapshot)
            for (empresa_id, medida, _dimensao, mes), valor in somados.items()
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
        cur.executemany(sql_insert, linhas)
    return len(linhas)

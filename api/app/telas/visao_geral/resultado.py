"""Seção Resultado da Visão geral. REGRAS PROVISÓRIAS: confirmar com o negócio (ver TODO.md).

Lê os lançamentos gerenciais (f_lancamentos_*: data de competência, valor e Id_Descritivo)
e o orçamento (f_orcamento_*, menos os orçamentos comercial e de ticket) que o cliente tem
em cfg.fonte_carga. Colunas por papel: COLUNAS_LANCAMENTOS, COLUNAS_ORCAMENTO,
COLUNAS_DEVOLUCAO e COLUNAS_DESPESAS (as da PJ Cortes), trocadas pelas de
cfg.fonte_carga.colunas_modelo.

Com lançamentos agrupados (f_lancamentos_agrupados_*, a view da DRE do cliente, que já
junta tudo abaixo com a regra dele), o realizado vem só deles. Sem eles, do realizado, além
dos lançamentos, entra a operação, como na DRE do Power BI da PJ:
- vendas: receita bruta = faturado e CMV = custo das vendas (mart.resumo_mensal, o
  histórico inteiro das tabelas f_vendas_*);
- devoluções (f_devolucao_*, valor na data de emissão) nas deduções;
- despesas do contas a pagar (f_despesas_*) na data de emissão, com o grupo da DRE pelo
  de-para da conta do ERP (cfg.conta_dre, migration 14; conta sem de-para ou com
  fora_da_dre fica de fora). Valor da DRE = −en_valor, como no Power BI: despesa positiva
  é custo; negativa (crédito, estorno) abate o grupo, e nos grupos de receita é receita.
Inadimplência: com `dre.inadimplencia` em cfg.parametro_cliente ({"taxa": 0.4}, em % da
receita bruta, vigente no mês), cada mês ganha a provisão taxa × receita bruta nas deduções
(conta 110.01.02, Provisão para Devedores Duvidosos), como a DRE do Power BI da PJ. Sem o
parâmetro, nada. Contas fora da DRE por escolha do cliente: `dre.contas_fora` em
cfg.parametro_cliente ({"contas": ["120.03.22"]}, vigente no mês; um código de nível 2,
"120.03", tira tudo dentro dele), no realizado e no orçado. Ainda não entra: a folha
gerencial. O Power BI usa a data de
competência das despesas a partir de 2026 (en_datcomp, que não vem no arquivo). O
orçamento continua só o das tabelas f_orcamento_*.

O Id_Descritivo segue o plano de contas padrão da LANN (cfg.plano_conta, app/telas/plano.py):
os 3 primeiros dígitos são o grupo da DRE (GRUPOS) e 140.01 é a folha (despesas com
pessoal). Entram na DRE só as contas com competência CO ou CO/CA no plano (as de só caixa,
CA, e as fora do plano ficam de fora); sem o plano cadastrado, todas as dos grupos da DRE.

- sinal: no realizado, como no Power BI, o lançamento soma com Tipo "E" e subtrai com
  "S", e a despesa do contas a pagar entra por −en_valor; o crédito (estorno) abate o
  grupo. No orçado, que não tem Tipo, cada conta entra pelo valor absoluto da soma do
  mês, com o fator dela no plano (uma redutora de custo abate o grupo); sem plano, com o
  sinal do grupo (não importa se o DW grava despesa negativa ou positiva);
- mês de referência: o último mês FECHADO com receita bruta até `ate` (o mês escolhido na
  tela, se fechado; senão o anterior ao mês da última carga, que ainda não tem todos os
  lançamentos). Sem tabela f_lancamentos_* a seção não sai: só com vendas, o EBITDA
  seria o faturado;
- referência: o orçado do mês ou, sem ele, a média dos 6 meses anteriores (relativo.py).
  Cenário do orçado: o de `orcamento.cenario` em cfg.parametro_cliente (ex.:
  {"cenario": "Meta"}); sem o parâmetro, "Base" ou, se o orçamento tiver um só cenário,
  esse. Com vários e nenhum escolhido, não há orçado (aviso no log uma vez por processo);
- velocímetros: margem EBITDA, despesas fixas e folha, em % da receita líquida;
- cartões: EBITDA e lucro líquido do mês, ponto de equilíbrio (despesas fixas ÷ margem de
  contribuição em % da receita líquida) e margem de segurança ((receita líquida − ponto
  de equilíbrio) ÷ receita líquida).

Desempenho: realizado e orçado (todos os cenários) vêm numa ida ao banco (`consultas`, a
mesma leitura do painel Resultado), e o cenário é escolhido aqui.
"""

import logging
from collections.abc import Callable, Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from psycopg import sql

from app.core.cache import existe
from app.core.db import Conexao, em_uma_ida
from app.farol.parametro import farol, parametros_do_cliente, vigente
from app.seguranca.acesso import ContextoConsolidado
from app.telas.contrato import Status
from app.telas.moldura import MESES
from app.telas.plano import Plano, ler_plano, normalizar
from app.telas.visao_geral.contra_referencia import Mensal, Razao
from app.telas.visao_geral.contrato import Indicador, Kpi, Secao, Valor
from app.telas.visao_geral.relativo import MESES_REFERENCIA, Medidor, Serie, indicador_relativo
from app.telas.visao_geral.texto import moeda_sinal, numero
from app.telas.visao_geral.vendas import (
    COLUNAS_VENDAS,
    Consulta,
    SecaoDoMes,
    TabelaDoCliente,
    consulta_resumo_mensal,
    de_todas,
    filtro_empresa,
    mes_menos,
    tabelas_do_cliente,
)

logger = logging.getLogger(__name__)

# papel → coluna (as da PJ Cortes)
COLUNAS_LANCAMENTOS: Mapping[str, str] = {
    "data": "Data_de_Competencia",
    "valor": "Valor_Regime_de_Competencia",
    "conta": "Id_Descritivo",
    # E = entrada (soma), S = saída (subtrai)
    "tipo": "Tipo",
}
COLUNAS_ORCAMENTO: Mapping[str, str] = {
    "data": "Data",
    "valor": "Valor",
    "conta": "Id_Descritivo",
    "cenario": "Cenario",
}
COLUNAS_DEVOLUCAO: Mapping[str, str] = {"data": "en_dataemissao", "valor": "en_valortotal"}
COLUNAS_DESPESAS: Mapping[str, str] = {
    "data": "en_datemi",
    "valor": "en_valor",
    "conta": "en_codigoplanocliente",
}
# lançamentos agrupados do cliente (f_lancamentos_agrupados_*): a DRE já montada pela
# regra do cliente, com o valor no sinal do resultado (receita positiva, custo negativo)
PREFIXO_AGRUPADOS = "f_lancamentos_agrupados_"
COLUNAS_AGRUPADOS: Mapping[str, str] = {
    "data": "Data",
    "valor": "Valor",
    "conta": "Id_Descritivo",
}
# começam com f_orcamento_, mas são metas de venda, não o orçamento da DRE
FORA_DO_ORCAMENTO = ("f_orcamento_comercial_", "f_orcamento_ticket_")
CENARIO_PADRAO = "base"
# cfg.parametro_cliente: {"cenario": "<nome>"}, o cenário do orçamento usado como orçado
PARAMETRO_CENARIO = "orcamento.cenario"
# cfg.parametro_cliente: {"taxa": 0.4}, a provisão de inadimplência em % da receita bruta
PARAMETRO_INADIMPLENCIA = "dre.inadimplencia"
# onde a provisão entra: Provisão para Devedores Duvidosos, nas deduções
CONTA_INADIMPLENCIA = "110.01.02"
# cfg.parametro_cliente: {"contas": ["120.03.22"]}, contas que o cliente tira da DRE
PARAMETRO_CONTAS_FORA = "dre.contas_fora"
# (mês, Id_Descritivo normalizado) → a conta fica fora da DRE nesse mês
type ForaDaDre = Callable[[date, str], bool]
# nome da consulta (ver `consultas`) → linhas
type Linhas = Mapping[str, Sequence[Mapping[str, Any]]]

# grupos do plano gerencial (3 primeiros dígitos do Id_Descritivo)
RECEITA_BRUTA = "100"
DEDUCOES = "110"
CUSTOS = "120"
DESPESAS_VARIAVEIS = "130"
DESPESAS_FIXAS = "140"
DEPRECIACAO = "150"
RECEITAS_FINANCEIRAS = "160"
DESPESAS_FINANCEIRAS = "170"
DESPESAS_NAO_OPERACIONAIS = "180"
RECEITAS_NAO_OPERACIONAIS = "190"
IMPOSTOS_RESULTADO = "200"
# 140.01: despesas com pessoal (5 primeiros dígitos)
FOLHA = "14001"
# grupos em que a despesa negativa do contas a pagar é receita
GRUPOS_DE_RECEITA = frozenset({RECEITA_BRUTA, RECEITAS_FINANCEIRAS, RECEITAS_NAO_OPERACIONAIS})
# histórico lido: tendência, acumulado do ano e média dos meses anteriores
MESES_HISTORICO = 36

MARGEM_EBITDA = Medidor(
    id="margem-ebitda",
    nome="Margem EBITDA",
    unidade="pct",
    unidade_delta="pp",
    casas=1,
    texto=lambda v: f"{numero(v, 1)}%",
)
DESPESAS_FIXAS_RECEITA = Medidor(
    id="despesas-fixas",
    nome="Despesas fixas",
    unidade="pct",
    unidade_delta="pp",
    casas=1,
    texto=lambda v: f"{numero(v, 1)}%",
    contexto="da receita líquida",
)
FOLHA_RECEITA = Medidor(
    id="folha-receita",
    nome="Folha sobre a receita",
    unidade="pct",
    unidade_delta="pp",
    casas=1,
    texto=lambda v: f"{numero(v, 1)}%",
)
# medidor → (numerador em Mensal, chave do farol)
INDICADORES: tuple[tuple[Medidor, str, str], ...] = (
    (MARGEM_EBITDA, "ebitda", "margem_ebitda"),
    (DESPESAS_FIXAS_RECEITA, "despesas_fixas", "despesas_fixas"),
    (FOLHA_RECEITA, "folha", "folha_receita"),
)


def medidas_da_dre(grupos: Mapping[str, Decimal], folha: Decimal) -> dict[str, Decimal] | None:
    """Linhas da DRE de um mês a partir da soma de cada grupo; None sem receita bruta."""

    def g(grupo: str) -> Decimal:
        return abs(grupos.get(grupo, Decimal(0)))

    if not g(RECEITA_BRUTA):
        return None
    receita_liquida = g(RECEITA_BRUTA) - g(DEDUCOES)
    margem_contribuicao = receita_liquida - g(CUSTOS) - g(DESPESAS_VARIAVEIS)
    ebitda = margem_contribuicao - g(DESPESAS_FIXAS)
    lucro_liquido = (
        ebitda
        - g(DEPRECIACAO)
        + g(RECEITAS_FINANCEIRAS)
        - g(DESPESAS_FINANCEIRAS)
        - g(DESPESAS_NAO_OPERACIONAIS)
        + g(RECEITAS_NAO_OPERACIONAIS)
        - g(IMPOSTOS_RESULTADO)
    )
    return {
        "receita_liquida": receita_liquida,
        "margem_contribuicao": margem_contribuicao,
        "despesas_fixas": g(DESPESAS_FIXAS),
        "folha": abs(folha),
        "ebitda": ebitda,
        "lucro_liquido": lucro_liquido,
    }


def cenario_do_orcamento(cenarios: Sequence[str], escolhido: str | None = None) -> str | None:
    """Cenário usado, em minúsculas: o `escolhido` (parâmetro do cliente) se existir no
    orçamento; sem ele, "base" se houver, senão o único; None se houver vários e nenhum
    "base" (não dá para escolher) ou se o escolhido não existir."""
    distintos = {c.strip().lower() for c in cenarios if c.strip()}
    if escolhido is not None:
        return escolhido.strip().lower() if escolhido.strip().lower() in distintos else None
    if CENARIO_PADRAO in distintos:
        return CENARIO_PADRAO
    if len(distintos) == 1:
        return distintos.pop()
    return None


@dataclass(frozen=True)
class Operacao:
    """Mês → grupo da DRE → valor da operação (vendas, devoluções e despesas), já na
    direção da DRE: positivo = receita nos grupos de receita e despesa nos de custo."""

    grupos: dict[date, dict[str, Decimal]] = field(default_factory=dict)
    # despesas com pessoal (140.01) do contas a pagar
    folha: dict[date, Decimal] = field(default_factory=dict)


def grupos_da_operacao(
    vendas: Mensal,
    devolucoes: Mapping[date, Decimal],
    despesas: Iterable[tuple[date, str, Decimal]] = (),
    plano: Plano | None = None,
    fora: ForaDaDre | None = None,
) -> Operacao:
    """Vendas (faturado e custo do resumo mensal), devoluções e despesas por mês e grupo
    da DRE. Despesas: (mês, Id_Descritivo, valor com o sinal do contas a pagar: positivo
    = saída), do contas a pagar e dos lançamentos; só as contas que o `plano` põe na DRE
    (competência CO ou CO/CA) e que o cliente não tirou dela (`fora`)."""
    plano = plano or Plano()
    op = Operacao()

    def somar(mes: date, grupo: str, valor: Decimal) -> None:
        do_mes = op.grupos.setdefault(mes, {})
        do_mes[grupo] = do_mes.get(grupo, Decimal(0)) + valor

    for mes, medidas in vendas.items():
        if "faturado" in medidas:
            somar(mes, RECEITA_BRUTA, medidas["faturado"])
        if "custo" in medidas:
            somar(mes, CUSTOS, medidas["custo"])
    for mes, valor in devolucoes.items():
        somar(mes, DEDUCOES, valor)
    for mes, id_descritivo, valor in despesas:
        codigo = normalizar(id_descritivo)
        if codigo is None or not plano.na_dre(codigo) or (fora is not None and fora(mes, codigo)):
            continue
        digitos = codigo.replace(".", "")
        grupo = digitos[:3]
        # valor da DRE = −en_valor; nos grupos de custo a despesa entra positiva
        somar(mes, grupo, -valor if grupo in GRUPOS_DE_RECEITA else valor)
        if digitos[:5] == FOLHA:
            op.folha[mes] = op.folha.get(mes, Decimal(0)) + valor
    return op


def dre_por_mes(
    lancados: Mapping[date, Mapping[str, Decimal]],
    folha: Mapping[date, Decimal],
    operacao: Operacao,
) -> Mensal:
    """Linhas da DRE por mês: os grupos lançados somados aos da operação. Mês sem receita
    bruta fica de fora.

    O lançado entra em valor absoluto (pode gravar despesa negativa); a operação já vem
    na direção da DRE, e um crédito, como um estorno, abate o grupo."""
    mensal: Mensal = {}
    for mes in set(lancados) | set(operacao.grupos):
        grupos = {g: abs(v) for g, v in lancados.get(mes, {}).items()}
        for grupo, valor in operacao.grupos.get(mes, {}).items():
            grupos[grupo] = grupos.get(grupo, Decimal(0)) + valor
        folha_mes = abs(folha.get(mes, Decimal(0))) + operacao.folha.get(mes, Decimal(0))
        medidas = medidas_da_dre(grupos, folha_mes)
        if medidas is not None:
            mensal[mes] = medidas
    return mensal


def taxa_inadimplencia(conn: Conexao, cliente_id: UUID, mes: date) -> Decimal | None:
    """% da receita bruta provisionado como inadimplência no mês (`dre.inadimplencia`
    vigente no primeiro dia dele); None sem o parâmetro ou com a taxa inválida."""
    valor = vigente(parametros_do_cliente(conn, cliente_id), PARAMETRO_INADIMPLENCIA, mes)
    taxa = valor.get("taxa") if isinstance(valor, dict) else None
    if isinstance(taxa, bool) or not isinstance(taxa, int | float | str):
        return None
    try:
        decimal = Decimal(str(taxa))
    except InvalidOperation:
        return None
    return decimal if decimal.is_finite() and decimal > 0 else None


def fora_da_dre(conn: Conexao, cliente_id: UUID) -> ForaDaDre:
    """As contas que o cliente tira da DRE (`dre.contas_fora`, vigente no mês): a conta
    listada e, se o listado é um código de nível 1 ou 2, tudo dentro dele."""
    parametros = parametros_do_cliente(conn, cliente_id)

    def fora(mes: date, conta: str) -> bool:
        valor = vigente(parametros, PARAMETRO_CONTAS_FORA, mes)
        contas = valor.get("contas") if isinstance(valor, dict) else None
        if not isinstance(contas, list):
            return False
        for c in contas:
            codigo = normalizar(c) if isinstance(c, str) else None
            if codigo is not None and (conta == codigo or conta.startswith(codigo + ".")):
                return True
        return False

    return fora


def com_inadimplencia(op: Operacao, taxa: Callable[[date], Decimal | None]) -> Operacao:
    """A operação com a provisão de inadimplência (taxa do mês × receita bruta) somada às
    deduções de cada mês com receita."""
    grupos: dict[date, dict[str, Decimal]] = {}
    for mes, do_mes in op.grupos.items():
        grupos[mes] = dict(do_mes)
        receita = do_mes.get(RECEITA_BRUTA, Decimal(0))
        t = taxa(mes)
        if t is not None and receita > 0:
            grupos[mes][DEDUCOES] = do_mes.get(DEDUCOES, Decimal(0)) + receita * t / 100
    return Operacao(grupos=grupos, folha=dict(op.folha))


def com_as_colunas(tabelas: list[TabelaDoCliente]) -> list[TabelaDoCliente]:
    """Só as tabelas que têm as colunas de todos os papéis; as outras ficam de fora com
    aviso (ex.: colunas_modelo ainda não cadastrado), em vez de derrubar a seção."""
    validas = []
    for t in tabelas:
        faltam = sorted(set(t.colunas.values()) - t.existentes)
        if faltam:
            logger.warning("mart.%s sem as colunas %s: fica fora da DRE", t.tabela, faltam)
        else:
            validas.append(t)
    return validas


def tabelas_devolucoes(conn: Conexao, ctx: ContextoConsolidado) -> list[TabelaDoCliente]:
    return com_as_colunas(
        tabelas_do_cliente(conn, ctx.cliente_id, "f_devolucao_", COLUNAS_DEVOLUCAO)
    )


def tabelas_lancamentos(conn: Conexao, ctx: ContextoConsolidado) -> list[TabelaDoCliente]:
    return com_as_colunas(
        [
            t
            for t in tabelas_do_cliente(conn, ctx.cliente_id, "f_lancamentos_", COLUNAS_LANCAMENTOS)
            if not t.tabela.startswith(PREFIXO_AGRUPADOS)
        ]
    )


def tabelas_agrupados(conn: Conexao, ctx: ContextoConsolidado) -> list[TabelaDoCliente]:
    return com_as_colunas(
        tabelas_do_cliente(conn, ctx.cliente_id, PREFIXO_AGRUPADOS, COLUNAS_AGRUPADOS)
    )


def tabelas_despesas(conn: Conexao, ctx: ContextoConsolidado) -> list[TabelaDoCliente]:
    """Tabelas de despesas que entram na DRE; vazio sem a migration 14 (cfg.conta_dre)."""
    if not existe(conn, "cfg.conta_dre"):
        return []
    return com_as_colunas(tabelas_do_cliente(conn, ctx.cliente_id, "f_despesas_", COLUNAS_DESPESAS))


def _parametros(ctx: ContextoConsolidado, de: date, ate: date) -> dict[str, Any]:
    return {"cliente": ctx.cliente_id, "empresas": list(ctx.empresas), "de": de, "ate": ate}


def consulta_devolucoes(
    ctx: ContextoConsolidado, tabelas: list[TabelaDoCliente], de: date, ate: date
) -> Consulta:
    """Devoluções por mês (linhas: mes, valor), de `de` até o fim do mês de `ate`."""
    return sql.SQL(
        """
        with d as ({linhas})
        select date_trunc('month', dia)::date as mes, sum(valor) as valor
          from d
         where dia >= %(de)s and dia < %(ate)s::date + interval '1 month'
         group by 1
        """
    ).format(
        linhas=de_todas(tabelas, {"dia": "data", "valor": "valor"}, filtro_empresa(ctx))
    ), _parametros(ctx, de, ate)


def consulta_lancamentos(
    ctx: ContextoConsolidado, tabelas: list[TabelaDoCliente], de: date, ate: date
) -> Consulta:
    """Lançamentos por mês e Id_Descritivo (linhas: mes, conta, valor), com o sinal do
    contas a pagar: saída (Tipo "S") positiva, entrada ("E") negativa."""
    return sql.SQL(
        """
        with l as ({linhas})
        select date_trunc('month', dia)::date as mes, conta,
               sum(case when upper(trim(tipo)) = 'E' then -valor else valor end) as valor
          from l
         where conta is not null
           and dia >= %(de)s and dia < %(ate)s::date + interval '1 month'
         group by 1, 2
        """
    ).format(
        linhas=de_todas(
            tabelas,
            {"dia": "data", "conta": "conta", "valor": "valor", "tipo": "tipo"},
            filtro_empresa(ctx),
        )
    ), _parametros(ctx, de, ate)


def consulta_despesas(
    ctx: ContextoConsolidado, tabelas: list[TabelaDoCliente], de: date, ate: date
) -> Consulta:
    """Despesas com de-para em cfg.conta_dre por mês e Id_Descritivo (linhas: mes, conta,
    valor = soma de en_valor)."""
    return sql.SQL(
        """
        with d as ({linhas})
        select date_trunc('month', d.dia)::date as mes, c.id_descritivo as conta,
               sum(d.valor) as valor
          from d
          join cfg.conta_dre c on c.cliente_id = %(cliente)s and c.conta = d.conta
         where c.fora_da_dre is null and c.id_descritivo is not null
           and d.dia >= %(de)s and d.dia < %(ate)s::date + interval '1 month'
         group by 1, 2
        """
    ).format(
        linhas=de_todas(
            tabelas, {"dia": "data", "valor": "valor", "conta": "conta"}, filtro_empresa(ctx)
        )
    ), _parametros(ctx, de, ate)


def consulta_agrupados(
    ctx: ContextoConsolidado, tabelas: list[TabelaDoCliente], de: date, ate: date
) -> Consulta:
    """Lançamentos agrupados por mês e Id_Descritivo (linhas: mes, conta, valor no sinal do
    resultado)."""
    return sql.SQL(
        """
        with a as ({linhas})
        select date_trunc('month', dia)::date as mes, conta, sum(valor) as valor
          from a
         where conta is not null
           and dia >= %(de)s and dia < %(ate)s::date + interval '1 month'
         group by 1, 2
        """
    ).format(
        linhas=de_todas(
            tabelas, {"dia": "data", "conta": "conta", "valor": "valor"}, filtro_empresa(ctx)
        )
    ), _parametros(ctx, de, ate)


def consulta_orcamento(
    ctx: ContextoConsolidado, tabelas: list[TabelaDoCliente], de: date, ate: date
) -> Consulta:
    """Orçado por mês, conta e cenário (minúsculas; '' sem cenário)."""
    return sql.SQL(
        """
        with o as ({linhas})
        select date_trunc('month', dia)::date as mes, conta,
               coalesce(lower(trim(cenario)), '') as cenario, sum(valor) as valor
          from o
         where conta is not null
           and dia >= %(de)s and dia < %(ate)s::date + interval '1 month'
         group by 1, 2, 3
        """
    ).format(
        linhas=de_todas(
            tabelas,
            {"dia": "data", "conta": "conta", "valor": "valor", "cenario": "cenario"},
            filtro_empresa(ctx),
        )
    ), _parametros(ctx, de, ate)


def consultas(
    conn: Conexao, ctx: ContextoConsolidado, de: date, ate: date, com_agrupados: bool = True
) -> dict[str, Consulta]:
    """O realizado e o orçado, de `de` até o fim do mês de `ate`, para uma ida ao banco
    (em_uma_ida); também as leituras do painel Resultado. Com os lançamentos agrupados do
    cliente, eles no lugar das fontes da operação."""
    c: dict[str, Consulta] = {}
    if com_agrupados and (agrupados := tabelas_agrupados(conn, ctx)):
        # a view da DRE do cliente já traz vendas, CMV, devoluções e despesas
        c["agrupados"] = consulta_agrupados(ctx, agrupados, de, ate)
    else:
        vendas = tabelas_do_cliente(conn, ctx.cliente_id, "f_vendas_", COLUNAS_VENDAS)
        if vendas and existe(conn, "mart.resumo_mensal"):
            c["vendas"] = consulta_resumo_mensal(ctx, vendas, de, ate)
        if t := tabelas_devolucoes(conn, ctx):
            c["devolucoes"] = consulta_devolucoes(ctx, t, de, ate)
        if t := tabelas_lancamentos(conn, ctx):
            c["lancamentos"] = consulta_lancamentos(ctx, t, de, ate)
        if t := tabelas_despesas(conn, ctx):
            c["despesas"] = consulta_despesas(ctx, t, de, ate)
    if t := com_as_colunas(tabelas_orcamento(conn, ctx)):
        c["orcamento"] = consulta_orcamento(ctx, t, de, ate)
    return c


def operacao_das_linhas(linhas: Linhas, plano: Plano, fora: ForaDaDre) -> Operacao:
    """Grupos da DRE que vêm das vendas, das devoluções e das despesas (linhas de
    `consultas`). Com lançamentos agrupados, só deles (já trazem tudo isso)."""
    if "agrupados" in linhas:
        # sinal do resultado → sinal do contas a pagar (saída positiva)
        agrupados = [(r["mes"], r["conta"], -r["valor"]) for r in linhas["agrupados"] if r["valor"]]
        return grupos_da_operacao({}, {}, agrupados, plano, fora)
    vendas: Mensal = {}
    for r in linhas.get("vendas", []):
        vendas.setdefault(r["mes"], {})[r["medida"]] = r["valor"]
    devolucoes = {r["mes"]: r["valor"] for r in linhas.get("devolucoes", []) if r["valor"]}
    despesas = [
        (r["mes"], r["conta"], r["valor"])
        for r in [*linhas.get("lancamentos", []), *linhas.get("despesas", [])]
        if r["valor"]
    ]
    return grupos_da_operacao(vendas, devolucoes, despesas, plano, fora)


def somar_orcado(
    linhas: Iterable[Mapping[str, Any]], plano: Plano, fora: ForaDaDre
) -> tuple[dict[date, dict[str, Decimal]], dict[date, Decimal]]:
    """Soma por mês e grupo da DRE (3 primeiros dígitos do Id_Descritivo), e a folha, das
    linhas (mes, conta, valor) de um cenário do orçado.

    Orçado (sem sinal no DW): cada conta entra pelo valor absoluto da soma do mês, na
    direção do fator do plano (uma redutora de custo abate o grupo); só as contas que o
    plano põe na DRE. Sem plano, todas, somando ao grupo. Valor nulo conta como zero: o
    mês continua no orçado."""
    grupos: dict[date, dict[str, Decimal]] = {}
    folha: dict[date, Decimal] = {}
    for r in linhas:
        codigo = normalizar(r["conta"])
        if codigo is None or not plano.na_dre(codigo) or fora(r["mes"], codigo):
            continue
        digitos = codigo.replace(".", "")
        grupo = digitos[:3]
        fator = plano.fator(codigo)
        # direção da DRE: positivo = receita nos grupos de receita e custo nos outros
        sentido = 1 if fator is None else (fator if grupo in GRUPOS_DE_RECEITA else -fator)
        valor = abs(r["valor"] or Decimal(0)) * sentido
        do_mes = grupos.setdefault(r["mes"], {})
        do_mes[grupo] = do_mes.get(grupo, Decimal(0)) + valor
        if digitos[:5] == FOLHA:
            folha[r["mes"]] = folha.get(r["mes"], Decimal(0)) + valor
    return grupos, folha


def orcado_das_linhas(
    linhas: Iterable[Mapping[str, Any]],
    escolhido: str | None,
    plano: Plano,
    fora: ForaDaDre,
    cliente_id: UUID | None = None,
) -> Mensal:
    """Linhas da DRE do orçado por mês (linhas `orcamento` de `consultas`), no cenário de
    cenario_do_orcamento; sem cenário nomeado, tudo. Com vários e nenhum escolhido, sem
    orçado (aviso no log uma vez por processo)."""
    por_cenario: dict[str, list[Mapping[str, Any]]] = {}
    for r in linhas:
        por_cenario.setdefault(r["cenario"], []).append(r)
    nomeados = sorted(c for c in por_cenario if c)
    if not nomeados:  # orçamento sem cenário: vale tudo
        do_cenario = por_cenario.get("", [])
    else:
        cenario = cenario_do_orcamento(nomeados, escolhido)
        if cenario is None:
            avisar_uma_vez(
                (cliente_id, escolhido, tuple(nomeados)),
                "Orçamento do cliente %s sem cenário escolhido (%s em %s; cenários: %s): sem "
                "orçado. Cadastre o cenário em cfg.parametro_cliente.",
                cliente_id,
                repr(escolhido) if escolhido else "nenhum",
                PARAMETRO_CENARIO,
                ", ".join(nomeados),
            )
            return {}
        do_cenario = por_cenario[cenario]
    grupos, folha = somar_orcado(do_cenario, plano, fora)
    return dre_por_mes(grupos, folha, Operacao())


def tabelas_orcamento(conn: Conexao, ctx: ContextoConsolidado) -> list[TabelaDoCliente]:
    """Tabelas f_orcamento_* da DRE (sem os orçamentos comercial e de ticket)."""
    return [
        t
        for t in tabelas_do_cliente(conn, ctx.cliente_id, "f_orcamento_", COLUNAS_ORCAMENTO)
        if not t.tabela.startswith(FORA_DO_ORCAMENTO)
    ]


def cenario_escolhido(conn: Conexao, ctx: ContextoConsolidado, data: date) -> str | None:
    """O cenário de `orcamento.cenario` em cfg.parametro_cliente, vigente na data."""
    parametro = vigente(parametros_do_cliente(conn, ctx.cliente_id), PARAMETRO_CENARIO, data)
    escolhido = parametro.get("cenario") if isinstance(parametro, dict) else None
    return escolhido if isinstance(escolhido, str) else None


_avisados: set[Hashable] = set()


def avisar_uma_vez(chave: Hashable, mensagem: str, *args: object) -> None:
    """O aviso sai uma vez por processo, não a cada vez que a tela abre."""
    if chave in _avisados:
        return
    _avisados.add(chave)
    logger.warning(mensagem, *args)


def _contra(medida: str, ref: date, real: Mensal, orcado: Mensal) -> str:
    """ "orçado R$ 679 mil" ou, sem orçado, "média de 6 meses: R$ 640 mil"."""
    if medida in orcado.get(ref, {}):
        return f"orçado {moeda_sinal(orcado[ref][medida])}"
    anteriores = [m for m in sorted(real) if m < ref and medida in real[m]][-MESES_REFERENCIA:]
    if not anteriores:
        return "sem orçado no mês"
    media = sum((real[m][medida] for m in anteriores), Decimal(0)) / len(anteriores)
    nome = "mês anterior" if len(anteriores) == 1 else f"média de {len(anteriores)} meses"
    return f"{nome}: {moeda_sinal(media)}"


def _margem_seguranca(medidas: Mapping[str, Decimal]) -> tuple[Decimal, Decimal] | None:
    """(ponto de equilíbrio, margem de segurança em %); None com margem de contribuição
    zero ou negativa, quando não existe ponto de equilíbrio."""
    rl, mc = medidas["receita_liquida"], medidas["margem_contribuicao"]
    if rl <= 0 or mc <= 0:
        return None
    ponto = medidas["despesas_fixas"] / (mc / rl)
    return ponto, (rl - ponto) / rl * 100


def kpis_resultado(
    ref: date, real: Mensal, orcado: Mensal, status_ebitda: Status | None
) -> list[Kpi]:
    """EBITDA e lucro líquido do mês, ponto de equilíbrio e margem de segurança."""
    medidas = real[ref]
    nome_mes = MESES[ref.month - 1].lower()
    kpis = [
        Kpi(
            rotulo=f"EBITDA de {nome_mes}",
            valor=Valor(valor=medidas["ebitda"], unidade="brl"),
            contexto=_contra("ebitda", ref, real, orcado),
            status=status_ebitda,
        ),
        Kpi(
            rotulo=f"Lucro líquido de {nome_mes}",
            valor=Valor(valor=medidas["lucro_liquido"], unidade="brl"),
            contexto=_contra("lucro_liquido", ref, real, orcado),
        ),
    ]
    seguranca = _margem_seguranca(medidas)
    if seguranca is None:
        return kpis
    ponto, margem = seguranca
    antes = mes_menos(ref, MESES_REFERENCIA - 1)
    anterior = _margem_seguranca(real[antes]) if antes in real else None
    kpis += [
        Kpi(
            rotulo="Ponto de equilíbrio",
            valor=Valor(valor=ponto, unidade="brl"),
            contexto="de receita líquida por mês",
        ),
        Kpi(
            rotulo="Margem de segurança",
            valor=Valor(valor=margem, unidade="pct", casas=0),
            contexto=(
                f"era {numero(anterior[1])}% em {MESES[antes.month - 1].lower()}"
                if anterior is not None
                else "quanto a receita pode cair antes do prejuízo"
            ),
            # abaixo de zero, a receita não paga as despesas fixas
            status="critico" if margem < 0 else None,
        ),
    ]
    return kpis


def secao_resultado(ctx: ContextoConsolidado, ate: date) -> SecaoDoMes | None:
    """Seção Resultado; None sem tabela de lançamentos ou sem mês fechado com receita.

    `ate`: o último mês fechado que entra (primeiro dia)."""
    conn = ctx.sessao.conn
    if not tabelas_do_cliente(conn, ctx.cliente_id, "f_lancamentos_", COLUNAS_LANCAMENTOS):
        return None
    de = mes_menos(ate, MESES_HISTORICO)
    # realizado e orçado numa ida ao banco
    linhas = em_uma_ida(conn, consultas(conn, ctx, de, ate))
    if "agrupados" in linhas and not any(r["valor"] for r in linhas["agrupados"]):
        # tabela de agrupados sem valor (ex.: a carga dela falhou): a regra sem ela
        linhas = em_uma_ida(conn, consultas(conn, ctx, de, ate, com_agrupados=False))
    plano, fora = ler_plano(conn), fora_da_dre(conn, ctx.cliente_id)
    # lançamentos, vendas, devoluções e despesas: tudo na operação, com sinal
    operacao = com_inadimplencia(
        operacao_das_linhas(linhas, plano, fora),
        lambda m: taxa_inadimplencia(conn, ctx.cliente_id, m),
    )
    real = dre_por_mes({}, {}, operacao)
    if not real:
        return None
    ref = max(real)
    orcado = orcado_das_linhas(
        linhas.get("orcamento", []),
        cenario_escolhido(conn, ctx, ate),
        plano,
        fora,
        ctx.cliente_id,
    )

    indicadores: list[Indicador] = []
    for medidor, numerador, chave in INDICADORES:
        razao = Razao(numerador, "receita_liquida")
        ind = indicador_relativo(
            medidor,
            ref,
            Serie(real, razao, Decimal(100)),
            Serie(orcado, razao, Decimal(100)) if orcado else None,
            farol(conn, ctx.cliente_id, chave, ref),
        )
        if ind is not None:
            indicadores.append(ind)
    status_ebitda = next(
        (i.periodos.mes.status for i in indicadores if i.id == MARGEM_EBITDA.id), None
    )

    nome_mes = MESES[ref.month - 1].lower()
    contra = "o orçado" if ref in orcado else "a média dos meses anteriores"
    secao = Secao(
        id="resultado",
        titulo="Resultado",
        modulo=None,
        # "mês fechado", não "o último": com o mês escolhido na tela, pode ser um anterior
        resumo=f"Dados de {nome_mes} de {ref.year}, mês fechado, contra {contra}.",
        kpis=kpis_resultado(ref, real, orcado, status_ebitda),
        cartoes=[],
        indicadores=indicadores,
    )
    return SecaoDoMes(secao=secao, mes=ref)

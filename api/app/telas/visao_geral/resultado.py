"""Seção Resultado da Visão geral. REGRAS PROVISÓRIAS: confirmar com o negócio (ver TODO.md).

Lê os lançamentos gerenciais (f_lancamentos_*: data de competência, valor e Id_Descritivo)
e o orçamento (f_orcamento_*, menos os orçamentos comercial e de ticket) que o cliente tem
em cfg.fonte_carga. Colunas por papel: COLUNAS_LANCAMENTOS e COLUNAS_ORCAMENTO (as da PJ
Cortes), trocadas pelas de cfg.fonte_carga.colunas_modelo.

O Id_Descritivo segue o plano gerencial do mockup: os 3 primeiros dígitos são o grupo da
DRE (GRUPOS) e 140.01 é a folha (despesas com pessoal). Id fora do plano fica de fora.

- sinal: cada grupo entra pelo valor absoluto da soma do mês, com o sinal dele na DRE;
  assim não importa se o DW grava despesa negativa ou positiva;
- mês de referência: o último mês FECHADO com receita bruta, anterior ao mês da última
  carga (o mês corrente ainda não tem todos os lançamentos);
- referência: o orçado do mês (cenário "Base" ou, se o orçamento tiver um só cenário,
  esse) ou, sem ele, a média dos 6 meses anteriores (relativo.py);
- velocímetros: margem EBITDA, despesas fixas e folha, em % da receita líquida;
- cartões: EBITDA e lucro líquido do mês, ponto de equilíbrio (despesas fixas ÷ margem de
  contribuição em % da receita líquida) e margem de segurança ((receita líquida − ponto
  de equilíbrio) ÷ receita líquida).
"""

import logging
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal

from psycopg import sql

from app.core.db import Conexao
from app.farol.parametro import farol
from app.seguranca.acesso import ContextoConsolidado
from app.telas.contrato import Status
from app.telas.moldura import MESES
from app.telas.visao_geral.contra_referencia import Mensal, Razao
from app.telas.visao_geral.contrato import Indicador, Kpi, Secao, Valor
from app.telas.visao_geral.relativo import MESES_REFERENCIA, Medidor, Serie, indicador_relativo
from app.telas.visao_geral.texto import moeda_sinal, numero
from app.telas.visao_geral.vendas import (
    SecaoDoMes,
    TabelaDoCliente,
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
}
COLUNAS_ORCAMENTO: Mapping[str, str] = {
    "data": "Data",
    "valor": "Valor",
    "conta": "Id_Descritivo",
    "cenario": "Cenario",
}
# começam com f_orcamento_, mas são metas de venda, não o orçamento da DRE
FORA_DO_ORCAMENTO = ("f_orcamento_comercial_", "f_orcamento_ticket_")
CENARIO_PADRAO = "base"

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


def cenario_do_orcamento(cenarios: Sequence[str]) -> str | None:
    """Cenário usado, em minúsculas: "base" se houver, senão o único; None se houver vários
    e nenhum "base" (não dá para escolher)."""
    distintos = {c.strip().lower() for c in cenarios if c.strip()}
    if CENARIO_PADRAO in distintos:
        return CENARIO_PADRAO
    if len(distintos) == 1:
        return distintos.pop()
    return None


def _dre_mensal(
    conn: Conexao,
    ctx: ContextoConsolidado,
    tabelas: list[TabelaDoCliente],
    de: date,
    ate: date,
    cenario: str | None = None,
) -> Mensal:
    """Linhas da DRE por mês, de `de` até o fim do mês de `ate`."""
    campos = {"dia": "data", "conta": "conta", "valor": "valor"}
    filtro_cenario = sql.SQL("")
    if cenario is not None:
        campos["cenario"] = "cenario"
        filtro_cenario = sql.SQL("and lower(trim(cenario)) = %(cenario)s")
    grupos: dict[date, dict[str, Decimal]] = {}
    folha: dict[date, Decimal] = {}
    for r in conn.execute(
        sql.SQL(
            """
            with l as ({linhas}),
            c as (
                select date_trunc('month', dia)::date as mes,
                       regexp_replace(conta, '\\D', '', 'g') as digitos,
                       valor
                  from l
                 where dia >= %(de)s and dia < %(ate)s::date + interval '1 month'
                       {filtro_cenario}
            )
            select mes,
                   left(digitos, 3) as grupo,
                   coalesce(sum(valor), 0) as valor,
                   coalesce(sum(valor) filter (where left(digitos, 5) = %(folha)s), 0) as folha
              from c
             where digitos <> ''
             group by mes, left(digitos, 3)
            """
        ).format(
            linhas=de_todas(tabelas, campos, filtro_empresa(ctx)),
            filtro_cenario=filtro_cenario,
        ),
        {
            "cliente": ctx.cliente_id,
            "empresas": list(ctx.empresas),
            "de": de,
            "ate": ate,
            "folha": FOLHA,
            "cenario": cenario,
        },
    ):
        grupos.setdefault(r["mes"], {})[r["grupo"]] = r["valor"]
        folha[r["mes"]] = folha.get(r["mes"], Decimal(0)) + r["folha"]
    mensal: Mensal = {}
    for mes, por_grupo in grupos.items():
        medidas = medidas_da_dre(por_grupo, folha.get(mes, Decimal(0)))
        if medidas is not None:
            mensal[mes] = medidas
    return mensal


def _orcado(conn: Conexao, ctx: ContextoConsolidado, de: date, ate: date) -> Mensal:
    tabelas = [
        t
        for t in tabelas_do_cliente(conn, ctx.cliente_id, "f_orcamento_", COLUNAS_ORCAMENTO)
        if not t.tabela.startswith(FORA_DO_ORCAMENTO)
    ]
    if not tabelas:
        return {}
    cenarios = [
        r["cenario"]
        for r in conn.execute(
            sql.SQL(
                "with o as ({linhas}) select distinct cenario from o where cenario <> ''"
            ).format(linhas=de_todas(tabelas, {"cenario": "cenario"}, filtro_empresa(ctx))),
            {"cliente": ctx.cliente_id, "empresas": list(ctx.empresas)},
        )
    ]
    if not cenarios:  # orçamento sem cenário: vale tudo
        return _dre_mensal(conn, ctx, tabelas, de, ate)
    cenario = cenario_do_orcamento(cenarios)
    if cenario is None:
        logger.warning(
            "Orçamento do cliente %s com vários cenários e nenhum 'Base' (%s): sem orçado",
            ctx.cliente_id,
            ", ".join(sorted(cenarios)),
        )
        return {}
    return _dre_mensal(conn, ctx, tabelas, de, ate, cenario)


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


def secao_resultado(ctx: ContextoConsolidado, hoje: date) -> SecaoDoMes | None:
    """Seção Resultado; None sem lançamentos ou sem mês fechado com receita."""
    conn = ctx.sessao.conn
    tabelas = tabelas_do_cliente(conn, ctx.cliente_id, "f_lancamentos_", COLUNAS_LANCAMENTOS)
    if not tabelas:
        return None
    # meses fechados: até o mês anterior ao da última carga
    ate = mes_menos(date(hoje.year, hoje.month, 1), 1)
    de = mes_menos(ate, MESES_HISTORICO)
    real = _dre_mensal(conn, ctx, tabelas, de, ate)
    if not real:
        return None
    ref = max(real)
    orcado = _orcado(conn, ctx, de, ate)

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
        resumo=f"Dados de {nome_mes} de {ref.year}, o último mês fechado, contra {contra}.",
        kpis=kpis_resultado(ref, real, orcado, status_ebitda),
        cartoes=[],
        indicadores=indicadores,
    )
    return SecaoDoMes(secao=secao, mes=ref)

"""Seção Vendas da Visão geral. REGRAS PROVISÓRIAS: confirmar com o negócio (ver TODO.md).

Lê a tabela de vendas (f_vendas_*) e a de metas (f_metas_*) que o cliente tem registradas
em cfg.fonte_carga, e o histórico mensal de mart.resumo_mensal (gravado pela carga).

- mês de referência: o mais recente com venda em en_datemicao (a janela da carga pode
  guardar só o mês fechado, então não é necessariamente o mês do snapshot);
- faturado: soma de en_VALTOT; meta: soma de Valor em f_metas na Competencia do mês;
- projeção e a realizar por dia útil: ver projecao.py (dias úteis em calendario.py);
- clientes: en_codcli distintos; pedidos: en_numdoc distintos; ticket: faturado / pedidos;
- "Vendas contra a meta" (velocímetro): faturado / meta em %, no mês e no acumulado do
  ano (meses do ano com faturado e meta); tendência dos últimos 6 meses com faturado e
  meta. Faixas do farol: farol.vendas_meta em cfg.parametro_cliente (padrão no código);
- "Clientes que compraram", "Ticket por pedido" e "Produtos por cliente" (velocímetros):
  contra a meta cadastrada ou, sem ela, a média dos 6 meses anteriores (ver
  contra_referencia.py). Produtos por cliente = pares cliente × produto (en_codigo)
  distintos ÷ clientes.

Empresa: com uma empresa escolhida, só entram linhas dessa empresa. Tabela sem coluna de
empresa (empresa nula) só entra na visão de todas as empresas.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from psycopg import sql

from app.core.db import Conexao
from app.farol.parametro import Farol, farol
from app.seguranca.acesso import ContextoConsolidado
from app.telas.calendario import feriados, ultimo_dia_do_mes
from app.telas.moldura import MESES
from app.telas.visao_geral.contra_referencia import (
    MESES_CURTOS,
    Definicao,
    Mensal,
    Razao,
    indicador_contra_referencia,
)
from app.telas.visao_geral.contrato import Faixa as FaixaGauge
from app.telas.visao_geral.contrato import (
    Gauge,
    Indicador,
    IndicadorPeriodo,
    Kpi,
    PeriodosIndicador,
    Secao,
    Tendencia,
    Valor,
)
from app.telas.visao_geral.projecao import Projecao, projetar
from app.telas.visao_geral.texto import moeda, moeda_cheia, numero

MESES_TENDENCIA = 6
INDICADOR_META = "vendas_meta"

CLIENTES = Definicao(
    id="clientes-compraram",
    nome="Clientes que compraram",
    chave_farol="clientes_compraram",
    razao=Razao("clientes"),
    unidade="qtd",
    sufixo="clientes",
    texto=lambda v: f"{numero(v)} clientes",
)
TICKET = Definicao(
    id="ticket-pedido",
    nome="Ticket por pedido",
    chave_farol="ticket_pedido",
    razao=Razao("faturado", "pedidos"),
    unidade="brl",
    compacto=False,
    delta_percentual=True,
    texto=moeda_cheia,
)
PRODUTOS = Definicao(
    id="produtos-cliente",
    nome="Produtos por cliente",
    chave_farol="produtos_cliente",
    razao=Razao("clientes_produtos", "clientes"),
    unidade="qtd",
    casas=1,
    sufixo="produtos",
    texto=lambda v: f"{numero(v, 1)} produtos",
)


@dataclass(frozen=True)
class SecaoDoMes:
    secao: Secao
    # mês de referência dos números da seção
    mes: date


@dataclass(frozen=True)
class MesDeVendas:
    mes: date
    ultimo_dia: date
    faturado: Decimal
    # soma de en_precus (custo total da linha)
    custo: Decimal
    clientes: int
    pedidos: int
    # pares cliente × produto distintos
    clientes_produtos: int


@dataclass(frozen=True)
class BaseVendas:
    """O que a seção Vendas já calculou e a seção Custos reaproveita."""

    tabela: str
    mes: MesDeVendas
    # histórico do resumo mensal, com o mês de referência vindo da tabela
    mensal: Mensal
    # primeiro mês do histórico lido
    inicio: date
    # feriados do mês de referência
    feriados: set[date]


def data_texto(coluna: str) -> sql.Composable:
    """Data guardada como texto no mart → date (mesmos formatos da janela da carga).

    Usa os 10 primeiros caracteres (ignora a hora); formato desconhecido vira null.
    """
    c = sql.SQL("left({}, 10)").format(sql.Identifier(coluna))
    return sql.SQL(
        "case"
        " when {c} ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}$' then to_date({c}, 'YYYY-MM-DD')"
        " when {c} ~ '^\\d{{2}}/\\d{{2}}/\\d{{4}}$' then to_date({c}, 'DD/MM/YYYY')"
        " when {c} ~ '^\\d{{4}}/\\d{{2}}/\\d{{2}}$' then to_date({c}, 'YYYY/MM/DD')"
        " end"
    ).format(c=c)


def tabela_do_cliente(conn: Conexao, cliente_id: UUID, prefixo: str) -> str | None:
    """Tabela de dados do cliente que começa com `prefixo` (ex.: f_vendas_) e existe no mart."""
    linha = conn.execute(
        """
        select f.tabela_destino
          from cfg.fonte_carga f
         where f.cliente_id = %s
           and f.tipo = 'tabela'
           and starts_with(f.tabela_destino, %s)
           and to_regclass('mart.' || quote_ident(f.tabela_destino)) is not null
         order by f.tabela_destino
         limit 1
        """,
        (cliente_id, prefixo),
    ).fetchone()
    return linha["tabela_destino"] if linha else None


def filtro_empresa(ctx: ContextoConsolidado, coluna: str = "_empresa_id") -> sql.Composable:
    c = sql.Identifier(coluna)
    if ctx.empresa_id is not None:
        return sql.SQL("{c} = any(%(empresas)s)").format(c=c)
    return sql.SQL("({c} is null or {c} = any(%(empresas)s))").format(c=c)


def _vendas_do_mes(conn: Conexao, ctx: ContextoConsolidado, tabela: str) -> MesDeVendas | None:
    linha = conn.execute(
        sql.SQL(
            """
            with v as (
                select {dia} as dia, "en_VALTOT" as valor, "en_precus" as custo,
                       "en_codcli", "en_numdoc", "en_codigo"
                  from {tabela}
                 where _cliente_id = %(cliente)s and {filtro}
            ),
            ref as (select date_trunc('month', max(dia))::date as mes from v)
            select ref.mes,
                   max(v.dia) as ultimo_dia,
                   coalesce(sum(v.valor), 0) as faturado,
                   coalesce(sum(v.custo), 0) as custo,
                   count(distinct v."en_codcli") as clientes,
                   count(distinct v."en_numdoc") as pedidos,
                   count(distinct (v."en_codcli", v."en_codigo")) as clientes_produtos
              from v
              join ref on v.dia >= ref.mes and v.dia < ref.mes + interval '1 month'
             group by ref.mes
            """
        ).format(
            dia=data_texto("en_datemicao"),
            tabela=sql.Identifier("mart", tabela),
            filtro=filtro_empresa(ctx),
        ),
        {"cliente": ctx.cliente_id, "empresas": list(ctx.empresas)},
    ).fetchone()
    if linha is None:
        return None
    return MesDeVendas(
        mes=linha["mes"],
        ultimo_dia=linha["ultimo_dia"],
        faturado=linha["faturado"],
        custo=linha["custo"],
        clientes=linha["clientes"],
        clientes_produtos=linha["clientes_produtos"],
        pedidos=linha["pedidos"],
    )


def _metas(conn: Conexao, ctx: ContextoConsolidado, de: date, ate: date) -> dict[date, Decimal]:
    """Meta por mês, de `de` até `ate` (meses inteiros); meses sem meta ficam de fora."""
    tabela = tabela_do_cliente(conn, ctx.cliente_id, "f_metas_")
    if tabela is None:
        return {}
    competencia = data_texto("Competencia")
    return {
        r["mes"]: r["meta"]
        for r in conn.execute(
            sql.SQL(
                """
                select date_trunc('month', {competencia})::date as mes, sum("Valor") as meta
                  from {tabela}
                 where _cliente_id = %(cliente)s and {filtro}
                   and {competencia} >= %(de)s
                   and {competencia} < %(ate)s::date + interval '1 month'
                 group by 1
                """
            ).format(
                tabela=sql.Identifier("mart", tabela),
                filtro=filtro_empresa(ctx),
                competencia=competencia,
            ),
            {"cliente": ctx.cliente_id, "empresas": list(ctx.empresas), "de": de, "ate": ate},
        )
        if r["meta"]
    }


def _resumo_mensal(
    conn: Conexao, ctx: ContextoConsolidado, tabela: str, de: date, ate: date
) -> Mensal:
    """Totais por mês e medida do histórico (mart.resumo_mensal); vazio sem a migration 8."""
    existe = conn.execute("select to_regclass('mart.resumo_mensal') is not null as ok").fetchone()
    if not (existe and existe["ok"]):
        return {}
    mensal: Mensal = {}
    for r in conn.execute(
        sql.SQL(
            """
            select mes, medida, sum(valor) as valor
              from mart.resumo_mensal
             where cliente_id = %(cliente)s and tabela = %(tabela)s and {filtro}
               and mes between %(de)s and %(ate)s
             group by mes, medida
            """
        ).format(filtro=filtro_empresa(ctx, "empresa_id")),
        {
            "cliente": ctx.cliente_id,
            "empresas": list(ctx.empresas),
            "tabela": tabela,
            "de": de,
            "ate": ate,
        },
    ):
        mensal.setdefault(r["mes"], {})[r["medida"]] = r["valor"]
    return mensal


def _periodo(faturado: Decimal, meta: Decimal, f: Farol, referencia: str) -> IndicadorPeriodo:
    pct = faturado / meta * 100
    delta = faturado - meta
    return IndicadorPeriodo(
        status=f.status(pct),
        valor=Valor(valor=pct, unidade="pct", casas=1),
        ponteiro=f.ponteiro(pct),
        delta=Valor(valor=delta, unidade="brl", casas=1),
        delta_ruim=delta < 0,
        referencia=referencia,
    )


def indicador_vendas_meta(
    ref: date,
    faturado: dict[date, Decimal],
    metas: dict[date, Decimal],
    f: Farol,
    projecao: Decimal | None = None,
) -> Indicador | None:
    """Velocímetro "Vendas contra a meta"; None sem meta no mês ou com menos de 2 meses
    de histórico (a tendência precisa de uma linha).

    Com `projecao`, o mês de referência usa a projeção de fechamento (no período "mês" e
    no último ponto da tendência); o acumulado do ano continua com o realizado.
    """
    if ref not in faturado or ref not in metas:
        return None
    comparaveis = [m for m in sorted(faturado) if m <= ref and m in metas]
    tendencia = comparaveis[-MESES_TENDENCIA:]
    if len(tendencia) < 2:
        return None

    if projecao is None:
        mes = _periodo(
            faturado[ref],
            metas[ref],
            f,
            f"{moeda(faturado[ref])} contra meta de {moeda(metas[ref])}",
        )
    else:
        mes = _periodo(
            projecao,
            metas[ref],
            f,
            f"projeção de {moeda(projecao)} contra meta de {moeda(metas[ref])}",
        )
    meses_ano = [m for m in comparaveis if m.year == ref.year]
    fat_ano = sum((faturado[m] for m in meses_ano), Decimal(0))
    meta_ano = sum((metas[m] for m in meses_ano), Decimal(0))
    intervalo = (
        MESES_CURTOS[ref.month - 1]
        if len(meses_ano) == 1
        else f"{MESES_CURTOS[meses_ano[0].month - 1]} a {MESES_CURTOS[ref.month - 1]}"
    )
    ano = _periodo(
        fat_ano, meta_ano, f, f"{moeda(fat_ano)} de {intervalo} contra meta de {moeda(meta_ano)}"
    )

    return Indicador(
        id="vendas-meta",
        nome="Vendas contra a meta",
        origem="dw",
        gauge=Gauge(
            min=f.minimo,
            max=f.maximo,
            meta=f.meta,
            unidade="pct",
            faixas=[FaixaGauge(de=de, ate=ate, status=s) for de, ate, s in f.faixas()],
        ),
        periodos=PeriodosIndicador(mes=mes, ano=ano),
        tendencia=Tendencia(
            rotulos=[MESES_CURTOS[m.month - 1] for m in tendencia],
            real=[
                projecao if m == ref and projecao is not None else faturado[m] for m in tendencia
            ],
            referencia=[metas[m] for m in tendencia],
            unidade="brl",
            casas=2,
        ),
    )


def _mes_menos(mes: date, meses: int) -> date:
    indice = mes.year * 12 + mes.month - 1 - meses
    return date(indice // 12, indice % 12 + 1, 1)


def secao_vendas(ctx: ContextoConsolidado) -> tuple[SecaoDoMes, BaseVendas] | None:
    """Seção Vendas e a base para a de Custos; None se o cliente não tem tabela de vendas
    ou não há venda no recorte."""
    conn = ctx.sessao.conn
    tabela = tabela_do_cliente(conn, ctx.cliente_id, "f_vendas_")
    if tabela is None:
        return None
    v = _vendas_do_mes(conn, ctx, tabela)
    if v is None:
        return None

    # histórico: tendência, acumulado do ano e média dos 6 meses anteriores
    inicio = min(date(v.mes.year, 1, 1), _mes_menos(v.mes, MESES_TENDENCIA))
    metas = _metas(conn, ctx, inicio, v.mes)
    mensal = _resumo_mensal(conn, ctx, tabela, inicio, v.mes)
    # o mês de referência vem da tabela, que é do mesmo arquivo do resumo
    mensal[v.mes] = {
        "faturado": v.faturado,
        "pedidos": Decimal(v.pedidos),
        "clientes": Decimal(v.clientes),
        "clientes_produtos": Decimal(v.clientes_produtos),
        "custo": v.custo,
    }
    faturado = {m: t["faturado"] for m, t in mensal.items() if "faturado" in t}
    contra_referencia = {
        d.id: indicador_contra_referencia(
            d, v.mes, mensal, farol(conn, ctx.cliente_id, d.chave_farol, v.mes)
        )
        for d in (CLIENTES, TICKET, PRODUTOS)
    }

    f = farol(conn, ctx.cliente_id, INDICADOR_META, v.mes)
    meta = metas.get(v.mes)
    feriados_mes = feriados(conn, ctx.cliente_id, v.mes, ultimo_dia_do_mes(v.mes))
    proj = projetar(v.faturado, meta, v.ultimo_dia, feriados_mes) if meta is not None else None

    indicadores = [
        indicador_vendas_meta(v.mes, faturado, metas, f, proj.projecao if proj else None),
        *contra_referencia.values(),
    ]
    nome_mes = MESES[v.mes.month - 1].lower()
    secao = Secao(
        id="vendas",
        titulo="Vendas",
        modulo=None,
        resumo=f"Dados de {nome_mes} de {v.mes.year}.",
        kpis=kpis_vendas(v.mes, v.ultimo_dia, v.faturado, meta, proj, f),
        cartoes=[],
        indicadores=[i for i in indicadores if i is not None],
    )
    base = BaseVendas(tabela=tabela, mes=v, mensal=mensal, inicio=inicio, feriados=feriados_mes)
    return SecaoDoMes(secao=secao, mes=v.mes), base


def kpis_vendas(
    mes: date,
    ultimo_dia: date,
    faturado: Decimal,
    meta: Decimal | None,
    proj: Projecao | None,
    f: Farol,
) -> list[Kpi]:
    """Meta, faturado, projeção de fechamento e a realizar por dia útil.

    - sem meta: só o faturado;
    - mês fechado (sem dia útil restante): sem "A realizar", e a projeção é o faturado;
    - meta já batida: "A realizar" fica em zero, com "meta atingida".
    O status do faturado compara com a meta proporcional aos dias úteis passados; o de
    "A realizar", o ritmo atual com o ritmo necessário.
    """
    nome_mes = MESES[mes.month - 1].lower()
    ate = ultimo_dia.strftime("%d/%m")
    if meta is None:
        return [
            Kpi(
                rotulo=f"Faturado em {nome_mes}",
                valor=Valor(valor=faturado, unidade="brl"),
                contexto=f"vendas até {ate}",
            )
        ]

    kpis = [
        Kpi(
            rotulo=f"Meta de {nome_mes}",
            valor=Valor(valor=meta, unidade="brl"),
            contexto="meta comercial do mês",
        )
    ]
    if proj is None:
        kpis.append(
            Kpi(
                rotulo=f"Faturado em {nome_mes}",
                valor=Valor(valor=faturado, unidade="brl"),
                contexto=f"vendas até {ate}",
                status=f.status(faturado / meta * 100),
            )
        )
        return kpis

    kpis.append(
        Kpi(
            rotulo=f"Faturado até {ate}",
            valor=Valor(valor=faturado, unidade="brl"),
            contexto=f"meta até a data: {moeda(proj.meta_ate_data)}",
            status=f.status(faturado / proj.meta_ate_data * 100) if proj.meta_ate_data else None,
        )
    )
    kpis.append(
        Kpi(
            rotulo="Projeção de fechamento",
            valor=Valor(valor=proj.projecao, unidade="brl"),
            contexto=(
                f"{moeda(proj.diferenca)} abaixo da meta"
                if proj.diferenca < 0
                else f"{moeda(proj.diferenca)} acima da meta"
            ),
            status=f.status(proj.projecao / meta * 100),
        )
    )
    if proj.a_realizar is not None:
        if proj.a_realizar <= 0:
            kpis.append(
                Kpi(
                    rotulo="A realizar por dia útil",
                    valor=Valor(valor=Decimal(0), unidade="brl"),
                    contexto="meta atingida",
                    status="saudavel",
                )
            )
        else:
            kpis.append(
                Kpi(
                    rotulo="A realizar por dia útil",
                    valor=Valor(valor=proj.a_realizar, unidade="brl"),
                    contexto=f"hoje vende {moeda(proj.ritmo)} por dia",
                    status=f.status(proj.ritmo / proj.a_realizar * 100),
                )
            )
    return kpis

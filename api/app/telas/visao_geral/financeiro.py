"""Seção Financeiro da Visão geral. REGRAS PROVISÓRIAS: confirmar com o negócio (ver TODO.md).

Lê os títulos a receber (f_receitas_*) e a pagar (f_despesas_*) que o cliente tem em
cfg.fonte_carga: datas de emissão, vencimento e pagamento e valor do título. Colunas por
papel: COLUNAS_RECEBER e COLUNAS_PAGAR (as da PJ Cortes), trocadas pelas de
cfg.fonte_carga.colunas_modelo. Essas tabelas precisam entrar SEM janela de datas: título
antigo ainda em aberto e a série de 12 meses dependem do arquivo inteiro.

- "hoje": a data da última carga;
- título em aberto numa data: emitido até ela e sem pagamento até ela. Pagamento anterior
  à emissão (data vazia gravada como 1899 ou 1900 em alguns ERPs) conta como sem
  pagamento, e sem vencimento vale a emissão. O valor em aberto é o do título inteiro
  (baixa parcial fica de fora);
- vencido (modelo D+2): em aberto com vencimento + 2 dias úteis antes da data. O pagamento
  feito no vencimento pode levar até 2 dias úteis para compensar; até lá o título ainda
  está a vencer. Vale para a receber e a pagar. As faixas de atraso contam os dias
  corridos desde o vencimento;
- cartões: a receber a vencer e vencido, a pagar nos próximos 30 dias (com quanto isso é
  da média paga por mês nos 6 meses fechados anteriores) e a pagar vencido;
- inadimplência: vencido ÷ carteira em aberto hoje, por faixa de atraso, e a mesma conta
  no fim de cada um dos 12 meses fechados anteriores; farol contra a média dessa série
  (farol.inadimplencia, em p.p.);
- prazo médio de recebimento e de pagamento: dias entre emissão e pagamento, ponderados
  pelo valor, dos títulos pagos em cada mês, contra a média dos 6 meses anteriores.

Saldo em conta, cobertura de caixa e ciclo de caixa ficam de fora: não há fonte.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from psycopg import sql

from app.core.db import Conexao
from app.farol.parametro import Farol, deslocado, farol
from app.seguranca.acesso import ContextoConsolidado
from app.telas.calendario import feriados, mais_dias_uteis, ultimo_dia_do_mes
from app.telas.contrato import Status
from app.telas.visao_geral.contra_referencia import Mensal, Razao, rotulo_eixo
from app.telas.visao_geral.contrato import (
    CartaoInadimplencia,
    Indicador,
    ItemCarteira,
    Kpi,
    Secao,
    SerieInadimplencia,
    Trecho,
    Valor,
)
from app.telas.visao_geral.relativo import MESES_REFERENCIA, Medidor, Serie, indicador_relativo
from app.telas.visao_geral.texto import moeda, numero
from app.telas.visao_geral.vendas import (
    SecaoDoMes,
    TabelaDoCliente,
    de_todas,
    filtro_empresa,
    mes_menos,
    tabelas_do_cliente,
)

# papel → coluna (as da PJ Cortes)
COLUNAS_RECEBER: Mapping[str, str] = {
    "emissao": "en_dataemissao",
    "vencimento": "en_datavencimento",
    "pagamento": "en_datapagamento",
    "valor": "en_valor",
}
COLUNAS_PAGAR: Mapping[str, str] = {
    "emissao": "en_datemi",
    "vencimento": "en_datven",
    "pagamento": "en_datpag",
    "valor": "en_valor",
}
MESES_SERIE = 12
DIAS_A_PAGAR = 30
# D+2: dias úteis de compensação depois do vencimento
DIAS_COMPENSACAO = 2


def _dias(v: Decimal) -> str:
    return f"{numero(v)} dias"


PRAZO_RECEBIMENTO = Medidor(
    id="prazo-recebimento",
    nome="Prazo médio de recebimento",
    unidade="dias",
    unidade_delta="dias",
    casas=0,
    texto=_dias,
    contexto="dos títulos recebidos no mês",
)
PRAZO_PAGAMENTO = Medidor(
    id="prazo-pagamento",
    nome="Prazo médio de pagamento",
    unidade="dias",
    unidade_delta="dias",
    casas=0,
    texto=_dias,
    contexto="dos títulos pagos no mês",
)
PRAZO = Razao("dias_valor", "valor")


@dataclass(frozen=True)
class Carteira:
    """Títulos em aberto numa data, por faixa de atraso."""

    a_vencer: Decimal
    # a vencer nos próximos DIAS_A_PAGAR dias (parte de a_vencer)
    proximos_dias: Decimal
    ate_30: Decimal
    ate_60: Decimal
    ate_90: Decimal
    mais_90: Decimal

    @property
    def vencido(self) -> Decimal:
        return self.ate_30 + self.ate_60 + self.ate_90 + self.mais_90

    @property
    def total(self) -> Decimal:
        return self.a_vencer + self.vencido


@dataclass(frozen=True)
class PontoSerie:
    # último dia do mês
    dia: date
    vencido: Decimal
    aberto: Decimal


def ultimo_vencimento_vencido(dia: date, feriados: set[date]) -> date:
    """Vencimento mais recente que já está vencido em `dia` no modelo D+2: aquele cujo
    vencimento + DIAS_COMPENSACAO dias úteis cai antes de `dia`."""
    vencimento = dia - timedelta(days=1)
    while mais_dias_uteis(vencimento, DIAS_COMPENSACAO, feriados) >= dia:
        vencimento -= timedelta(days=1)
    return vencimento


def _titulos(tabelas: list[TabelaDoCliente], ctx: ContextoConsolidado) -> sql.Composable:
    campos = {
        "emissao": "emissao",
        "vencimento": "vencimento",
        "pagamento": "pagamento",
        "valor": "valor",
    }
    return sql.SQL(
        """
        select emissao,
               coalesce(vencimento, emissao) as vencimento,
               case when pagamento >= emissao then pagamento end as pagamento,
               valor
          from ({linhas}) t
         where emissao is not null and valor > 0
        """
    ).format(linhas=de_todas(tabelas, campos, filtro_empresa(ctx)))


def _carteira(
    conn: Conexao,
    ctx: ContextoConsolidado,
    tabelas: list[TabelaDoCliente],
    hoje: date,
    limite: date,
) -> Carteira:
    """Em aberto em `hoje`; vencido = vencimento até `limite` (ultimo_vencimento_vencido)."""
    linha = conn.execute(
        sql.SQL(
            """
            with t as ({titulos}),
            a as (
                select %(hoje)s::date - vencimento as atraso,
                       vencimento <= %(limite)s as vencido,
                       valor
                  from t
                 where emissao <= %(hoje)s and (pagamento is null or pagamento > %(hoje)s)
            )
            select coalesce(sum(valor) filter (where not vencido), 0) as a_vencer,
                   coalesce(sum(valor) filter (where not vencido and atraso >= -%(dias)s::int),
                            0) as proximos_dias,
                   coalesce(sum(valor) filter (where vencido and atraso <= 30), 0) as ate_30,
                   coalesce(sum(valor) filter (where vencido and atraso between 31 and 60), 0)
                       as ate_60,
                   coalesce(sum(valor) filter (where vencido and atraso between 61 and 90), 0)
                       as ate_90,
                   coalesce(sum(valor) filter (where vencido and atraso > 90), 0) as mais_90
              from a
            """
        ).format(titulos=_titulos(tabelas, ctx)),
        {
            "cliente": ctx.cliente_id,
            "empresas": list(ctx.empresas),
            "hoje": hoje,
            "limite": limite,
            "dias": DIAS_A_PAGAR,
        },
    ).fetchone()
    if linha is None:  # agregado sem group by sempre devolve uma linha
        raise LookupError("carteira sem linha")
    return Carteira(**linha)


def _serie(
    conn: Conexao,
    ctx: ContextoConsolidado,
    tabelas: list[TabelaDoCliente],
    limites: Mapping[date, date],
) -> list[PontoSerie]:
    """Vencido e em aberto em cada dia de `limites` (dia → ultimo_vencimento_vencido)."""
    # materialized: sem ele o Postgres refaz os títulos (e a conversão das datas que ainda
    # estão em texto) para cada dia
    return [
        PontoSerie(dia=r["dia"], vencido=r["vencido"], aberto=r["aberto"])
        for r in conn.execute(
            sql.SQL(
                """
                with t as materialized ({titulos}),
                fim as (
                    select * from unnest(%(dias)s::date[], %(limites)s::date[]) as f (dia, limite)
                )
                select fim.dia,
                       coalesce(sum(t.valor) filter (where t.vencimento <= fim.limite), 0)
                           as vencido,
                       coalesce(sum(t.valor), 0) as aberto
                  from fim
                  left join t
                    on t.emissao <= fim.dia and (t.pagamento is null or t.pagamento > fim.dia)
                 group by fim.dia
                 order by fim.dia
                """
            ).format(titulos=_titulos(tabelas, ctx)),
            {
                "cliente": ctx.cliente_id,
                "empresas": list(ctx.empresas),
                "dias": list(limites),
                "limites": list(limites.values()),
            },
        )
    ]


def _pagos_por_mes(
    conn: Conexao, ctx: ContextoConsolidado, tabelas: list[TabelaDoCliente], de: date, hoje: date
) -> Mensal:
    """Por mês de pagamento: valor pago e dias até o pagamento × valor (para o prazo)."""
    return {
        r["mes"]: {"valor": r["valor"], "dias_valor": r["dias_valor"]}
        for r in conn.execute(
            sql.SQL(
                """
                with t as ({titulos})
                select date_trunc('month', pagamento)::date as mes,
                       sum((pagamento - emissao) * valor) as dias_valor,
                       sum(valor) as valor
                  from t
                 where pagamento >= %(de)s and pagamento <= %(hoje)s
                 group by 1
                """
            ).format(titulos=_titulos(tabelas, ctx)),
            {"cliente": ctx.cliente_id, "empresas": list(ctx.empresas), "de": de, "hoje": hoje},
        )
    }


def media_paga(pagos: Mensal, hoje: date) -> Decimal | None:
    """Média paga por mês nos até 6 meses fechados anteriores ao de `hoje` com pagamento."""
    aberto = date(hoje.year, hoje.month, 1)
    meses = [m for m in sorted(pagos) if m < aberto][-MESES_REFERENCIA:]
    if not meses:
        return None
    return sum((pagos[m]["valor"] for m in meses), Decimal(0)) / len(meses)


def _status_carteira(rotulo: str) -> Status:
    return (
        "saudavel" if rotulo == "A vencer" else "atencao" if rotulo == "1 a 30 dias" else "critico"
    )


def cartao_inadimplencia(
    hoje: date, carteira: Carteira, serie: list[PontoSerie], base: Farol
) -> CartaoInadimplencia | None:
    """Vencido sobre a carteira a receber; None sem carteira ou com menos de 2 meses de
    série (sem média para comparar)."""
    if not carteira.total:
        return None
    pontos = [(p.dia, p.vencido / p.aberto * 100) for p in serie if p.aberto]
    if len(pontos) < 2:
        return None
    pct = carteira.vencido / carteira.total * 100
    media = sum((v for _, v in pontos), Decimal(0)) / len(pontos)
    faixas = [
        ("A vencer", carteira.a_vencer),
        ("1 a 30 dias", carteira.ate_30),
        ("31 a 60 dias", carteira.ate_60),
        ("61 a 90 dias", carteira.ate_90),
        ("Mais de 90", carteira.mais_90),
    ]
    return CartaoInadimplencia(
        tipo="inadimplencia",
        status=deslocado(base, media).status(pct),
        percentual=Valor(valor=pct, unidade="pct", casas=1),
        texto=[
            Trecho(texto="De cada "),
            Trecho(texto="R$ 100", destaque=True),
            Trecho(texto=" que a empresa tem para receber, "),
            Trecho(texto=f"R$ {numero(pct, 2)}", destaque=True),
            Trecho(texto=f" já venceram. A média de {len(pontos)} meses é R$ {numero(media, 2)}."),
        ],
        nota=(
            f"Carteira em aberto em {hoje:%d/%m}: {moeda(carteira.total)}. "
            "O título conta como vencido depois de 2 dias úteis do vencimento (D+2)."
        ),
        carteira=[
            ItemCarteira(
                rotulo=rotulo,
                valor=Valor(valor=valor, unidade="brl"),
                percentual=valor / carteira.total * 100,
                status=_status_carteira(rotulo),
            )
            for rotulo, valor in faixas
        ],
        serie=SerieInadimplencia(
            rotulos=[rotulo_eixo(dia) for dia, _ in pontos],
            valores=[v for _, v in pontos],
            media=media,
        ),
    )


def kpis_financeiro(
    receber: Carteira | None,
    pagar: Carteira | None,
    media_paga_mes: Decimal | None,
    status_inadimplencia: Status | None,
    f_pagar: Farol,
) -> list[Kpi]:
    """A receber a vencer e vencido, a pagar nos próximos 30 dias e a pagar vencido."""
    kpis: list[Kpi] = []
    if receber is not None and receber.total:
        kpis += [
            Kpi(
                rotulo="A receber a vencer",
                valor=Valor(valor=receber.a_vencer, unidade="brl"),
                contexto=f"carteira de {moeda(receber.total)}",
            ),
            Kpi(
                rotulo="A receber vencido",
                valor=Valor(valor=receber.vencido, unidade="brl"),
                contexto=f"{numero(receber.vencido / receber.total * 100, 1)}% da carteira",
                status=status_inadimplencia,
            ),
        ]
    if pagar is not None and pagar.total:
        if media_paga_mes:
            cobertura = pagar.proximos_dias / media_paga_mes * 100
            contexto = f"só o que está lançado: {numero(cobertura)}% da média paga"
            status: Status | None = f_pagar.status(cobertura)
        else:
            contexto, status = "títulos lançados no sistema", None
        kpis += [
            Kpi(
                rotulo=f"A pagar, {DIAS_A_PAGAR} dias",
                valor=Valor(valor=pagar.proximos_dias, unidade="brl"),
                contexto=contexto,
                status=status,
            ),
            Kpi(
                rotulo="A pagar vencido",
                valor=Valor(valor=pagar.vencido, unidade="brl"),
                contexto="títulos em aberto já vencidos",
            ),
        ]
    return kpis


def _prazo(m: Medidor, pagos: Mensal, hoje: date, base: Farol) -> Indicador | None:
    """Prazo médio do mês mais recente com pagamento, até o mês de `hoje`."""
    meses = [x for x in pagos if x <= hoje and PRAZO.tem(pagos, x)]
    if not meses:
        return None
    return indicador_relativo(m, max(meses), Serie(pagos, PRAZO), None, base)


def secao_financeiro(ctx: ContextoConsolidado, hoje: date) -> SecaoDoMes | None:
    """Seção Financeiro; None sem tabela de títulos ou sem nada em aberto nem pago."""
    conn = ctx.sessao.conn
    a_receber = tabelas_do_cliente(conn, ctx.cliente_id, "f_receitas_", COLUNAS_RECEBER)
    a_pagar = tabelas_do_cliente(conn, ctx.cliente_id, "f_despesas_", COLUNAS_PAGAR)
    if not a_receber and not a_pagar:
        return None
    aberto = date(hoje.year, hoje.month, 1)
    de = min(date(hoje.year, 1, 1), mes_menos(aberto, MESES_SERIE))
    # um mês antes do primeiro dia da série: o D+2 de um vencimento volta alguns dias
    feriados_periodo = feriados(conn, ctx.cliente_id, mes_menos(de, 1), hoje)
    limite = ultimo_vencimento_vencido(hoje, feriados_periodo)

    cartoes: list[CartaoInadimplencia] = []
    indicadores: list[Indicador] = []
    receber: Carteira | None = None
    pagar: Carteira | None = None
    pagos_pagar: Mensal = {}
    if a_receber:
        receber = _carteira(conn, ctx, a_receber, hoje, limite)
        fins = [ultimo_dia_do_mes(mes_menos(aberto, n)) for n in range(MESES_SERIE, 0, -1)]
        serie = _serie(
            conn, ctx, a_receber, {d: ultimo_vencimento_vencido(d, feriados_periodo) for d in fins}
        )
        cartao = cartao_inadimplencia(
            hoje, receber, serie, farol(conn, ctx.cliente_id, "inadimplencia", hoje)
        )
        if cartao is not None:
            cartoes.append(cartao)
        pagos_receber = _pagos_por_mes(conn, ctx, a_receber, de, hoje)
        ind = _prazo(
            PRAZO_RECEBIMENTO,
            pagos_receber,
            hoje,
            farol(conn, ctx.cliente_id, "prazo_recebimento", hoje),
        )
        if ind is not None:
            indicadores.append(ind)
    if a_pagar:
        pagar = _carteira(conn, ctx, a_pagar, hoje, limite)
        pagos_pagar = _pagos_por_mes(conn, ctx, a_pagar, de, hoje)
        ind = _prazo(
            PRAZO_PAGAMENTO, pagos_pagar, hoje, farol(conn, ctx.cliente_id, "prazo_pagamento", hoje)
        )
        if ind is not None:
            indicadores.append(ind)

    kpis = kpis_financeiro(
        receber,
        pagar,
        media_paga(pagos_pagar, hoje),
        cartoes[0].status if cartoes else None,
        farol(conn, ctx.cliente_id, "pagar_lancado", hoje),
    )
    if not kpis and not indicadores:
        return None
    secao = Secao(
        id="financeiro",
        titulo="Financeiro",
        modulo=None,
        resumo=f"Posição em {hoje:%d/%m/%Y}, com os títulos lançados no sistema.",
        kpis=kpis,
        cartoes=cartoes,
        indicadores=indicadores,
    )
    return SecaoDoMes(secao=secao, mes=aberto)

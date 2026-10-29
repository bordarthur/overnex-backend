"""Seção Financeiro da Visão geral. REGRAS PROVISÓRIAS: confirmar com o negócio (ver TODO.md).

Lê os títulos a receber (f_receitas_*) e a pagar (f_despesas_*) que o cliente tem em
cfg.fonte_carga: datas de emissão, vencimento e pagamento e valor do título. Colunas por
papel: COLUNAS_RECEBER e COLUNAS_PAGAR (as da PJ Cortes), trocadas pelas de
cfg.fonte_carga.colunas_modelo. Essas tabelas precisam entrar SEM janela de datas: título
antigo ainda em aberto e a série de 12 meses dependem do arquivo inteiro.

- "hoje": a data da última carga;
- título em aberto numa data: emitido até ela e sem pagamento até ela. Pagamento antes de
  1901 (data vazia gravada como 1899 ou 1900 em alguns ERPs) conta como sem pagamento;
  pagamento anterior à emissão (adiantamento) conta como pago. Sem vencimento vale a
  emissão. O valor em aberto é o do título inteiro (baixa parcial fica de fora);
- modelo D+2, como no Power BI da PJ: o pagamento feito no vencimento pode levar 2 dias
  úteis para compensar. A vencer: vencimento a partir da data; vencido: vencimento + 2 dias
  úteis até a data; entre os dois, em compensação (fora dos dois cartões, dentro da
  carteira). Vale para a receber e a pagar. As faixas de atraso contam os dias corridos
  desde o vencimento;
- cartões do topo: a receber a vencer e vencido, a pagar nos próximos 30 dias (com quanto
  isso é da média paga por mês nos 6 meses fechados anteriores) e a pagar vencido;
- próximos 7, 15 e 30 dias (JANELAS): a receber e a pagar lançado a vencer na janela
  (vencimento de hoje até hoje + N dias; o que está em compensação fica fora, como no
  "a vencer"); o que falta lançar = máx(0, média paga × N/30 − a pagar da janela); o fim
  é a receber − a pagar (só o lançado) e isso menos o que falta (com o que falta lançar).
  Fim ≥ 0 é saudável, < 0 atenção; o status de cada modo é o pior das janelas. Sem saldo
  em conta: o fim é a sobra ou a falta da janela, não o caixa;
- contas a pagar lançadas: o lançado nos próximos 30 dias ÷ a média paga por mês, em %
  (a mesma conta do cartão "A pagar, 30 dias"), com farol.pagar_lancado (padrão: abaixo
  de 50 crítico, de 50 a 80 atenção, a partir de 80 saudável), e o pago em cada um dos 6
  meses fechados;
- inadimplência: vencido ÷ carteira em aberto hoje, por faixa de atraso, e a mesma conta
  no fim de cada um dos 12 meses fechados anteriores; farol contra a média dessa série
  (farol.inadimplencia, em p.p.).

Sem velocímetros: os prazos médios de recebimento e de pagamento ficam no painel Receber e
pagar. Saldo em conta, cobertura de caixa e ciclo de caixa ficam de fora: não há fonte.
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
from app.telas.moldura import MESES
from app.telas.visao_geral.contra_referencia import Mensal, rotulo_eixo
from app.telas.visao_geral.contrato import (
    CartaoInadimplencia,
    CartaoPagarLancado,
    CartaoProximosDias,
    CartaoSecao,
    FimJanela,
    ItemCarteira,
    JanelaProximosDias,
    Kpi,
    LegendaProximosDias,
    ModoProximosDias,
    PagoNoMes,
    ProximosLancado,
    Secao,
    SerieInadimplencia,
    Trecho,
    Valor,
    ValorRotulado,
)
from app.telas.visao_geral.relativo import MESES_REFERENCIA
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
# janelas do cartão de próximos dias, em dias corridos a partir de hoje
JANELAS = (7, 15, DIAS_A_PAGAR)
# D+2: dias úteis de compensação depois do vencimento
DIAS_COMPENSACAO = 2

MODO_LANCADO = "lancado"
MODO_COM_FALTA = "com-falta"
PIOR: Mapping[Status, int] = {"saudavel": 0, "atencao": 1, "critico": 2}


@dataclass(frozen=True)
class Carteira:
    """Títulos em aberto numa data, por faixa de atraso."""

    # vencimento a partir da data
    a_vencer: Decimal
    # dias da janela (JANELAS) → a vencer de hoje até hoje + dias (parte de a_vencer)
    proximos: Mapping[int, Decimal]
    ate_30: Decimal
    ate_60: Decimal
    ate_90: Decimal
    mais_90: Decimal
    # já passou do vencimento, mas ainda dentro dos 2 dias úteis do D+2
    compensando: Decimal = Decimal(0)

    @property
    def proximos_dias(self) -> Decimal:
        """A vencer nos próximos DIAS_A_PAGAR dias."""
        return self.proximos.get(DIAS_A_PAGAR, Decimal(0))

    @property
    def vencido(self) -> Decimal:
        return self.ate_30 + self.ate_60 + self.ate_90 + self.mais_90

    @property
    def total(self) -> Decimal:
        return self.a_vencer + self.compensando + self.vencido


@dataclass(frozen=True)
class PontoSerie:
    # último dia do mês
    dia: date
    vencido: Decimal
    aberto: Decimal


def ultimo_vencimento_vencido(dia: date, feriados: set[date]) -> date:
    """Vencimento mais recente que já está vencido em `dia` no modelo D+2: aquele cujo
    vencimento + DIAS_COMPENSACAO dias úteis cai até `dia` (no próprio D+2 já venceu)."""
    vencimento = dia - timedelta(days=1)
    while mais_dias_uteis(vencimento, DIAS_COMPENSACAO, feriados) > dia:
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
               -- antes de 1901: data vazia gravada como 1899/1900
               case when pagamento >= date '1901-01-01' then pagamento end as pagamento,
               valor
          from ({linhas}) t
         where emissao is not null and valor > 0
        """
    ).format(linhas=de_todas(tabelas, campos, filtro_empresa(ctx)))


def _proximos() -> sql.Composable:
    """O a vencer de cada janela (JANELAS), na ordem, num array."""
    return sql.SQL("array[{}]").format(
        sql.SQL(", ").join(
            sql.SQL("coalesce(sum(valor) filter (where atraso between {} and 0), 0)").format(
                sql.Literal(-dias)
            )
            for dias in JANELAS
        )
    )


@dataclass(frozen=True)
class Leitura:
    """As três contas de uma tabela de títulos, de uma leitura só."""

    carteira: Carteira
    serie: list[PontoSerie]
    # por mês de pagamento: valor pago
    pagos: Mensal


def _ler(
    conn: Conexao,
    ctx: ContextoConsolidado,
    tabelas: list[TabelaDoCliente],
    hoje: date,
    limite: date,
    limites_serie: Mapping[date, date],
    de: date,
) -> Leitura:
    """Lê os títulos uma vez (CTE materializada) e calcula, na mesma consulta:
    - carteira: em aberto em `hoje`; vencido = vencimento até `limite`
      (ultimo_vencimento_vencido); a vencer em cada janela de JANELAS;
    - série: vencido e em aberto em cada dia de `limites_serie` (dia →
      ultimo_vencimento_vencido); vazio, sem série;
    - pagos: por mês de pagamento, de `de` até `hoje`, o valor pago.
    """
    linhas = conn.execute(
        sql.SQL(
            """
            with t as materialized ({titulos}),
            a as (
                select %(hoje)s::date - vencimento as atraso,
                       vencimento <= %(limite)s as vencido,
                       valor
                  from t
                 where emissao <= %(hoje)s and (pagamento is null or pagamento > %(hoje)s)
            ),
            fim as (
                select * from unnest(%(dias)s::date[], %(limites)s::date[]) as f (dia, limite)
            )
            select 'carteira' as parte,
                   null::date as dia,
                   coalesce(sum(valor) filter (where atraso <= 0), 0) as a_vencer,
                   {proximos} as proximos,
                   coalesce(sum(valor) filter (where atraso > 0 and not vencido), 0)
                       as compensando,
                   coalesce(sum(valor) filter (where vencido and atraso <= 30), 0) as ate_30,
                   coalesce(sum(valor) filter (where vencido and atraso between 31 and 60), 0)
                       as ate_60,
                   coalesce(sum(valor) filter (where vencido and atraso between 61 and 90), 0)
                       as ate_90,
                   coalesce(sum(valor) filter (where vencido and atraso > 90), 0) as mais_90,
                   null::numeric as vencido,
                   null::numeric as aberto,
                   null::numeric as pago
              from a
            union all
            select 'serie', fim.dia, null, null, null, null, null, null, null,
                   coalesce(sum(t.valor) filter (where t.vencimento <= fim.limite), 0),
                   coalesce(sum(t.valor), 0),
                   null
              from fim
              left join t
                on t.emissao <= fim.dia and (t.pagamento is null or t.pagamento > fim.dia)
             group by fim.dia
            union all
            select 'pagos', date_trunc('month', pagamento)::date,
                   null, null, null, null, null, null, null, null, null,
                   sum(valor)
              from t
             where pagamento >= %(de)s and pagamento <= %(hoje)s
             group by 2
            """
        ).format(titulos=_titulos(tabelas, ctx), proximos=_proximos()),
        {
            "cliente": ctx.cliente_id,
            "empresas": list(ctx.empresas),
            "hoje": hoje,
            "limite": limite,
            "dias": list(limites_serie),
            "limites": list(limites_serie.values()),
            "de": de,
        },
    ).fetchall()
    carteira: Carteira | None = None
    serie: list[PontoSerie] = []
    pagos: Mensal = {}
    for r in linhas:
        if r["parte"] == "carteira":
            carteira = Carteira(
                a_vencer=r["a_vencer"],
                proximos=dict(zip(JANELAS, r["proximos"], strict=True)),
                ate_30=r["ate_30"],
                ate_60=r["ate_60"],
                ate_90=r["ate_90"],
                mais_90=r["mais_90"],
                compensando=r["compensando"],
            )
        elif r["parte"] == "serie":
            serie.append(PontoSerie(dia=r["dia"], vencido=r["vencido"], aberto=r["aberto"]))
        else:
            pagos[r["dia"]] = {"valor": r["pago"]}
    if carteira is None:  # agregado sem group by sempre devolve uma linha
        raise LookupError("carteira sem linha")
    return Leitura(carteira=carteira, serie=sorted(serie, key=lambda p: p.dia), pagos=pagos)


def meses_pagos(pagos: Mensal, hoje: date) -> list[date]:
    """Os até 6 meses fechados anteriores ao de `hoje` com pagamento, do mais antigo."""
    aberto = date(hoje.year, hoje.month, 1)
    return [m for m in sorted(pagos) if m < aberto][-MESES_REFERENCIA:]


def media_paga(pagos: Mensal, hoje: date) -> Decimal | None:
    """Média paga por mês nos até 6 meses fechados anteriores ao de `hoje` com pagamento."""
    meses = meses_pagos(pagos, hoje)
    if not meses:
        return None
    return sum((pagos[m]["valor"] for m in meses), Decimal(0)) / len(meses)


def _status_carteira(rotulo: str) -> Status:
    if rotulo in ("A vencer", "Em compensação"):
        return "saudavel"
    return "atencao" if rotulo == "1 a 30 dias" else "critico"


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
        # só aparece com valor: some no dia em que nada está compensando
        *([("Em compensação", carteira.compensando)] if carteira.compensando else []),
        ("1 a 30 dias", carteira.ate_30),
        ("31 a 60 dias", carteira.ate_60),
        ("61 a 90 dias", carteira.ate_90),
        ("Mais de 90", carteira.mais_90),
    ]
    return CartaoInadimplencia(
        tipo="inadimplencia",
        titulo="Inadimplência corrente",
        subtitulo="Quanto da carteira a receber já venceu, e há quanto tempo",
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
            "O título vence de fato 2 dias úteis depois do vencimento (D+2); até lá, fica "
            "em compensação."
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


def _brl(valor: Decimal) -> Valor:
    return Valor(valor=valor, unidade="brl")


def falta_lancar(media: Decimal, dias: int, pagar: Decimal) -> Decimal:
    """O que falta lançar a pagar para a janela chegar à média paga, proporcional aos dias."""
    return max(Decimal(0), media * dias / DIAS_A_PAGAR - pagar)


def _fim(valor: Decimal) -> FimJanela:
    if valor >= 0:
        return FimJanela(valor=_brl(valor), status="saudavel", texto="entra mais do que sai")
    return FimJanela(valor=_brl(valor), status="atencao", texto="sai mais do que entra")


def _pior(janelas: list[JanelaProximosDias], modo: str) -> Status | None:
    status = [s for j in janelas if (s := j.fim[modo].status) is not None]
    return max(status, key=lambda s: PIOR[s], default=None)


def cartao_proximos_dias(
    hoje: date, receber: Carteira, pagar: Carteira, media: Decimal | None
) -> CartaoProximosDias:
    """O que vence a receber menos o que vence a pagar em 7, 15 e 30 dias. Sem média paga
    (nenhum pagamento nos meses fechados), só o modo "Só o lançado"."""
    if media is not None and media <= 0:
        media = None
    janelas: list[JanelaProximosDias] = []
    for dias in JANELAS:
        a_receber = receber.proximos.get(dias, Decimal(0))
        a_pagar = pagar.proximos.get(dias, Decimal(0))
        falta = falta_lancar(media, dias, a_pagar) if media is not None else Decimal(0)
        fim = {MODO_LANCADO: _fim(a_receber - a_pagar)}
        if media is not None:
            fim[MODO_COM_FALTA] = _fim(a_receber - a_pagar - falta)
        janelas.append(
            JanelaProximosDias(
                titulo=f"{dias} dias",
                ate=f"até {hoje + timedelta(days=dias):%d/%m}",
                receber=_brl(a_receber),
                pagar=_brl(a_pagar),
                falta=_brl(falta),
                fim=fim,
            )
        )
    modos = [
        ModoProximosDias(
            id=MODO_LANCADO,
            rotulo="Só o lançado",
            com_falta=False,
            status=_pior(janelas, MODO_LANCADO),
        )
    ]
    if media is not None:
        modos.append(
            ModoProximosDias(
                id=MODO_COM_FALTA,
                rotulo="Com o que falta lançar",
                com_falta=True,
                status=_pior(janelas, MODO_COM_FALTA),
            )
        )
        nota = (
            "Só o lançado considera as contas a pagar que já estão no sistema; com o que falta "
            f"lançar completa cada janela até a média paga por mês ({moeda(media)}, "
            "proporcional aos dias), porque essas contas também vão vencer."
        )
    else:
        nota = (
            "Só o lançado considera as contas a pagar que já estão no sistema. Sem pagamentos "
            "nos meses fechados, não há média paga para estimar o que falta lançar."
        )
    return CartaoProximosDias(
        tipo="proximos-dias",
        titulo="Próximos 7, 15 e 30 dias",
        subtitulo=(
            "O que vence a receber, menos o que vence a pagar: quanto sobra ou falta em cada janela"
        ),
        explicacao=(
            "Cada linha mostra o que vence a receber na janela. Embaixo, o que vence a pagar "
            "volta do fim dessa barra. O traço marca quanto sobra ou falta. O saldo em conta "
            "ainda não entra: o sistema não tem essa fonte."
        ),
        nota=nota,
        modos=modos,
        legenda=LegendaProximosDias(
            saldo=None,
            receber="A receber",
            pagar="A pagar",
            falta="Falta lançar, pela média paga",
        ),
        saldo=None,
        minimo=None,
        janelas=janelas,
    )


def _mes_por_extenso(mes: date, com_ano: bool) -> str:
    nome = MESES[mes.month - 1].lower()
    return f"{nome} de {mes.year}" if com_ano else nome


def cartao_pagar_lancado(
    hoje: date, pagar: Carteira, pagos: Mensal, f: Farol
) -> CartaoPagarLancado | None:
    """O lançado a pagar nos próximos 30 dias contra a média paga por mês nos 6 meses
    fechados; None sem pagamento nesses meses."""
    meses = meses_pagos(pagos, hoje)
    media = media_paga(pagos, hoje)
    if media is None or media <= 0:
        return None
    lancado = pagar.proximos_dias
    pct = lancado / media * 100
    primeiro, ultimo = meses[0], meses[-1]
    if len(meses) == 1:
        nota = f"Em {_mes_por_extenso(ultimo, False)}, a empresa pagou {moeda(media)}."
    else:
        com_ano = primeiro.year != ultimo.year
        nota = (
            f"De {_mes_por_extenso(primeiro, com_ano)} a {_mes_por_extenso(ultimo, com_ano)}, "
            f"a empresa pagou em média {moeda(media)} por mês."
        )
    return CartaoPagarLancado(
        tipo="pagar-lancado",
        titulo="Contas a pagar lançadas",
        subtitulo=(
            f"O que já está lançado para os próximos {DIAS_A_PAGAR} dias, contra o que a "
            "empresa paga por mês"
        ),
        explicacao=(
            "As barras cinza são o que a empresa pagou em cada mês. A barra da direita é o "
            f"que já está lançado para vencer nos próximos {DIAS_A_PAGAR} dias; o hachurado "
            "é o que ainda falta para chegar à média paga."
        ),
        status=f.status(pct),
        percentual=Valor(valor=pct, unidade="pct", casas=0),
        texto=f"da média paga por mês já está lançada para os próximos {DIAS_A_PAGAR} dias.",
        nota=nota,
        pagos=[PagoNoMes(rotulo=rotulo_eixo(m), valor=_brl(pagos[m]["valor"])) for m in meses],
        media=ValorRotulado(valor=_brl(media), rotulo=f"média paga {moeda(media)}"),
        proximos=ProximosLancado(
            rotulo=f"próximos {DIAS_A_PAGAR} dias",
            lancado=ValorRotulado(valor=_brl(lancado), rotulo="lançado"),
            falta=ValorRotulado(valor=_brl(max(Decimal(0), media - lancado)), rotulo="falta"),
        ),
    )


def _compensando(c: Carteira) -> str:
    return f"; {moeda(c.compensando)} em compensação (D+2)" if c.compensando else ""


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
                contexto=f"carteira de {moeda(receber.total)}" + _compensando(receber),
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
                contexto="títulos em aberto já vencidos" + _compensando(pagar),
            ),
        ]
    return kpis


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

    receber: Carteira | None = None
    pagar: Carteira | None = None
    pagos_pagar: Mensal = {}
    inadimplencia: CartaoInadimplencia | None = None
    if a_receber:
        fins = [ultimo_dia_do_mes(mes_menos(aberto, n)) for n in range(MESES_SERIE, 0, -1)]
        leitura = _ler(
            conn,
            ctx,
            a_receber,
            hoje,
            limite,
            {d: ultimo_vencimento_vencido(d, feriados_periodo) for d in fins},
            de,
        )
        receber = leitura.carteira
        inadimplencia = cartao_inadimplencia(
            hoje, receber, leitura.serie, farol(conn, ctx.cliente_id, "inadimplencia", hoje)
        )
    if a_pagar:
        # sem série: o gráfico de inadimplência é só do a receber
        leitura = _ler(conn, ctx, a_pagar, hoje, limite, {}, de)
        pagar = leitura.carteira
        pagos_pagar = leitura.pagos

    media = media_paga(pagos_pagar, hoje)
    f_pagar = farol(conn, ctx.cliente_id, "pagar_lancado", hoje)
    cartoes: list[CartaoSecao] = []
    if receber is not None and pagar is not None and (receber.total or pagar.total):
        cartoes.append(cartao_proximos_dias(hoje, receber, pagar, media))
    if pagar is not None and (lancado := cartao_pagar_lancado(hoje, pagar, pagos_pagar, f_pagar)):
        cartoes.append(lancado)
    if inadimplencia is not None:
        cartoes.append(inadimplencia)

    kpis = kpis_financeiro(
        receber,
        pagar,
        media,
        inadimplencia.status if inadimplencia else None,
        f_pagar,
    )
    if not kpis and not cartoes:
        return None
    secao = Secao(
        id="financeiro",
        titulo="Financeiro",
        modulo=None,
        resumo=f"Posição em {hoje:%d/%m/%Y}, com os títulos lançados no sistema.",
        kpis=kpis,
        cartoes=cartoes,
        indicadores=[],
    )
    return SecaoDoMes(secao=secao, mes=aberto)

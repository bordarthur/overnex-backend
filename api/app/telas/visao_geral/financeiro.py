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
- média paga (e recebida) por mês: a dos até 6 meses fechados anteriores com pagamento
  (com recebimento), pela data de pagamento;
- cartões do topo: saldo em conta hoje (só com fonte de saldo), a receber nos próximos 30
  dias (com o a vencer no total e a carteira), a pagar nos próximos 30 dias (com quanto
  isso é da média paga) e a pagar vencido;
- próximos 7, 15 e 30 dias (JANELAS): a receber e a pagar lançado a vencer na janela
  (vencimento de hoje até hoje + N dias; o que está em compensação fica fora, como no
  "a vencer"). Na projeção, vendas novas = máx(0, média recebida × N/30 − a receber da
  janela) e o que falta lançar = máx(0, média paga × N/30 − a pagar da janela). O fim é
  saldo + a receber − a pagar (só o lançado) e saldo + a receber + novas − a pagar − falta
  (com a projeção). Com saldo: fim < 0 crítico, abaixo do caixa mínimo atenção, senão
  saudável. Sem saldo, o fim é a sobra ou a falta da janela, não o caixa: ≥ 0 saudável,
  < 0 atenção. O status de cada modo é o pior das janelas;
- contas a pagar lançadas: o lançado nos próximos 30 dias ÷ a média paga por mês, em %
  (a mesma conta do cartão "A pagar, 30 dias"), com farol.pagar_lancado (padrão: abaixo
  de 50 crítico, de 50 a 80 atenção, a partir de 80 saudável), no total e por grupo de
  pagamento, com o pago em cada um dos 6 meses fechados. Grupo sem farol (capex) não tem
  status. O grupo é o da conta do ERP no de-para (cfg.conta_dre.grupo_pagamento, migration
  20): Fornecedores, Custos fixos, despesas e impostos, Dívida e Capex, lidos na mesma
  consulta dos títulos. O total é a soma dos grupos: conta sem grupo (ex.: retirada de
  sócios, conta fora do de-para) fica fora do cartão e só aparece na explicação, e por
  isso o percentual pode diferir do KPI "A pagar, 30 dias", que soma tudo. Cliente sem
  nenhuma conta com grupo: um grupo só, com tudo (GRUPO_UNICO);
- cobertura de caixa: saldo ÷ (média paga por mês ÷ 30), em dias, contra a média dos
  últimos 12 meses (11 fechados e hoje). Status PROVISÓRIO: a partir da média saudável, a
  partir de 75% dela atenção, abaixo crítico. Sem fonte de saldo, o cartão não sai;
- inadimplência: vencido ÷ carteira em aberto hoje, por faixa de atraso, e a mesma conta
  no fim de cada um dos 12 meses fechados anteriores; farol contra a média dessa série
  (farol.inadimplencia, em p.p.).

Sem velocímetros: os prazos médios de recebimento e de pagamento ficam no painel Receber e
pagar. Sem fonte, por enquanto: saldo em conta (e com ele a cobertura de caixa), caixa
mínimo e ciclo de caixa.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from psycopg import sql

from app.core.cache import existe_coluna
from app.core.db import Conexao
from app.farol.parametro import Farol, deslocado, farol
from app.seguranca.acesso import ContextoConsolidado
from app.telas.calendario import feriados, mais_dias_uteis, ultimo_dia_do_mes
from app.telas.contrato import Status
from app.telas.moldura import MESES
from app.telas.visao_geral.contra_referencia import Mensal, rotulo_eixo, rotulos_meses
from app.telas.visao_geral.contrato import (
    CartaoCobertura,
    CartaoInadimplencia,
    CartaoPagarLancado,
    CartaoProximosDias,
    CartaoSecao,
    FimJanela,
    GrupoPagar,
    ItemCarteira,
    JanelaProximosDias,
    Kpi,
    LegendaProximosDias,
    MediaRotulada,
    MinimoCaixa,
    ModoProximosDias,
    PagoNoMes,
    ProximosLancado,
    ReguaCobertura,
    Secao,
    SerieCobertura,
    SerieInadimplencia,
    Trecho,
    Valor,
    ValorRotulado,
)
from app.telas.visao_geral.relativo import MESES_REFERENCIA
from app.telas.visao_geral.texto import moeda, moeda_sinal, numero
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
    # conta do ERP: o grupo de pagamento vem do de-para (cfg.conta_dre.grupo_pagamento)
    "conta": "en_codigoplanocliente",
}
MESES_SERIE = 12
DIAS_A_PAGAR = 30
# janelas do cartão de próximos dias, em dias corridos a partir de hoje
JANELAS = (7, 15, DIAS_A_PAGAR)
# D+2: dias úteis de compensação depois do vencimento
DIAS_COMPENSACAO = 2

# meses da série da cobertura de caixa, contando o de hoje
MESES_COBERTURA = 12
# cobertura abaixo dessa fração da média: crítico (PROVISÓRIO)
COBERTURA_CRITICA = Decimal("0.75")

MODO_LANCADO = "lancado"
MODO_PROJECAO = "projecao"
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


def _titulos(
    tabelas: list[TabelaDoCliente], ctx: ContextoConsolidado, por_grupo: bool = False
) -> sql.Composable:
    """Os títulos, com o grupo de pagamento da conta (cfg.conta_dre) se `por_grupo`; senão o
    grupo vem vazio."""
    campos = {
        "emissao": "emissao",
        "vencimento": "vencimento",
        "pagamento": "pagamento",
        "valor": "valor",
    }
    grupo = sql.SQL("null::text")
    juncao = sql.SQL("")
    if por_grupo:
        campos["conta"] = "conta"
        grupo = sql.SQL("g.grupo_pagamento")
        juncao = sql.SQL(
            "left join cfg.conta_dre g on g.cliente_id = %(cliente)s and g.conta = t.conta"
        )
    return sql.SQL(
        """
        select t.emissao,
               coalesce(t.vencimento, t.emissao) as vencimento,
               -- antes de 1901: data vazia gravada como 1899/1900
               case when t.pagamento >= date '1901-01-01' then t.pagamento end as pagamento,
               t.valor,
               {grupo} as grupo
          from ({linhas}) t
          {juncao}
         where t.emissao is not null and t.valor > 0
        """
    ).format(linhas=de_todas(tabelas, campos, filtro_empresa(ctx)), grupo=grupo, juncao=juncao)


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
    # por grupo de pagamento (None: conta sem grupo), só com a leitura por grupo: a vencer
    # nos próximos DIAS_A_PAGAR dias e o pago por mês
    lancado_grupo: dict[str | None, Decimal] = field(default_factory=dict)
    pagos_grupo: dict[str | None, Mensal] = field(default_factory=dict)


# as partes da leitura por grupo de pagamento (ver _ler)
POR_GRUPO = sql.SQL(
    """
    union all
    select 'grupo-lancado', null, null, {proximos}, null, null, null, null, null, null, null,
           null, grupo
      from a
     group by grupo
    union all
    select 'grupo-pago', date_trunc('month', pagamento)::date,
           null, null, null, null, null, null, null, null, null,
           sum(valor), grupo
      from t
     where pagamento >= %(de)s and pagamento <= %(hoje)s
     group by 2, grupo
    """
)


def _ler(
    conn: Conexao,
    ctx: ContextoConsolidado,
    tabelas: list[TabelaDoCliente],
    hoje: date,
    limite: date,
    limites_serie: Mapping[date, date],
    de: date,
    por_grupo: bool = False,
) -> Leitura:
    """Lê os títulos uma vez (CTE materializada) e calcula, na mesma consulta:
    - carteira: em aberto em `hoje`; vencido = vencimento até `limite`
      (ultimo_vencimento_vencido); a vencer em cada janela de JANELAS;
    - série: vencido e em aberto em cada dia de `limites_serie` (dia →
      ultimo_vencimento_vencido); vazio, sem série;
    - pagos: por mês de pagamento, de `de` até `hoje`, o valor pago;
    - com `por_grupo`, por grupo de pagamento da conta: o a vencer em cada janela (a mesma
      conta da carteira) e o pago por mês.
    """
    linhas = conn.execute(
        sql.SQL(
            """
            with t as materialized ({titulos}),
            a as (
                select %(hoje)s::date - vencimento as atraso,
                       vencimento <= %(limite)s as vencido,
                       valor,
                       grupo
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
                   null::numeric as pago,
                   null::text as grupo
              from a
            union all
            select 'serie', fim.dia, null, null, null, null, null, null, null,
                   coalesce(sum(t.valor) filter (where t.vencimento <= fim.limite), 0),
                   coalesce(sum(t.valor), 0),
                   null, null
              from fim
              left join t
                on t.emissao <= fim.dia and (t.pagamento is null or t.pagamento > fim.dia)
             group by fim.dia
            union all
            select 'pagos', date_trunc('month', pagamento)::date,
                   null, null, null, null, null, null, null, null, null,
                   sum(valor), null
              from t
             where pagamento >= %(de)s and pagamento <= %(hoje)s
             group by 2
            {por_grupo}
            """
        ).format(
            titulos=_titulos(tabelas, ctx, por_grupo),
            proximos=_proximos(),
            por_grupo=POR_GRUPO.format(proximos=_proximos()) if por_grupo else sql.SQL(""),
        ),
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
    lancado_grupo: dict[str | None, Decimal] = {}
    pagos_grupo: dict[str | None, Mensal] = {}
    for r in linhas:
        if r["parte"] == "grupo-lancado":
            lancado_grupo[r["grupo"]] = dict(zip(JANELAS, r["proximos"], strict=True))[DIAS_A_PAGAR]
            continue
        if r["parte"] == "grupo-pago":
            pagos_grupo.setdefault(r["grupo"], {})[r["dia"]] = {"valor": r["pago"]}
            continue
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
    return Leitura(
        carteira=carteira,
        serie=sorted(serie, key=lambda p: p.dia),
        pagos=pagos,
        lancado_grupo=lancado_grupo,
        pagos_grupo=pagos_grupo,
    )


def meses_pagos(pagos: Mensal, hoje: date) -> list[date]:
    """Os até 6 meses fechados anteriores ao de `hoje` com pagamento, do mais antigo."""
    aberto = date(hoje.year, hoje.month, 1)
    return [m for m in sorted(pagos) if m < aberto][-MESES_REFERENCIA:]


def media_paga(pagos: Mensal, hoje: date) -> Decimal | None:
    """Média paga (ou recebida) por mês nos até 6 meses fechados anteriores ao de `hoje`
    com pagamento."""
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
        ("Mais de 90 dias", carteira.mais_90),
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
                so_na_barra=True if rotulo in ("A vencer", "Em compensação") else None,
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


def falta_lancar(media: Decimal, dias: int, lancado: Decimal) -> Decimal:
    """O que falta para a janela chegar à média por mês, proporcional aos dias: as contas a
    pagar ainda não lançadas (média paga) ou as vendas novas (média recebida)."""
    return max(Decimal(0), media * dias / DIAS_A_PAGAR - lancado)


def _fim(valor: Decimal, com_saldo: bool, minimo: Decimal | None) -> FimJanela:
    if not com_saldo:
        # sem saldo, o fim é a sobra ou a falta da janela, não o caixa
        if valor >= 0:
            return FimJanela(valor=_brl(valor), status="saudavel", texto="entra mais do que sai")
        return FimJanela(valor=_brl(valor), status="atencao", texto="sai mais do que entra")
    if valor < 0:
        return FimJanela(valor=_brl(valor), status="critico", texto="caixa negativo")
    if minimo is not None and valor < minimo:
        return FimJanela(valor=_brl(valor), status="atencao", texto="abaixo do mínimo")
    return FimJanela(valor=_brl(valor), status="saudavel", texto="caixa no fim")


def _pior(janelas: list[JanelaProximosDias], modo: str) -> Status | None:
    status = [s for j in janelas if (s := j.fim[modo].status) is not None]
    return max(status, key=lambda s: PIOR[s], default=None)


def _positiva(media: Decimal | None) -> Decimal | None:
    return media if media is not None and media > 0 else None


def _nota_proximos(media_receber: Decimal | None, media_pagar: Decimal | None) -> str:
    if media_receber is None and media_pagar is None:
        return (
            "Só o lançado considera as contas que já estão no sistema. Sem pagamentos nem "
            "recebimentos nos meses fechados, não há média para projetar o que ainda vai "
            "entrar e sair."
        )
    partes: list[str] = []
    quem: list[str] = []
    sem = ""
    if media_receber is not None:
        partes.append(f"o a receber até a média recebida ({moeda(media_receber)} por mês)")
        quem.append("vendas novas")
    else:
        sem = (
            " Sem recebimentos nos meses fechados, não há média recebida para projetar as "
            "vendas novas."
        )
    if media_pagar is not None:
        partes.append(f"o a pagar até a média paga ({moeda(media_pagar)} por mês)")
        quem.append("contas ainda não lançadas")
    else:
        sem = (
            " Sem pagamentos nos meses fechados, não há média paga para estimar as contas a lançar."
        )
    proporcional = "proporcionais" if len(partes) == 2 else "proporcional"
    return (
        "Só o lançado usa o que já está no sistema; com a projeção, cada janela completa "
        f"{' e '.join(partes)}, {proporcional} aos dias, porque {' e '.join(quem)} também "
        f"passam pelo caixa.{sem}"
    )


def _explicacao_proximos(com_saldo: bool, com_minimo: bool, com_projecao: bool) -> str:
    if com_saldo:
        texto = (
            "Cada linha começa no saldo de hoje e soma o que vence a receber na janela. "
            "Embaixo, o que vence a pagar volta do fim dessa barra. O traço marca onde o "
            "caixa termina"
        ) + ("; a linha tracejada é o caixa mínimo." if com_minimo else ".")
    else:
        texto = (
            "Cada linha mostra o que vence a receber na janela. Embaixo, o que vence a pagar "
            "volta do fim dessa barra. O traço marca quanto sobra ou falta."
        )
    if com_projecao:
        texto += (
            " Na projeção, o hachurado claro são vendas novas, pela média recebida, e o "
            "escuro, contas ainda não lançadas, pela média paga."
        )
    if not com_saldo:
        texto += " O saldo em conta ainda não entra: o sistema não tem essa fonte."
    return texto


def cartao_proximos_dias(
    hoje: date,
    receber: Carteira,
    pagar: Carteira,
    media_pagar: Decimal | None,
    media_receber: Decimal | None = None,
    saldo: Decimal | None = None,
    minimo: Decimal | None = None,
) -> CartaoProximosDias:
    """O saldo de hoje (quando houver), mais o que vence a receber, menos o que vence a
    pagar, em 7, 15 e 30 dias. Sem média paga nem recebida (nada pago nem recebido nos
    meses fechados), só o modo "Só o lançado". O caixa mínimo só vale com saldo."""
    media_pagar, media_receber = _positiva(media_pagar), _positiva(media_receber)
    projecao = media_pagar is not None or media_receber is not None
    com_saldo = saldo is not None
    if not com_saldo:
        minimo = None
    base = saldo if saldo is not None else Decimal(0)
    janelas: list[JanelaProximosDias] = []
    for dias in JANELAS:
        a_receber = receber.proximos.get(dias, Decimal(0))
        a_pagar = pagar.proximos.get(dias, Decimal(0))
        falta = falta_lancar(media_pagar, dias, a_pagar) if media_pagar else Decimal(0)
        novas = falta_lancar(media_receber, dias, a_receber) if media_receber else Decimal(0)
        fim = {MODO_LANCADO: _fim(base + a_receber - a_pagar, com_saldo, minimo)}
        if projecao:
            fim[MODO_PROJECAO] = _fim(base + a_receber + novas - a_pagar - falta, com_saldo, minimo)
        janelas.append(
            JanelaProximosDias(
                titulo=f"{dias} dias",
                ate=f"até {hoje + timedelta(days=dias):%d/%m}",
                receber=_brl(a_receber),
                pagar=_brl(a_pagar),
                falta=_brl(falta),
                novas=_brl(novas) if projecao else None,
                fim=fim,
            )
        )
    modos = [
        ModoProximosDias(
            id=MODO_LANCADO,
            rotulo="Só o lançado",
            com_falta=False,
            com_novas=False,
            status=_pior(janelas, MODO_LANCADO),
        )
    ]
    if projecao:
        modos.append(
            ModoProximosDias(
                id=MODO_PROJECAO,
                rotulo="Com a projeção",
                com_falta=True,
                com_novas=True,
                status=_pior(janelas, MODO_PROJECAO),
            )
        )
    if com_saldo:
        subtitulo = (
            "O saldo de hoje, mais o que entra, menos o que sai: onde o caixa termina em "
            "cada janela"
        )
    else:
        subtitulo = (
            "O que vence a receber, menos o que vence a pagar: quanto sobra ou falta em cada janela"
        )
    return CartaoProximosDias(
        tipo="proximos-dias",
        titulo="Próximos 7, 15 e 30 dias",
        subtitulo=subtitulo,
        explicacao=_explicacao_proximos(com_saldo, minimo is not None, projecao),
        nota=_nota_proximos(media_receber, media_pagar),
        modos=modos,
        legenda=LegendaProximosDias(
            saldo="Saldo hoje" if com_saldo else None,
            receber="A receber",
            novas="Vendas novas, pela média recebida" if projecao else None,
            pagar="A pagar",
            falta="Contas a lançar, pela média paga",
        ),
        saldo=_brl(saldo) if saldo is not None else None,
        minimo=(
            MinimoCaixa(valor=minimo, rotulo=f"caixa mínimo {moeda(minimo)}")
            if minimo is not None
            else None
        ),
        janelas=janelas,
    )


def _mes_por_extenso(mes: date, com_ano: bool) -> str:
    nome = MESES[mes.month - 1].lower()
    return f"{nome} de {mes.year}" if com_ano else nome


def _periodo(meses: list[date]) -> str:
    """ "em abril" ou "de abril a setembro" (com o ano quando os meses cruzam anos)."""
    primeiro, ultimo = meses[0], meses[-1]
    if len(meses) == 1:
        return f"em {_mes_por_extenso(ultimo, False)}"
    com_ano = primeiro.year != ultimo.year
    return f"de {_mes_por_extenso(primeiro, com_ano)} a {_mes_por_extenso(ultimo, com_ano)}"


def _lista(itens: list[str]) -> str:
    """["a", "b", "c"] → "a, b e c"."""
    return " e ".join([", ".join(itens[:-1]), itens[-1]]) if len(itens) > 1 else "".join(itens)


def _minuscula(nome: str) -> str:
    return nome[:1].lower() + nome[1:]


@dataclass(frozen=True)
class GrupoPagamento:
    """O a pagar de um grupo de pagamento (fornecedores, custos fixos, dívida, capex)."""

    nome: str
    # a vencer nos próximos DIAS_A_PAGAR dias
    lancado: Decimal
    # por mês de pagamento: valor pago
    pagos: Mensal = field(default_factory=dict)
    # sem farol: oscila muito para tratar o que falta como falta (capex)
    sem_farol: bool = False


# cliente sem grupo de pagamento cadastrado (cfg.conta_dre.grupo_pagamento): um grupo só
GRUPO_UNICO = "Todas as contas a pagar"
# grupo_pagamento (migration 20) → (nome no cartão, sem farol), na ordem do cartão
GRUPOS_PAGAMENTO: Mapping[str, tuple[str, bool]] = {
    "fornecedores": ("Fornecedores", False),
    "custos": ("Custos fixos, despesas e impostos", False),
    "divida": ("Dívida", False),
    # oscila muito para tratar o que falta como falta
    "capex": ("Capex", True),
}


def grupos_da_leitura(
    lancado: Mapping[str | None, Decimal], pagos: Mapping[str | None, Mensal]
) -> tuple[list[GrupoPagamento], Mensal] | None:
    """Os grupos de pagamento (GRUPOS_PAGAMENTO, na ordem) e o pago por mês nas contas sem
    grupo, que ficam fora do cartão; None se nenhum título lido tem grupo (o cliente ainda
    não tem a classificação)."""
    if all(g is None for g in (*lancado, *pagos)):
        return None
    grupos = [
        GrupoPagamento(nome, lancado.get(id_, Decimal(0)), dict(pagos.get(id_, {})), sem_farol)
        for id_, (nome, sem_farol) in GRUPOS_PAGAMENTO.items()
    ]
    return grupos, dict(pagos.get(None, {}))


def _grupo_pagar(
    g: GrupoPagamento, meses: list[date], rotulos: list[str], f: Farol
) -> tuple[GrupoPagar, Decimal] | None:
    """O cartão do grupo e quanto falta nele; None sem pagamento do grupo nos meses."""
    pagos = [g.pagos[m]["valor"] if m in g.pagos else Decimal(0) for m in meses]
    media = sum(pagos, Decimal(0)) / len(meses)
    if media <= 0:
        return None
    falta = max(Decimal(0), media - g.lancado)
    pct = g.lancado / media * 100
    grupo = GrupoPagar(
        nome=g.nome,
        status=None if g.sem_farol else f.status(pct),
        percentual=Valor(valor=pct, unidade="pct", casas=0),
        texto=f"lançado; falta {moeda(falta)}" if falta else "lançado; nada falta",
        pagos=[PagoNoMes(rotulo=r, valor=_brl(v)) for r, v in zip(rotulos, pagos, strict=True)],
        media=ValorRotulado(valor=_brl(media), rotulo=f"média {moeda(media)}"),
        proximos=ProximosLancado(
            rotulo=f"{DIAS_A_PAGAR} dias",
            lancado=ValorRotulado(valor=_brl(g.lancado), rotulo="lançado"),
            falta=ValorRotulado(valor=_brl(falta), rotulo="falta"),
        ),
        nota=(
            f"Oscila de {moeda(min(pagos))} a {moeda(max(pagos))} por mês. Confira se há "
            "investimento previsto antes de tratar como falta."
            if g.sem_farol
            else None
        ),
    )
    return grupo, falta


def _nota_grupos(faltas: list[tuple[str, Decimal]]) -> str:
    """ "Faltam R$ 2,65 mi: R$ 1,35 mi em custos fixos…, R$ 1,04 mi em fornecedores…", do
    grupo com mais falta para o com menos."""
    com_falta = sorted(((n, v) for n, v in faltas if v > 0), key=lambda nv: -nv[1])
    total = sum((v for _, v in com_falta), Decimal(0))
    if not com_falta:
        return "Todos os grupos já têm lançado ao menos a média paga por mês."
    if len(com_falta) == 1:
        return f"Faltam {moeda(total)}, tudo em {_minuscula(com_falta[0][0])}."
    partes = [f"{moeda(v)} em {_minuscula(n)}" for n, v in com_falta]
    return f"Faltam {moeda(total)}: {_lista(partes)}."


def cartao_pagar_lancado(
    hoje: date, grupos: list[GrupoPagamento], f: Farol, sem_grupo: Mensal | None = None
) -> CartaoPagarLancado | None:
    """O lançado a pagar nos próximos 30 dias contra a média paga por mês nos 6 meses
    fechados, no total e por grupo; None sem pagamento nesses meses. Os meses são os
    fechados com pagamento em qualquer grupo; o grupo sem pagamento num deles conta 0. O
    total é a soma dos grupos: `sem_grupo` (pago por mês nas contas sem grupo) fica fora e
    só aparece na explicação."""
    total: Mensal = {}
    for g in grupos:
        for mes, v in g.pagos.items():
            total[mes] = {"valor": total.get(mes, {}).get("valor", Decimal(0)) + v["valor"]}
    meses = meses_pagos(total, hoje)
    media = media_paga(total, hoje)
    if media is None or media <= 0:
        return None
    lancado = sum((g.lancado for g in grupos), Decimal(0))
    pct = lancado / media * 100
    rotulos = rotulos_meses(meses)
    # (grupo, cartão do grupo, quanto falta), só os grupos com pagamento nos meses
    feitos = [(g, *r) for g in grupos if (r := _grupo_pagar(g, meses, rotulos, f)) is not None]
    periodo = _periodo(meses)
    barra_da_direita = (
        f"A barra da direita é o que já está lançado para vencer nos próximos {DIAS_A_PAGAR} "
        "dias; o hachurado é o que falta para chegar à média"
    )
    if len(grupos) == 1:
        falta = max(Decimal(0), media - lancado)
        if falta:
            nota = (
                f"Faltam {moeda(falta)} para chegar à média paga por mês ({moeda(media)}, "
                f"{periodo})."
            )
        else:
            nota = f"O lançado já cobre a média paga por mês ({moeda(media)}, {periodo})."
        rotulo_grupos = f"Pago por mês e lançado para {DIAS_A_PAGAR} dias"
        explicacao = (
            f"As barras cinza são o que a empresa pagou em cada mês. {barra_da_direita} "
            "paga. O plano de contas ainda não separa os pagamentos por grupo."
        )
    else:
        nota = _nota_grupos([(g.nome, falta) for g, _, falta in feitos])
        rotulo_grupos = "Por grupo de pagamento"
        nomes = _lista([_minuscula(g.nome) for g, _, _ in feitos])
        oscilam = "".join(
            f" {g.nome} oscila muito: confira se há investimento previsto antes de tratar "
            "como falta."
            for g, _, _ in feitos
            if g.sem_farol
        )
        fora = sum(
            ((sem_grupo or {}).get(m, {}).get("valor", Decimal(0)) for m in meses), Decimal(0)
        )
        de_fora = (
            f" Ficam fora dos grupos {moeda(fora)} pagos {periodo} em contas sem grupo de "
            "pagamento no cadastro."
            if fora > 0
            else ""
        )
        explicacao = (
            f"Em cada grupo ({nomes}), as barras cinza são o que a empresa pagou por mês. "
            f"{barra_da_direita} do grupo.{oscilam}{de_fora}"
        )
    return CartaoPagarLancado(
        tipo="pagar-lancado",
        titulo="Contas a pagar lançadas",
        subtitulo=(
            f"O que já está lançado para os próximos {DIAS_A_PAGAR} dias, contra o que a "
            "empresa paga por mês"
        ),
        explicacao=explicacao,
        status=f.status(pct),
        percentual=Valor(valor=pct, unidade="pct", casas=0),
        texto=f"da média paga por mês já está lançada para os próximos {DIAS_A_PAGAR} dias.",
        nota=nota,
        rotulo_grupos=rotulo_grupos,
        grupos=[cartao for _, cartao, _ in feitos],
    )


def _inteiro(valor: Decimal) -> int:
    return int(valor.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def dias_de_cobertura(saldo: Decimal, media_paga_mes: Decimal) -> Decimal:
    """Quantos dias o saldo paga, se nada entrar: saldo ÷ (média paga por mês ÷ 30)."""
    return max(Decimal(0), saldo * DIAS_A_PAGAR / media_paga_mes)


def _dias(n: int) -> str:
    return "1 dia" if n == 1 else f"{n} dias"


def cartao_cobertura(
    hoje: date,
    saldo: Decimal | None,
    media_paga_mes: Decimal | None,
    historico: Mapping[date, Decimal],
) -> CartaoCobertura | None:
    """Dias de cobertura hoje contra os últimos meses fechados de `historico` (mês → dias
    de cobertura no fim dele). None sem saldo, sem média paga ou sem histórico.
    Status PROVISÓRIO: a partir da média, saudável; a partir de COBERTURA_CRITICA da média,
    atenção; abaixo, crítico."""
    if saldo is None or media_paga_mes is None or media_paga_mes <= 0:
        return None
    aberto = date(hoje.year, hoje.month, 1)
    meses = [m for m in sorted(historico) if m < aberto][-(MESES_COBERTURA - 1) :]
    if not meses:
        return None
    dias = _inteiro(dias_de_cobertura(saldo, media_paga_mes))
    valores = [*(_inteiro(historico[m]) for m in meses), dias]
    media = Decimal(sum(valores)) / len(valores)
    referencia = _inteiro(media)
    status: Status
    if dias >= media:
        status = "saudavel"
    elif dias >= media * COBERTURA_CRITICA:
        status = "atencao"
    else:
        status = "critico"
    anterior, mes_anterior = valores[-2], meses[-1]
    if dias:
        texto = [
            Trecho(texto="Sem nada entrar, o saldo acaba em "),
            Trecho(texto=f"{hoje + timedelta(days=dias):%d/%m}", destaque=True),
            Trecho(texto="."),
        ]
    else:
        texto = [Trecho(texto="O saldo de hoje não paga nem um dia das contas.")]
    return CartaoCobertura(
        tipo="cobertura",
        titulo="Cobertura de caixa",
        subtitulo="Quantos dias o saldo de hoje paga, se nada entrar",
        explicacao=(
            "Cada quadrado é um dia. Os preenchidos são os dias que o saldo de hoje paga, "
            "pela média do que a empresa paga por dia, se nada entrar. Os contornados vão "
            f"até a média de {len(valores)} meses."
        ),
        status=status,
        dias=Valor(valor=Decimal(dias), unidade="dias"),
        texto=texto,
        nota=(
            f"{'Era' if anterior == 1 else 'Eram'} {_dias(anterior)} em "
            f"{_mes_por_extenso(mes_anterior, mes_anterior.year != hoje.year)}. "
            f"A média de {len(valores)} meses é {referencia}."
        ),
        regua=ReguaCobertura(
            total=DIAS_A_PAGAR,
            cobertos=min(dias, DIAS_A_PAGAR),
            referencia=min(referencia, DIAS_A_PAGAR),
            rotulo_cobertos=f"hoje: {_dias(dias)}",
            rotulo_referencia=f"média: {referencia}",
            inicio=f"{hoje:%d/%m}",
            fim=f"{hoje + timedelta(days=DIAS_A_PAGAR):%d/%m}",
        ),
        serie=SerieCobertura(
            titulo=f"Últimos {len(valores)} meses, em dias de cobertura",
            rotulos=[*rotulos_meses(meses), "hoje"],
            valores=[Decimal(v) for v in valores],
            media=MediaRotulada(valor=media, rotulo=f"média {referencia}"),
        ),
    )


def _compensando(c: Carteira) -> str:
    return f"; {moeda(c.compensando)} em compensação (D+2)" if c.compensando else ""


def kpis_financeiro(
    receber: Carteira | None,
    pagar: Carteira | None,
    media_paga_mes: Decimal | None,
    f_pagar: Farol,
    saldo: Decimal | None = None,
) -> list[Kpi]:
    """Saldo em conta hoje (com fonte de saldo), a receber nos próximos 30 dias, a pagar
    nos próximos 30 dias e a pagar vencido."""
    kpis: list[Kpi] = []
    if saldo is not None:
        kpis.append(
            Kpi(rotulo="Saldo em conta hoje", valor=_brl(saldo), contexto="todas as contas")
        )
    if receber is not None and receber.total:
        kpis.append(
            Kpi(
                rotulo=f"A receber, {DIAS_A_PAGAR} dias",
                valor=_brl(receber.proximos_dias),
                contexto=(
                    f"a vencer no total: {moeda(receber.a_vencer)}; "
                    f"carteira: {moeda(receber.total)}"
                ),
            )
        )
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
                valor=_brl(pagar.proximos_dias),
                contexto=contexto,
                status=status,
            ),
            Kpi(
                rotulo="A pagar vencido",
                valor=_brl(pagar.vencido),
                contexto="títulos em aberto já vencidos" + _compensando(pagar),
            ),
        ]
    return kpis


def resumo_financeiro(
    hoje: date, proximos: CartaoProximosDias | None, media_pagar: Decimal | None
) -> str:
    """Quanto falta lançar a pagar em 30 dias e onde a projeção termina. Sem a projeção, a
    data da posição."""
    trinta = proximos.janelas[-1] if proximos and proximos.janelas else None
    fim = trinta.fim.get(MODO_PROJECAO) if trinta else None
    if proximos is None or trinta is None or fim is None:
        return f"Posição em {hoje:%d/%m/%Y}, com os títulos lançados no sistema."
    valor = fim.valor.valor
    if proximos.saldo is not None:
        depois = f"o caixa de {DIAS_A_PAGAR} dias fecha em {moeda_sinal(valor)}"
    elif valor >= 0:
        depois = f"entram {moeda(valor)} a mais do que saem nos próximos {DIAS_A_PAGAR} dias"
    else:
        depois = f"saem {moeda(valor)} a mais do que entram nos próximos {DIAS_A_PAGAR} dias"
    if _positiva(media_pagar) is None:
        return f"Com a projeção, {depois}."
    if trinta.falta.valor > 0:
        return (
            f"Faltam lançar {moeda(trinta.falta.valor)} de contas a pagar; com a projeção, "
            f"{depois}."
        )
    return (
        f"As contas a pagar dos próximos {DIAS_A_PAGAR} dias já estão lançadas; com a "
        f"projeção, {depois}."
    )


def _por_grupo(conn: Conexao, tabelas: list[TabelaDoCliente]) -> bool:
    """Lê o a pagar por grupo de pagamento: com a migration 20 e a coluna da conta em todas
    as tabelas."""
    return existe_coluna(conn, "cfg.conta_dre", "grupo_pagamento") and all(
        t.colunas.get("conta") in t.existentes for t in tabelas
    )


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
    recebidos: Mensal = {}
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
        recebidos = leitura.pagos
        inadimplencia = cartao_inadimplencia(
            hoje, receber, leitura.serie, farol(conn, ctx.cliente_id, "inadimplencia", hoje)
        )
    lidos: tuple[list[GrupoPagamento], Mensal] | None = None
    if a_pagar:
        # sem série: o gráfico de inadimplência é só do a receber
        leitura = _ler(conn, ctx, a_pagar, hoje, limite, {}, de, _por_grupo(conn, a_pagar))
        pagar = leitura.carteira
        pagos_pagar = leitura.pagos
        lidos = grupos_da_leitura(leitura.lancado_grupo, leitura.pagos_grupo)

    media = media_paga(pagos_pagar, hoje)
    media_receber = media_paga(recebidos, hoje)
    # sem fonte de saldo bancário nem de caixa mínimo (ver TODO.md): o próximos dias começa
    # em zero, e a cobertura de caixa não sai
    saldo: Decimal | None = None
    minimo: Decimal | None = None
    f_pagar = farol(conn, ctx.cliente_id, "pagar_lancado", hoje)
    cartoes: list[CartaoSecao] = []
    proximos: CartaoProximosDias | None = None
    if receber is not None and pagar is not None and (receber.total or pagar.total):
        proximos = cartao_proximos_dias(hoje, receber, pagar, media, media_receber, saldo, minimo)
        cartoes.append(proximos)
    if pagar is not None:
        grupos, sem_grupo = lidos or (
            [GrupoPagamento(GRUPO_UNICO, pagar.proximos_dias, pagos_pagar)],
            {},
        )
        if lancado := cartao_pagar_lancado(hoje, grupos, f_pagar, sem_grupo):
            cartoes.append(lancado)
    if cobertura := cartao_cobertura(hoje, saldo, media, {}):
        cartoes.append(cobertura)
    if inadimplencia is not None:
        cartoes.append(inadimplencia)

    kpis = kpis_financeiro(receber, pagar, media, f_pagar, saldo)
    if not kpis and not cartoes:
        return None
    secao = Secao(
        id="financeiro",
        titulo="Financeiro",
        modulo=None,
        resumo=resumo_financeiro(hoje, proximos, media),
        kpis=kpis,
        cartoes=cartoes,
        indicadores=[],
    )
    return SecaoDoMes(secao=secao, mes=aberto)

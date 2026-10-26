"""COM-01 — Vendas. REGRAS PROVISÓRIAS: confirmar com o negócio (ver TODO.md).

Fontes e mês: ver base.py. Filtros: canal (do cadastro do vendedor; a venda entra pelo
vendedor dela) e linha (papel linha das vendas; PJ: it_grupo). Com filtro de linha não há
meta (a meta não é aberta por linha): saem os números contra a meta e os cartões que
comparam as linhas entre si.

- vendido: soma do valor; pedidos: pedidos distintos; clientes: clientes distintos;
  margem bruta: 1 − custo ÷ valor;
- projeção, ritmo e necessário por dia útil: base.MesPainel (como na Visão geral);
- andamento do mês: régua, estatísticas e o acumulado dia a dia contra a meta acumulada
  por dia útil (visão Volume com o papel quantidade, sem meta: a meta de volume está vazia).
  "Mês anterior": o vendido no mês anterior inteiro;
- fatores: faturamento = clientes × venda por cliente, contra a meta de valor e a de
  clientes (NDeClientes). Clientes no fim do mês: os de hoje × (clientes do mês anterior ÷
  os que ele tinha no mesmo número de dias úteis), porque cliente distinto não cresce em
  linha reta com os dias. Sem meta de clientes, o cartão não sai;
- ritmo por dia útil: vendido no dia contra a meta diária (meta ÷ dias úteis); nos dias que
  faltam, o necessário por dia;
- linhas de produto: receita, participação e margem do mês contra os 3 meses anteriores
  somados; farol pela variação da margem (−1 p.p. ou pior, crítico; abaixo de 0, atenção);
- canais: projeção do canal (vendido pelos vendedores do canal, no ritmo do mês) contra a
  soma das metas desses vendedores;
- últimos 12 meses (fechados, antes do mês da tela): vendido contra a meta (vermelho abaixo
  de 98% da meta) e clientes contra a meta de clientes, com o ticket por cliente;
- participação das linhas: as 6 maiores dos 12 meses e "Outras".

Fora, por falta de fonte: supermeta. Fora por decisão (textos que interpretam): conclusão
dos fatores e as notas de linhas, canais e participação.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from psycopg import sql

from app.core.db import Conexao
from app.farol.parametro import Farol, farol
from app.seguranca.acesso import ContextoConsolidado
from app.telas.calendario import feriados, ultimo_dia_do_mes
from app.telas.contrato import Status
from app.telas.moldura import UltimaCarga
from app.telas.paineis.base import (
    Conteudo,
    Fontes,
    MesPainel,
    Metas,
    Opcoes,
    Vendedor,
    escolher,
    fontes,
    ler_inicio,
    mes_painel,
    meses_antes,
    opcoes,
    parametros,
    rotulo,
    slug,
    tem_papel,
    vendas_sql,
)
from app.telas.paineis.contrato import (
    Barra,
    Cartao,
    CartaoAndamentoMes,
    CartaoContraMeta,
    CartaoFatores,
    CartaoGraficos,
    CartaoTabela,
    Celula,
    ColunaTabela,
    DiaDegrau,
    Escala,
    Falta,
    Fator,
    GraficoBarras,
    GraficoCartao,
    GraficoCombo,
    GraficoDegraus,
    GraficoEmpilhado,
    GrupoBarras,
    Hoje,
    ItemContraMeta,
    ItemLegenda,
    ItemValor,
    LinhaTabela,
    MarcaRegua,
    NomesCombo,
    PontoCombo,
    ReferenciaBarras,
    Regua,
    SerieDegrau,
    SerieEmpilhada,
    TrechoRegua,
    VisaoAndamento,
)
from app.telas.paineis.formato import (
    CEM,
    SEMANA,
    brl,
    mais,
    nome_mes,
    pct,
    pct_texto,
    pp,
    q,
    qtd,
)
from app.telas.visao_geral.contra_referencia import rotulo_eixo
from app.telas.visao_geral.contrato import Valor
from app.telas.visao_geral.texto import moeda, numero
from app.telas.visao_geral.vendas import mes_menos

MESES_HISTORICO = 12
MESES_LINHAS = 3
LINHAS_NO_GRAFICO = 6
# mês abaixo de 98% da meta: barra vermelha
TOLERANCIA_META = Decimal("0.98")
# diferença de fator abaixo de −0,5% (ou acima de +0,5%): pinta
LIMIAR_FATOR = Decimal("0.5")
ZERO = Decimal(0)


@dataclass(frozen=True)
class MesVendas:
    valor: Decimal
    custo: Decimal
    quantidade: Decimal
    pedidos: int
    clientes: int


@dataclass
class DadosVendas:
    # dia do mês da tela → (valor, quantidade)
    dias: dict[date, tuple[Decimal, Decimal]] = field(default_factory=dict)
    meses: dict[date, MesVendas] = field(default_factory=dict)
    # (mês, linha) → (valor, custo); sem o filtro de linha
    linhas: dict[tuple[date, str], tuple[Decimal, Decimal]] = field(default_factory=dict)
    # vendedor → vendido no mês da tela
    por_vendedor: dict[str | None, Decimal] = field(default_factory=dict)
    # dia → clientes que compraram pela primeira vez no mês anterior nesse dia
    novos_anterior: dict[date, int] = field(default_factory=dict)


def ler_dados(
    conn: Conexao,
    ctx: ContextoConsolidado,
    f: Fontes,
    mes: date,
    vendedores: list[str] | None,
    linhas: list[str] | None,
) -> DadosVendas:
    """Uma consulta: as linhas de venda do período (13 meses) lidas uma vez (CTE
    materializada) e as cinco agregações da tela."""
    rows = conn.execute(
        sql.SQL(
            """
            with v as materialized (
                select * from ({vendas}) t
                 where dia >= %(de)s and dia < %(ate)s
                   and (%(vendedores)s::text[] is null or vendedor = any(%(vendedores)s))
            ),
            f as (select * from v where %(linhas)s::text[] is null or linha = any(%(linhas)s))
            select 'dia' as parte, dia as data, null::text as chave,
                   sum(valor) as valor, sum(custo) as custo, sum(quantidade) as quantidade,
                   null::bigint as pedidos, null::bigint as clientes
              from f where dia >= %(mes)s group by dia
            union all
            select 'mes', date_trunc('month', dia)::date, null,
                   sum(valor), sum(custo), sum(quantidade),
                   count(distinct pedido), count(distinct cliente)
              from f group by 2
            union all
            select 'linha', date_trunc('month', dia)::date, linha,
                   sum(valor), sum(custo), null, null, null
              from v where linha is not null group by 2, 3
            union all
            select 'vendedor', null, vendedor, sum(valor), null, null, null, null
              from f where dia >= %(mes)s group by 3
            union all
            select 'novos', primeiro, null, null, null, null, null, count(*)
              from (select cliente, min(dia) as primeiro
                      from f
                     where dia >= %(anterior)s and dia < %(mes)s and cliente is not null
                     group by cliente) c
             group by primeiro
            """
        ).format(vendas=vendas_sql(ctx, f)),
        parametros(
            ctx,
            de=mes_menos(mes, MESES_HISTORICO),
            ate=ultimo_dia_do_mes(mes) + timedelta(days=1),
            mes=mes,
            anterior=mes_menos(mes, 1),
            vendedores=vendedores,
            linhas=linhas,
        ),
    ).fetchall()
    d = DadosVendas()
    for r in rows:
        valor = r["valor"] or ZERO
        if r["parte"] == "dia":
            d.dias[r["data"]] = (valor, r["quantidade"] or ZERO)
        elif r["parte"] == "mes":
            d.meses[r["data"]] = MesVendas(
                valor, r["custo"] or ZERO, r["quantidade"] or ZERO, r["pedidos"], r["clientes"]
            )
        elif r["parte"] == "linha":
            d.linhas[(r["data"], r["chave"])] = (valor, r["custo"] or ZERO)
        elif r["parte"] == "vendedor":
            d.por_vendedor[r["chave"]] = valor
        else:
            d.novos_anterior[r["data"]] = r["clientes"]
    return d


def montar(
    ctx: ContextoConsolidado,
    escolhido: date | None,
    carga: UltimaCarga,
    pedidos: Mapping[str, str | None],
) -> Conteudo:
    conn = ctx.sessao.conn
    f = fontes(conn, ctx)
    inicio = ler_inicio(conn, ctx, f, escolhido)
    panorama, vendedores, metas = inicio.panorama, inicio.vendedores, inicio.metas

    canal_o: Opcoes | None = None
    if tem_papel(f.vendas, "vendedor") and any(v.canal for v in vendedores.values()):
        canal_o = opcoes("canal", "Canal", (v.canal for v in vendedores.values()))
    linha_o = opcoes("linha", "Linha", panorama.linhas, todas=True) if panorama.linhas else None
    filtros = [o.filtro for o in (canal_o, linha_o) if o is not None]
    canais = escolher(canal_o, pedidos.get("canal"))
    linhas = escolher(linha_o, pedidos.get("linha"))
    escolhidos = None if canais is None else [c for c, v in vendedores.items() if v.canal in canais]

    mes = panorama.mes
    fechado = mes is not None and mes < carga.data_snapshot.replace(day=1)
    if mes is None or panorama.ultimo_dia is None:
        # sem venda no mês (ainda, ou fora da janela das tabelas de vendas)
        return Conteudo(filtros=filtros)
    mp = mes_painel(conn, ctx, mes, panorama.ultimo_dia, fechado)
    dados = ler_dados(conn, ctx, f, mes, escolhidos, None if linhas is None else list(linhas))
    atual = dados.meses.get(mes)
    if atual is None or not atual.valor:
        return Conteudo(filtros=filtros)

    # sem meta com filtro de linha
    meta = metas.de(mes, escolhidos) if linhas is None else None
    f_meta = farol(conn, ctx.cliente_id, "vendas_meta", mes)
    calc = Calculo(mp=mp, atual=atual, meta=meta, f_meta=f_meta)

    cartoes: list[Cartao | None] = [
        cartao_andamento(calc, dados, mes, tem_papel(f.vendas, "quantidade"))
    ]
    fatores = None
    if linhas is None:
        meta_clientes = metas.de(mes, escolhidos, clientes=True)
        fatores = cartao_fatores(conn, ctx, calc, dados, meta_clientes)
    ritmo = cartao_ritmo(calc, dados)
    cartoes += lado_a_lado(fatores, ritmo)
    tabela_linhas = cartao_linhas(dados, mes) if linhas is None else None
    blocos = (
        cartao_canais(calc, dados, metas, vendedores, escolhidos)
        if tem_papel(f.vendas, "vendedor")
        else None
    )
    cartoes += lado_a_lado(tabela_linhas, blocos)
    cartoes.append(cartao_doze_meses(dados, metas, mes, escolhidos, linhas is None))
    if linhas is None:
        cartoes.append(cartao_participacao(dados, mes))

    return Conteudo(
        filtros=filtros,
        destaques=destaques(calc),
        apoios=apoios(atual),
        cartoes=[c for c in cartoes if c is not None],
    )


def lado_a_lado(a: Cartao | None, b: Cartao | None) -> list[Cartao | None]:
    """Dois cartões da mesma fileira; se um falta, o outro ocupa a linha inteira."""
    if a is None and b is None:
        return []
    if a is None or b is None:
        unico = a if a is not None else b
        return [unico.model_copy(update={"largura": 12})] if unico is not None else []
    return [a, b]


@dataclass(frozen=True)
class Calculo:
    mp: MesPainel
    atual: MesVendas
    meta: Decimal | None
    f_meta: Farol

    @property
    def vendido(self) -> Decimal:
        return self.atual.valor

    @property
    def projecao(self) -> Decimal:
        return self.mp.projetar(self.atual.valor)

    @property
    def ritmo(self) -> Decimal | None:
        return self.vendido / self.mp.du_passados if self.mp.du_passados else None

    @property
    def necessario(self) -> Decimal | None:
        """Por dia útil, para a meta; None sem meta ou sem dia útil pela frente."""
        if self.meta is None or not self.mp.aberto:
            return None
        return (self.meta - self.vendido) / self.mp.du_restantes

    def status(self, valor: Decimal) -> Status:
        """Farol do valor contra a meta (só com meta)."""
        if not self.meta:
            raise ValueError("sem meta")
        return self.f_meta.status(valor / self.meta * CEM)


# ---------- topo ----------


def destaques(c: Calculo) -> list[ItemValor]:
    mp = c.mp
    nome = nome_mes(mp.mes)
    contexto = (
        f"{mp.du_passados} de {mp.du_mes} dias úteis" if mp.aberto else f"{mp.du_mes} dias úteis"
    )
    if c.meta:
        contexto += f"; {pct_texto(c.vendido / c.meta * CEM)} da meta"
    itens = [
        ItemValor(
            rotulo=f"Vendido em {nome}" if mp.fechado else f"Vendido até {mp.ultimo_dia:%d/%m}",
            valor=brl(c.vendido),
            contexto=contexto,
        )
    ]
    if mp.aberto and c.ritmo is not None:
        ritmo = f"se o ritmo de {moeda(c.ritmo)} por dia se mantiver"
        itens.append(
            ItemValor(
                rotulo="Projeção de fechamento",
                valor=brl(c.projecao),
                contexto=(
                    f"{pct_texto(c.projecao / c.meta * CEM)} da meta, {ritmo}" if c.meta else ritmo
                ),
                status=c.status(c.projecao) if c.meta else None,
            )
        )
    elif c.meta:
        itens.append(
            ItemValor(
                rotulo=f"Meta de {nome}",
                valor=brl(c.meta),
                contexto=f"{pct_texto(c.vendido / c.meta * CEM)} atingido",
                status=c.status(c.vendido),
            )
        )
    necessario = c.necessario
    if necessario is not None and c.ritmo is not None:
        faltam = f"nos {c.mp.du_restantes} dias úteis que faltam"
        if necessario <= 0:
            itens.append(
                ItemValor(
                    rotulo="Média diária necessária para a meta",
                    valor=brl(ZERO),
                    contexto="meta atingida",
                    status="saudavel",
                )
            )
        else:
            diferenca = necessario - c.ritmo
            itens.append(
                ItemValor(
                    rotulo="Média diária necessária para a meta",
                    valor=brl(necessario),
                    contexto=(
                        f"{moeda(diferenca)} a mais por dia, {faltam}"
                        if diferenca > 0
                        else f"{moeda(diferenca)} a menos que o ritmo atual, {faltam}"
                    ),
                )
            )
    return itens


def apoios(atual: MesVendas) -> list[ItemValor]:
    if not atual.pedidos:
        return []
    itens = [
        ItemValor(rotulo="Pedidos", valor=qtd(Decimal(atual.pedidos))),
        ItemValor(
            rotulo="Ticket por pedido", valor=brl(atual.valor / atual.pedidos, compacto=False)
        ),
    ]
    if atual.custo:
        itens.append(
            ItemValor(
                rotulo="Margem bruta das vendas",
                valor=pct((1 - atual.custo / atual.valor) * CEM),
            )
        )
    if atual.clientes:
        itens.append(
            ItemValor(
                rotulo="Pedidos por cliente",
                valor=qtd(Decimal(atual.pedidos) / atual.clientes, 1),
            )
        )
    return itens


# ---------- andamento do mês ----------


def degraus(
    mp: MesPainel,
    diarios: Mapping[date, Decimal],
    meta: Decimal | None,
    unidade: Any,
    texto: Callable[[Decimal], str],
) -> GraficoDegraus:
    """Acumulado dia a dia do mês: vendido até a última venda, projeção no ritmo dos dias
    úteis e meta acumulada por dia útil."""
    fim = ultimo_dia_do_mes(mp.mes)
    total = sum(diarios.values(), ZERO)
    ritmo = total / mp.du_passados if mp.du_passados else ZERO
    dias: list[DiaDegrau] = []
    real: list[Decimal | None] = []
    proj: list[Decimal | None] = []
    acumulado_meta: list[Decimal | None] = []
    acc_real = acc_meta = ultimo = ZERO
    dia = mp.mes
    while dia <= fim:
        util = mp.util(dia)
        vendeu = dia in diarios
        semana_eixo = (dia.day - 1) % 7
        dias.append(
            DiaDegrau(
                rotulo=f"{dia:%d}",
                titulo=f"{dia:%d/%m} · {SEMANA[dia.weekday()]}"
                + ("" if util or vendeu else " · sem venda"),
                util=util,
                eixo=semana_eixo in (0, 4),
                eixo_curto=semana_eixo == 0,
            )
        )
        if util and meta is not None and mp.du_mes:
            acc_meta += meta / mp.du_mes
        acumulado_meta.append(q(acc_meta))
        if dia <= mp.ultimo_dia:
            acc_real += diarios.get(dia, ZERO)
            ultimo = acc_real
            real.append(q(acc_real))
            proj.append(q(acc_real) if mp.aberto and dia == mp.ultimo_dia else None)
        else:
            real.append(None)
            if util:
                ultimo += ritmo
            proj.append(q(ultimo))
        dia += timedelta(days=1)

    projecao = mp.projetar(total)
    series: list[SerieDegrau] = []
    if meta is not None:
        series.append(
            SerieDegrau(
                nome="Meta acumulada",
                estilo="meta",
                valores=acumulado_meta,
                rotulo_fim=f"Meta {texto(meta)}",
                rotulo_fim_curto="Meta",
            )
        )
    if mp.aberto:
        series.append(
            SerieDegrau(
                nome="Projeção acumulada",
                estilo="projecao",
                valores=proj,
                rotulo_fim=f"Projeção {texto(projecao)}",
                rotulo_fim_curto="Proj.",
            )
        )
    series.append(SerieDegrau(nome="Vendido acumulado", estilo="realizado", valores=real))
    return GraficoDegraus(
        tipo="degraus",
        unidade=unidade,
        dias=dias,
        series=series,
        hoje=(
            Hoje(indice=mp.ultimo_dia.day - 1, rotulo=f"hoje: {texto(total)}")
            if mp.aberto
            else None
        ),
        falta=(
            Falta(de=q(projecao), ate=q(meta))
            if mp.aberto and meta is not None and projecao < meta
            else None
        ),
    )


def _nota_andamento(mp: MesPainel) -> str:
    feriados_uteis = sorted(d for d in mp.feriados if d.weekday() < 5)
    if not feriados_uteis:
        planos = "nos fins de semana"
    elif len(feriados_uteis) == 1:
        planos = f"nos fins de semana e no feriado de {feriados_uteis[0]:%d/%m}"
    else:
        datas = ", ".join(f"{d:%d/%m}" for d in feriados_uteis[:-1])
        planos = f"nos fins de semana e nos feriados de {datas} e {feriados_uteis[-1]:%d/%m}"
    return (
        "A venda só anda nos dias úteis: por isso a linha é uma escadinha, plana "
        f"{planos} (faixas cinzas). Linha cheia: vendido. Tracejada: projeção no ritmo "
        "atual. Cinza: meta acumulada. O trecho vermelho no fim é o que falta para a meta."
    )


def cartao_andamento(
    c: Calculo, dados: DadosVendas, mes: date, com_volume: bool
) -> CartaoAndamentoMes:
    mp = c.mp
    trechos = [
        TrechoRegua(
            estilo="realizado",
            rotulo="Vendido até hoje" if mp.aberto else f"Vendido em {nome_mes(mes)}",
            de=ZERO,
            ate=q(c.vendido),
            valor=brl(c.vendido),
        )
    ]
    if mp.aberto:
        trechos.append(
            TrechoRegua(
                estilo="projecao",
                rotulo="Projeção no ritmo atual",
                de=q(c.vendido),
                ate=q(c.projecao),
                valor=brl(c.projecao - c.vendido),
                com_sinal=True,
            )
        )
    if c.meta is not None and c.projecao < c.meta:
        trechos.append(
            TrechoRegua(
                estilo="falta",
                rotulo="Falta para a meta" if mp.aberto else "Abaixo da meta",
                de=q(c.projecao),
                ate=q(c.meta),
                valor=brl(c.meta - c.projecao),
            )
        )
    marcas = [] if c.meta is None else [MarcaRegua(rotulo=f"Meta {moeda(c.meta)}", valor=q(c.meta))]

    visoes = [
        VisaoAndamento(
            id="valor",
            rotulo="Valor",
            grafico=degraus(mp, {d: v for d, (v, _) in dados.dias.items()}, c.meta, "brl", moeda),
        )
    ]
    if com_volume and c.atual.quantidade:
        visoes.append(
            VisaoAndamento(
                id="volume",
                rotulo="Volume",
                grafico=degraus(
                    mp, {d: v for d, (_, v) in dados.dias.items()}, None, "qtd", numero
                ),
            )
        )
    return CartaoAndamentoMes(
        id="andamento-mes",
        tipo="andamento-mes",
        largura=12,
        titulo="Como o mês está andando",
        subtitulo="A régua mostra o mês inteiro; o gráfico, o acumulado por dia",
        nota=_nota_andamento(mp),
        regua=Regua(trechos=trechos, marcas=marcas),
        estatisticas=estatisticas(c, dados, mes),
        visoes=visoes,
    )


def estatisticas(c: Calculo, dados: DadosVendas, mes: date) -> list[ItemValor]:
    mp = c.mp
    itens = [
        ItemValor(
            rotulo="Dias úteis",
            valor=f"{mp.du_passados} de {mp.du_mes}",
            contexto=f"faltam {mp.du_restantes}" if mp.aberto else "mês completo",
        )
    ]
    if c.ritmo is not None:
        itens.append(
            ItemValor(
                rotulo="Ritmo atual" if mp.aberto else "Ritmo do mês",
                valor=brl(c.ritmo, sufixo="por dia"),
                contexto="média por dia útil",
            )
        )
    necessario = c.necessario
    if necessario is not None and necessario > 0 and c.ritmo is not None:
        itens.append(
            ItemValor(
                rotulo="Necessário para a meta",
                valor=brl(necessario, sufixo="por dia"),
                contexto=f"{mais(necessario - c.ritmo)} por dia sobre o ritmo",
                status=c.f_meta.status(c.ritmo / necessario * CEM),
            )
        )
    vendas_dia = [(d, v) for d, (v, _) in dados.dias.items() if v > 0]
    if len(vendas_dia) >= 2:
        melhor = max(vendas_dia, key=lambda x: x[1])
        pior = min(vendas_dia, key=lambda x: x[1])
        itens.append(
            ItemValor(
                rotulo="Melhor e pior dia",
                valor=f"{moeda(melhor[1])} · {moeda(pior[1])}",
                contexto=f"{melhor[0]:%d/%m} e {pior[0]:%d/%m}",
            )
        )
    anterior = mes_menos(mes, 1)
    if (ant := dados.meses.get(anterior)) is not None and ant.valor:
        diferenca = c.projecao - ant.valor
        sujeito = "a projeção fica" if mp.aberto else f"{nome_mes(mes)} ficou"
        lado = "acima" if diferenca >= 0 else "abaixo"
        itens.append(
            ItemValor(
                rotulo=f"Vendido em {nome_mes(anterior)}",
                valor=brl(ant.valor),
                contexto=f"{sujeito} {moeda(diferenca)} {lado}",
                status="atencao" if diferenca < 0 else "saudavel",
            )
        )
    return itens


# ---------- fatores ----------


def clientes_no_fim(
    conn: Conexao,
    ctx: ContextoConsolidado,
    mp: MesPainel,
    clientes: int,
    novos_anterior: Mapping[date, int],
) -> Decimal | None:
    """Clientes que vão ter comprado até o fim do mês: os de hoje × (clientes do mês
    anterior ÷ os que ele tinha no mesmo número de dias úteis). None sem mês anterior.

    `novos_anterior`: dia → clientes que compraram pela primeira vez no mês anterior."""
    if not mp.aberto:
        return Decimal(clientes)
    anterior = mes_menos(mp.mes, 1)
    fim = ultimo_dia_do_mes(anterior)
    fer = feriados(conn, ctx.cliente_id, anterior, fim)
    # dia em que o mês anterior completou os mesmos dias úteis
    corte, contados, dia = fim, 0, anterior
    while dia <= fim:
        if dia.weekday() < 5 and dia not in fer:
            contados += 1
            if contados == mp.du_passados:
                corte = dia
                break
        dia += timedelta(days=1)
    total = sum(novos_anterior.values())
    ate_corte = sum(n for d, n in novos_anterior.items() if d <= corte)
    if not total or not ate_corte:
        return None
    return Decimal(clientes) * total / ate_corte


def cartao_fatores(
    conn: Conexao,
    ctx: ContextoConsolidado,
    c: Calculo,
    dados: DadosVendas,
    meta_clientes: Decimal | None,
) -> CartaoFatores | None:
    if c.meta is None or meta_clientes is None or not c.atual.clientes:
        return None
    clientes = clientes_no_fim(conn, ctx, c.mp, c.atual.clientes, dados.novos_anterior)
    if not clientes:
        return None
    rotulo_real = "Projeção" if c.mp.aberto else "Realizado"
    linhas: list[tuple[str, Decimal, Decimal, Callable[[Decimal], Valor]]] = [
        ("Clientes que compram no mês", clientes, meta_clientes, qtd),
        ("Venda por cliente", c.projecao / clientes, c.meta / meta_clientes, brl_cheio),
    ]
    diferencas = [(real / alvo - 1) * CEM for _, real, alvo, _ in linhas]
    pior = min(range(len(linhas)), key=lambda i: diferencas[i])
    fatores = []
    for i, (nome, real, alvo, valor) in enumerate(linhas):
        d = diferencas[i]
        fatores.append(
            Fator(
                nome=nome,
                operador="×" if i else None,
                causa=i == pior and d < -LIMIAR_FATOR,
                colunas=[
                    ItemValor(rotulo=rotulo_real, valor=valor(real)),
                    ItemValor(rotulo="Meta", valor=valor(alvo)),
                    ItemValor(
                        rotulo="Diferença",
                        valor=pct(d),
                        com_sinal=True,
                        status=(
                            "critico"
                            if d < -LIMIAR_FATOR
                            else "saudavel"
                            if d > LIMIAR_FATOR
                            else None
                        ),
                    ),
                ],
            )
        )
    total = (c.projecao / c.meta - 1) * CEM
    return CartaoFatores(
        id="fatores-meta",
        tipo="fatores",
        largura=5,
        titulo="Por que a meta escorrega" if total < 0 else "O que sustenta a meta",
        subtitulo="Faturamento = clientes × venda por cliente",
        total=ItemValor(
            rotulo="Faturamento projetado" if c.mp.aberto else "Faturamento do mês",
            valor=brl(c.projecao),
            contexto=f"{pct_texto(total, sinal=True)} contra a meta",
            status=c.status(c.projecao),
        ),
        fatores=fatores,
    )


def brl_cheio(v: Decimal) -> Valor:
    return brl(v, compacto=False)


# ---------- ritmo por dia ----------


def cartao_ritmo(c: Calculo, dados: DadosVendas) -> CartaoGraficos | None:
    mp = c.mp
    meta_dia = c.meta / mp.du_mes if c.meta is not None and mp.du_mes else None
    necessario = c.necessario
    fim = ultimo_dia_do_mes(mp.mes)
    grupos: list[GrupoBarras] = []
    dia = mp.mes
    while dia <= fim:
        if mp.util(dia) or dia in dados.dias:
            barra: Barra | None = None
            if dia <= mp.ultimo_dia:
                valor = dados.dias.get(dia, (ZERO, ZERO))[0]
                estilo: Any = (
                    "serie" if meta_dia is None else "saudavel" if valor >= meta_dia else "atencao"
                )
                barra = Barra(nome="Vendido", valor=q(valor), estilo=estilo)
            elif necessario is not None and necessario > 0:
                barra = Barra(nome="Necessário", valor=q(necessario), estilo="fantasma")
            if barra is not None:
                grupos.append(
                    GrupoBarras(rotulo=f"{dia:%d}", titulo=f"{dia:%d/%m}", barras=[barra])
                )
        dia += timedelta(days=1)
    if not grupos:
        return None
    referencias: list[ReferenciaBarras] = []
    legenda: list[ItemLegenda] = []
    if meta_dia is not None:
        referencias.append(ReferenciaBarras(nome="Meta diária", valor=q(meta_dia), estilo="media"))
        legenda.append(ItemLegenda(nome="Meta diária", marca="media"))
    if necessario is not None and necessario > 0:
        referencias.append(
            ReferenciaBarras(nome="Necessário", valor=q(necessario), estilo="minimo")
        )
        legenda.append(ItemLegenda(nome="Necessário", marca="minimo"))
    return CartaoGraficos(
        id="ritmo-dia",
        tipo="graficos",
        largura=7,
        titulo="Ritmo por dia útil",
        subtitulo=(
            "Vendido por dia contra a meta diária e o que falta fazer por dia"
            if meta_dia is not None
            else "Vendido por dia"
        ),
        legenda=legenda or None,
        graficos=[
            GraficoCartao(
                grafico=GraficoBarras(
                    tipo="barras",
                    unidade="brl",
                    grupos=grupos,
                    referencias=referencias or None,
                )
            )
        ],
    )


# ---------- linhas de produto ----------


def _por_linha(
    dados: DadosVendas, meses: list[date]
) -> tuple[dict[str, tuple[Decimal, Decimal]], dict[str, str]]:
    """(valor, custo) por linha (slug) nos meses somados, e o rótulo de cada linha."""
    somas: dict[str, tuple[Decimal, Decimal]] = {}
    rotulos: dict[str, str] = {}
    escolhidos = set(meses)
    for (m, linha), (valor, custo) in dados.linhas.items():
        if m not in escolhidos:
            continue
        chave = slug(linha)
        rotulos.setdefault(chave, rotulo(linha))
        v, c = somas.get(chave, (ZERO, ZERO))
        somas[chave] = (v + valor, c + custo)
    return somas, rotulos


def _margem(valor: Decimal, custo: Decimal) -> Decimal | None:
    return (1 - custo / valor) * CEM if valor > 0 and custo > 0 else None


def cartao_linhas(dados: DadosVendas, mes: date) -> CartaoTabela | None:
    atual, rotulos = _por_linha(dados, [mes])
    base, _ = _por_linha(dados, meses_antes(mes, MESES_LINHAS))
    total = sum((v for v, _ in atual.values()), ZERO)
    total_base = sum((v for v, _ in base.values()), ZERO)
    if total <= 0 or len(atual) < 2:
        return None
    linhas: list[LinhaTabela] = []
    for chave, (valor, custo) in sorted(atual.items(), key=lambda x: -x[1][0]):
        if valor <= 0:
            continue
        part = valor / total * CEM
        v3, c3 = base.get(chave, (ZERO, ZERO))
        d_part = part - v3 / total_base * CEM if total_base > 0 else None
        margem = _margem(valor, custo)
        margem3 = _margem(v3, c3)
        d_margem = margem - margem3 if margem is not None and margem3 is not None else None
        farol_linha: Any = None
        if d_margem is not None:
            farol_linha = "critico" if d_margem <= -1 else "atencao" if d_margem < 0 else "saudavel"
        sem = Celula(valor="–", tom="apagado")
        linhas.append(
            LinhaTabela(
                status=farol_linha if farol_linha in ("critico", "atencao") else None,
                celulas=[
                    Celula(valor=rotulos[chave], negrito=True),
                    Celula(valor=brl(valor)),
                    Celula(valor=pct(part, 0)),
                    sem
                    if d_part is None
                    else Celula(valor=pp(d_part), com_sinal=True, seta=True, tom="apagado"),
                    sem if margem is None else Celula(valor=pct(margem), negrito=True),
                    sem
                    if d_margem is None
                    else Celula(
                        valor=pp(d_margem),
                        com_sinal=True,
                        tom="critico" if d_margem < 0 else "saudavel" if d_margem > 0 else None,
                    ),
                    Celula(pill=farol_linha) if farol_linha else Celula(),
                ],
            )
        )
    return CartaoTabela(
        id="linhas-produto",
        tipo="tabela",
        largura=8,
        titulo="Linhas de produto ou serviço: receita e margem",
        subtitulo=(
            f"Participação no mês e margem bruta, contra os {MESES_LINHAS} meses anteriores somados"
        ),
        colunas=[
            ColunaTabela(rotulo="Linha", alinhar="esquerda"),
            ColunaTabela(rotulo="Receita"),
            ColunaTabela(rotulo="Part."),
            ColunaTabela(rotulo=f"vs {MESES_LINHAS} meses", ocultar_no_celular=True),
            ColunaTabela(rotulo="Margem"),
            ColunaTabela(rotulo=f"vs {MESES_LINHAS} meses", ocultar_no_celular=True),
            ColunaTabela(rotulo="Farol", ocultar_no_celular=True),
        ],
        linhas=linhas,
    )


# ---------- canais ----------


def cartao_canais(
    c: Calculo,
    dados: DadosVendas,
    metas: Metas,
    vendedores: Mapping[str, Vendedor],
    escolhidos: list[str] | None,
) -> CartaoContraMeta | None:
    mes = c.mp.mes
    permitidos = None if escolhidos is None else set(escolhidos)
    grupos: dict[str, list[Vendedor]] = {}
    for v in vendedores.values():
        if permitidos is None or v.codigo in permitidos:
            grupos.setdefault(rotulo(v.canal) if v.canal else "Sem canal", []).append(v)
    itens: list[tuple[Decimal, ItemContraMeta]] = []
    for canal, vs in grupos.items():
        codigos = [v.codigo for v in vs]
        meta = metas.de(mes, codigos)
        if meta is None:
            continue
        com_meta = sum(1 for cod in codigos if metas.de(mes, [cod]) is not None)
        vendido = sum((dados.por_vendedor.get(cod, ZERO) for cod in codigos), ZERO)
        projecao = c.mp.projetar(vendido)
        atingimento = projecao / meta * CEM
        itens.append(
            (
                meta,
                ItemContraMeta(
                    nome=canal,
                    status=c.f_meta.status(atingimento),
                    atingimento=pct(atingimento, 0),
                    realizado=brl(projecao),
                    meta=brl(meta),
                    diferenca=brl(projecao - meta),
                    diferenca_ruim=projecao < meta,
                    detalhes=[
                        *([f"Vendido até hoje {moeda(vendido)}"] if c.mp.aberto else []),
                        f"{com_meta} {'vendedores' if com_meta != 1 else 'vendedor'}",
                    ],
                ),
            )
        )
    if not itens:
        return None
    return CartaoContraMeta(
        id="canais",
        tipo="contra-meta",
        largura=4,
        titulo="Canais",
        subtitulo=(
            "Projeção de cada canal contra a meta"
            if c.mp.aberto
            else "Vendido por canal contra a meta"
        ),
        layout="blocos",
        rotulo_realizado="Projeção" if c.mp.aberto else "Vendido",
        rotulo_meta="meta",
        itens=[i for _, i in sorted(itens, key=lambda x: -x[0])],
    )


# ---------- histórico ----------


def cartao_doze_meses(
    dados: DadosVendas,
    metas: Metas,
    mes: date,
    escolhidos: list[str] | None,
    com_meta: bool,
) -> CartaoGraficos | None:
    meses = [m for m in meses_antes(mes, MESES_HISTORICO) if m in dados.meses]
    meses = [m for m in meses if dados.meses[m].valor > 0]
    if len(meses) < 2:
        return None
    vendas: list[PontoCombo] = []
    clientes: list[PontoCombo] = []
    for m in meses:
        d = dados.meses[m]
        meta = metas.de(m, escolhidos) if com_meta else None
        meta_cli = metas.de(m, escolhidos, clientes=True) if com_meta else None
        vendas.append(
            PontoCombo(
                rotulo=rotulo_eixo(m),
                valor=q(d.valor),
                referencia=None if meta is None else q(meta),
                status="critico" if meta is not None and d.valor < meta * TOLERANCIA_META else None,
                linha=None if meta is None else q(d.valor / meta * CEM, 1),
            )
        )
        clientes.append(
            PontoCombo(
                rotulo=rotulo_eixo(m),
                valor=Decimal(d.clientes),
                referencia=None if meta_cli is None else q(meta_cli),
                status=(
                    "critico"
                    if meta_cli is not None and d.clientes < meta_cli * TOLERANCIA_META
                    else None
                ),
                linha=q(d.valor / d.clientes) if d.clientes else None,
            )
        )
    return CartaoGraficos(
        id="vendas-12-meses",
        tipo="graficos",
        largura=12,
        titulo=f"Vendas nos últimos {len(meses)} meses",
        subtitulo=(
            "Barras: realizado; traço: meta; linha de cima: atingimento ou ticket por "
            "cliente. Barra vermelha é mês abaixo da meta."
            if com_meta
            else "Barras: realizado; linha de cima: ticket por cliente."
        ),
        graficos=[
            GraficoCartao(
                titulo="Vendas por mês",
                grafico=GraficoCombo(
                    tipo="combo",
                    unidade="brl",
                    nomes=NomesCombo(
                        valor="Vendido",
                        referencia="Meta" if com_meta else None,
                        linha="% da meta" if com_meta else None,
                    ),
                    escala_linha=Escala(unidade="pct", casas=0) if com_meta else None,
                    pontos=vendas,
                ),
            ),
            GraficoCartao(
                titulo="Clientes que compraram e ticket por cliente",
                grafico=GraficoCombo(
                    tipo="combo",
                    unidade="qtd",
                    nomes=NomesCombo(
                        valor="Clientes",
                        referencia="Meta de clientes" if com_meta else None,
                        linha="Ticket por cliente",
                    ),
                    escala_linha=Escala(unidade="brl"),
                    pontos=clientes,
                ),
            ),
        ],
    )


def cartao_participacao(dados: DadosVendas, mes: date) -> CartaoGraficos | None:
    meses = [m for m in meses_antes(mes, MESES_HISTORICO) if _por_linha(dados, [m])[0]]
    if len(meses) < 2:
        return None
    periodo, rotulos = _por_linha(dados, meses)
    if len(periodo) < 2:
        return None
    ordem = [k for k, _ in sorted(periodo.items(), key=lambda x: -x[1][0])]
    principais = ordem[:LINHAS_NO_GRAFICO]
    outras = ordem[LINHAS_NO_GRAFICO:]
    series: dict[str, list[Decimal]] = {rotulos[k]: [] for k in principais}
    if outras:
        series["Outras"] = []
    for m in meses:
        do_mes, _ = _por_linha(dados, [m])
        total = sum((v for v, _ in do_mes.values()), ZERO)
        for k in principais:
            series[rotulos[k]].append(q(do_mes.get(k, (ZERO, ZERO))[0] / total * CEM, 1))
        if outras:
            resto = sum((do_mes.get(k, (ZERO, ZERO))[0] for k in outras), ZERO)
            series["Outras"].append(q(resto / total * CEM, 1))
    return CartaoGraficos(
        id="participacao-linhas",
        tipo="graficos",
        largura=12,
        titulo="Participação de cada linha nas vendas, mês a mês",
        subtitulo="Quanto cada linha representa das vendas de cada mês",
        graficos=[
            GraficoCartao(
                grafico=GraficoEmpilhado(
                    tipo="empilhado",
                    unidade="pct",
                    casas=0,
                    rotulos=[rotulo_eixo(m) for m in meses],
                    series=[SerieEmpilhada(nome=n, valores=v) for n, v in series.items()],
                )
            )
        ],
    )

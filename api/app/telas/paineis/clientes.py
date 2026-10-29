"""COM-03 — Clientes e atendimentos. REGRAS PROVISÓRIAS: confirmar com o negócio (ver
TODO.md).

Painel que não depende de saber quem é o cliente. Fontes e mês: ver base.py. Sem filtros
próprios.

- atendimento: pedido distinto; ticket: vendido ÷ atendimentos; itens por atendimento:
  pares pedido × produto distintos ÷ atendimentos;
- KPIs do mês contra a média dos 12 meses fechados anteriores: 100% da média ou mais,
  saudável; de 90% a 100%, atenção; abaixo, crítico. Atendimentos do mês em aberto: a
  projeção no ritmo dos dias úteis;
- dia da semana: atendimentos por dia de operação (as datas daquele dia da semana fora dos
  feriados) nos 3 meses fechados anteriores, contra os 3 antes deles; vermelho abaixo de
  98% da referência;
- faixas de valor do pedido nos 3 meses fechados anteriores. Limites em
  cfg.parametro_cliente `clientes.faixas_valor` ({"limites": [500, 2000, 5000, 10000]}, o
  padrão);
- curva ABC dos 12 meses fechados anteriores, pelo vendido de cada produto: A até 80% da
  receita acumulada, B até 95%, C o resto. Margem em R$: vendido − custo;
- curva de margem pela margem % de cada produto: em terços (A o terço de maior margem) ou
  pelos limites de `clientes.curva_margem` ({"a": 33, "b": 27}: A a partir de 33%, C abaixo
  de 27%);
- itens que mais vendem: os 10 maiores da curva A; "vs 3 meses": vendido nos 3 meses
  fechados anteriores contra os 3 antes deles.

Fora por decisão: o aviso (cita um módulo que ainda não existe), as notas que interpretam
(dia da semana, itens por atendimento) e a chamada das faixas.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import Any

from psycopg import sql

from app.core.db import Conexao
from app.farol.parametro import parametros_do_cliente, vigente
from app.seguranca.acesso import ContextoConsolidado
from app.telas.calendario import feriados, ultimo_dia_do_mes
from app.telas.contrato import Status
from app.telas.moldura import UltimaCarga
from app.telas.paineis.base import (
    Conteudo,
    Fontes,
    Tipo,
    escolher,
    fontes,
    ler_inicio,
    mes_painel,
    meses_antes,
    parametros,
    rotulo,
    selecao,
    vendas_sql,
)
from app.telas.paineis.contrato import (
    Barra,
    Cartao,
    CartaoCurvaAbc,
    CartaoFaixas,
    CartaoGraficos,
    CartaoMatriz,
    CartaoTabela,
    Celula,
    CelulaMatriz,
    Chamada,
    ClasseAbc,
    ColunaTabela,
    Escala,
    GraficoBarras,
    GraficoCartao,
    GraficoCombo,
    GraficoLinhas,
    GrupoBarras,
    LinhaFaixa,
    LinhaTabela,
    Marcador,
    NomesCombo,
    PontoCombo,
    ReferenciaBarras,
    ReferenciaLinha,
    SerieLinha,
)
from app.telas.paineis.formato import (
    CEM,
    SEMANA_NOME,
    brl,
    contra_referencia,
    lista,
    nome_mes,
    pct,
    pct_texto,
    q,
    qtd,
)
from app.telas.visao_geral.contra_referencia import MESES_CURTOS, rotulo_eixo
from app.telas.visao_geral.contrato import Kpi, Trecho
from app.telas.visao_geral.texto import moeda_cheia, numero
from app.telas.visao_geral.vendas import filtro_empresa, mes_menos

MESES_HISTORICO = 12
MESES_SEMANA = 3
FAIXAS_PADRAO = (Decimal(500), Decimal(2000), Decimal(5000), Decimal(10000))
# curva ABC: limites da receita acumulada
LIMITE_A = Decimal(80)
LIMITE_B = Decimal(95)
ITENS_NA_TABELA = 10
PONTOS_CURVA = (0, 5, 10, 15, 20, 30, 40, 50, 60, 70, 80, 90, 100)
TOLERANCIA = Decimal("0.98")
SEMANA_CURTA = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")
ZERO = Decimal(0)


@dataclass(frozen=True)
class MesAtendimentos:
    valor: Decimal
    pedidos: int
    itens: int

    @property
    def ticket(self) -> Decimal | None:
        return self.valor / self.pedidos if self.pedidos else None

    @property
    def itens_por_pedido(self) -> Decimal | None:
        return Decimal(self.itens) / self.pedidos if self.pedidos else None


@dataclass(frozen=True)
class Produto:
    codigo: str
    nome: str
    linha: str | None
    valor: Decimal
    custo: Decimal
    # vendido nos 3 meses fechados anteriores e nos 3 antes deles
    recente: Decimal
    anterior: Decimal

    @property
    def margem(self) -> Decimal | None:
        return (1 - self.custo / self.valor) * CEM if self.valor > 0 and self.custo else None


@dataclass
class DadosClientes:
    meses: dict[date, MesAtendimentos] = field(default_factory=dict)
    # dia → (vendido, atendimentos), nos 6 meses antes do mês da tela e nele
    dias: dict[date, tuple[Decimal, int]] = field(default_factory=dict)
    # índice da faixa → (atendimentos, vendido)
    faixas: dict[int, tuple[int, Decimal]] = field(default_factory=dict)
    produtos: list[Produto] = field(default_factory=list)


def _nomes_produtos(f: Fontes, ctx: ContextoConsolidado) -> sql.Composable:
    if not f.produtos:
        return sql.SQL("select null::text as codigo, null::text as nome where false")
    campos: Mapping[str, tuple[str, Tipo]] = {
        "codigo": ("codigo", "texto"),
        "nome": ("nome", "texto"),
    }
    return sql.SQL(
        "select distinct on (codigo) codigo, nome from ({p}) b"
        " where codigo is not null order by codigo, nome is null"
    ).format(p=selecao(f.produtos, campos, filtro_empresa(ctx)))


def ler_dados(
    conn: Conexao, ctx: ContextoConsolidado, f: Fontes, mes: date, limites: list[Decimal]
) -> DadosClientes:
    rows = conn.execute(
        sql.SQL(
            """
            with v as materialized (
                select * from ({vendas}) t where dia >= %(de)s and dia < %(ate)s
            ),
            n as ({nomes})
            select 'mes' as parte, date_trunc('month', dia)::date as data, null::text as chave,
                   null::text as nome, null::text as linha,
                   sum(valor) as valor, null::numeric as custo,
                   count(distinct pedido) as pedidos,
                   count(distinct (pedido, produto)) as itens,
                   null::numeric as recente, null::numeric as anterior
              from v group by 2
            union all
            select 'dia', dia, null, null, null, sum(valor), null, count(distinct pedido),
                   null, null, null
              from v where dia >= %(seis)s group by dia
            union all
            select 'faixa', null, faixa::text, null, null, sum(total), null, count(*),
                   null, null, null
              from (select pedido, sum(valor) as total
                      from v
                     where dia >= %(tres)s and dia < %(mes)s and pedido is not null
                     group by pedido) p
             cross join lateral (
                select count(*) as faixa from unnest(%(limites)s::numeric[]) l where p.total > l
             ) x
             group by faixa
            union all
            select 'produto', null, v.produto, max(n.nome), max(v.linha),
                   sum(v.valor), sum(v.custo), null, null,
                   sum(v.valor) filter (where v.dia >= %(tres)s),
                   sum(v.valor) filter (where v.dia >= %(seis)s and v.dia < %(tres)s)
              from v left join n on n.codigo = v.produto
             where v.dia < %(mes)s and v.produto is not null
             group by v.produto
            """
        ).format(vendas=vendas_sql(ctx, f), nomes=_nomes_produtos(f, ctx)),
        parametros(
            ctx,
            de=mes_menos(mes, MESES_HISTORICO),
            ate=ultimo_dia_do_mes(mes) + timedelta(days=1),
            mes=mes,
            tres=mes_menos(mes, MESES_SEMANA),
            seis=mes_menos(mes, 2 * MESES_SEMANA),
            limites=limites,
        ),
    ).fetchall()
    d = DadosClientes()
    for r in rows:
        valor = r["valor"] or ZERO
        if r["parte"] == "mes":
            d.meses[r["data"]] = MesAtendimentos(valor, r["pedidos"], r["itens"])
        elif r["parte"] == "dia":
            d.dias[r["data"]] = (valor, r["pedidos"])
        elif r["parte"] == "faixa":
            d.faixas[int(r["chave"])] = (r["pedidos"], valor)
        else:
            d.produtos.append(
                Produto(
                    codigo=r["chave"],
                    nome=rotulo(r["nome"]) if r["nome"] else r["chave"],
                    linha=rotulo(r["linha"]) if r["linha"] else None,
                    valor=valor,
                    custo=r["custo"] or ZERO,
                    recente=r["recente"] or ZERO,
                    anterior=r["anterior"] or ZERO,
                )
            )
    return d


def _parametro(conn: Conexao, ctx: ContextoConsolidado, chave: str, mes: date) -> Any:
    return vigente(parametros_do_cliente(conn, ctx.cliente_id), chave, mes)


def limites_faixas(valor: Any) -> list[Decimal]:
    """`clientes.faixas_valor`: {"limites": [500, 2000, ...]} em ordem crescente."""
    try:
        limites = [Decimal(str(x)) for x in valor["limites"]]
    except (TypeError, KeyError, ArithmeticError, ValueError):
        return list(FAIXAS_PADRAO)
    if not limites or limites != sorted(set(limites)) or limites[0] <= 0:
        return list(FAIXAS_PADRAO)
    return limites


def montar(
    ctx: ContextoConsolidado,
    escolhido: date | None,
    carga: UltimaCarga,
    pedidos: Mapping[str, str | None],
    a_decidir: bool = False,
) -> Conteudo:
    # nenhum filtro próprio: qualquer um é inválido
    for valor in pedidos.values():
        escolher(None, valor)
    conn = ctx.sessao.conn
    f = fontes(conn, ctx)
    if not f.vendas:
        return Conteudo()
    panorama = ler_inicio(conn, ctx, f, escolhido, com_vendedores=False).panorama
    mes = panorama.mes
    if mes is None or panorama.ultimo_dia is None:
        return Conteudo()
    fechado = mes < carga.data_snapshot.replace(day=1)
    mp = mes_painel(conn, ctx, mes, panorama.ultimo_dia, fechado)
    limites = limites_faixas(_parametro(conn, ctx, "clientes.faixas_valor", mes))
    dados = ler_dados(conn, ctx, f, mes, limites)
    atual = dados.meses.get(mes)
    if atual is None or not atual.pedidos:
        return Conteudo()

    historico = [m for m in meses_antes(mes, MESES_HISTORICO) if m in dados.meses]
    historico = [m for m in historico if dados.meses[m].pedidos]
    atendimentos = mp.projetar(Decimal(atual.pedidos))
    fer = feriados(conn, ctx.cliente_id, mes_menos(mes, 2 * MESES_SEMANA), mes)
    semana = por_dia_da_semana(dados, mes, fer)

    curva = curva_abc(dados.produtos)
    margem = _parametro(conn, ctx, "clientes.curva_margem", mes)
    cartoes: list[Cartao | None] = [
        cartao_atendimentos(dados, historico, mes, atendimentos, mp.aberto),
        cartao_semana(semana),
        cartao_faixas(dados, limites),
    ]
    if curva is not None:
        cortes = cortes_margem(curva, margem)
        cartoes += [
            cartao_curva(curva),
            cartao_cruzamento(curva, cortes),
            cartao_mais_vendem(curva, cortes),
        ]
    cartoes.append(cartao_itens(dados, historico))
    return Conteudo(
        kpis=kpis(dados, historico, atual, atendimentos, mp.du_passados, mp.aberto, semana),
        cartoes=_ajustar_larguras([c for c in cartoes if c is not None]),
    )


def _ajustar_larguras(cartoes: list[Cartao]) -> list[Cartao]:
    """Os pares 7 + 5 da grade: se um falta, o outro ocupa a linha inteira."""
    pares = {("dia-semana", "faixas-valor"), ("cruzamento-abc", "itens-mais-vendem")}
    ids = [c.id for c in cartoes]
    sozinhos = {
        id_ for a, b in pares for id_, outro in ((a, b), (b, a)) if id_ in ids and outro not in ids
    }
    return [c.model_copy(update={"largura": 12}) if c.id in sozinhos else c for c in cartoes]


# ---------- KPIs ----------


@dataclass(frozen=True)
class DiaDaSemana:
    dia: int
    # atendimentos por dia de operação
    recente: Decimal
    anterior: Decimal | None
    ticket: Decimal | None


def _datas(de: date, ate: date, dia: int, fer: set[date]) -> int:
    """Datas do dia da semana `dia` de `de` a `ate` (exclusive), fora dos feriados."""
    n, d = 0, de
    while d < ate:
        if d.weekday() == dia and d not in fer:
            n += 1
        d += timedelta(days=1)
    return n


def por_dia_da_semana(dados: DadosClientes, mes: date, fer: set[date]) -> list[DiaDaSemana]:
    tres, seis = mes_menos(mes, MESES_SEMANA), mes_menos(mes, 2 * MESES_SEMANA)
    resultado: list[DiaDaSemana] = []
    for dia in range(7):
        recentes = [
            (v, p) for d, (v, p) in dados.dias.items() if tres <= d < mes and d.weekday() == dia
        ]
        antes = [p for d, (_, p) in dados.dias.items() if seis <= d < tres and d.weekday() == dia]
        pedidos = sum(p for _, p in recentes)
        n = _datas(tres, mes, dia, fer)
        if not pedidos or not n:
            continue
        n_antes = _datas(seis, tres, dia, fer)
        resultado.append(
            DiaDaSemana(
                dia=dia,
                recente=Decimal(pedidos) / n,
                anterior=Decimal(sum(antes)) / n_antes if antes and n_antes else None,
                ticket=sum((v for v, _ in recentes), ZERO) / pedidos,
            )
        )
    return resultado


def _contra_media(valor: Decimal, media: Decimal | None) -> Status | None:
    return None if not media else contra_referencia(valor, media)


def kpis(
    dados: DadosClientes,
    historico: list[date],
    atual: MesAtendimentos,
    atendimentos: Decimal,
    du_passados: int,
    aberto: bool,
    semana: list[DiaDaSemana],
) -> list[Kpi]:
    n = len(historico)
    meses = [dados.meses[m] for m in historico]
    media_atend = Decimal(sum(m.pedidos for m in meses)) / n if n else None
    tickets = [t for m in meses if (t := m.ticket) is not None]
    media_ticket = sum(tickets, ZERO) / len(tickets) if tickets else None
    cestas = [i for m in meses if (i := m.itens_por_pedido) is not None]
    media_itens = sum(cestas, ZERO) / len(cestas) if cestas else None
    de_meses = f"média de {n} meses"

    itens: list[Kpi] = []
    contexto = "projeção" if aberto else "no mês"
    if media_atend is not None:
        contexto += f"; {de_meses}: {numero(media_atend)}"
    itens.append(
        Kpi(
            rotulo="Atendimentos no mês",
            valor=qtd(atendimentos),
            contexto=contexto,
            status=_contra_media(atendimentos, media_atend),
        )
    )
    ticket = atual.ticket
    if ticket is not None:
        if media_ticket:
            d = (ticket / media_ticket - 1) * CEM
            contexto = (
                f"{pct_texto(d, sinal=True)} sobre a {de_meses}"
                if d >= 0
                else f"{pct_texto(-d)} abaixo da {de_meses}"
            )
        else:
            contexto = "vendido ÷ atendimentos"
        itens.append(
            Kpi(
                rotulo="Ticket médio por atendimento",
                valor=brl(ticket, compacto=False),
                contexto=contexto,
                status=_contra_media(ticket, media_ticket),
            )
        )
    cesta = atual.itens_por_pedido
    if cesta is not None and atual.itens:
        itens.append(
            Kpi(
                rotulo="Itens por atendimento",
                valor=qtd(cesta, 1),
                contexto=(
                    f"{de_meses}: {numero(media_itens, 1)}"
                    if media_itens is not None
                    else "produtos diferentes por atendimento"
                ),
                status=_contra_media(cesta, media_itens),
            )
        )
    if du_passados:
        fortes = sorted(semana, key=lambda s: -s.recente)[:2]
        nomes = [SEMANA_NOME[s.dia].lower() for s in sorted(fortes, key=lambda s: s.dia)]
        itens.append(
            Kpi(
                rotulo="Atendimentos por dia útil",
                valor=qtd(Decimal(atual.pedidos) / du_passados),
                contexto=(
                    f"{lista(nomes)} são os dias mais fortes"
                    if len(nomes) == 2
                    else "atendimentos ÷ dias úteis do mês"
                ),
            )
        )
    return itens


# ---------- cartões ----------


def _mes_curto(m: date) -> str:
    return MESES_CURTOS[m.month - 1]


def cartao_atendimentos(
    dados: DadosClientes, historico: list[date], mes: date, atendimentos: Decimal, aberto: bool
) -> CartaoGraficos | None:
    if not historico:
        return None
    media = Decimal(sum(dados.meses[m].pedidos for m in historico)) / len(historico)
    tickets = [(m, dados.meses[m].ticket) for m in historico]
    atual = dados.meses[mes]
    nome_atual = f"{rotulo_eixo(mes)} (projeção)" if aberto else rotulo_eixo(mes)
    grupos = [
        GrupoBarras(
            rotulo=_mes_curto(m),
            titulo=rotulo_eixo(m),
            barras=[
                Barra(
                    nome="Atendimentos",
                    valor=Decimal(dados.meses[m].pedidos),
                    estilo="serie-media" if dados.meses[m].pedidos >= media else "serie-clara",
                )
            ],
        )
        for m in historico
    ]
    grupos.append(
        GrupoBarras(
            rotulo=_mes_curto(mes) + ("*" if aberto else ""),
            titulo=nome_atual,
            barras=[
                Barra(
                    nome="Atendimentos",
                    valor=q(atendimentos, 0),
                    estilo=(
                        "projecao"
                        if aberto
                        else "serie-media"
                        if atendimentos >= media
                        else "serie-clara"
                    ),
                )
            ],
        )
    )
    serie = [t for _, t in tickets] + [atual.ticket]
    validos = [t for _, t in tickets if t is not None]
    graficos = [
        GraficoCartao(
            titulo="Atendimentos por mês",
            nota=(
                f"Barra escura: acima da média de {len(historico)} meses (tracejado). "
                "Clara: abaixo."
            ),
            grafico=GraficoBarras(
                tipo="barras",
                unidade="qtd",
                rotulos_nas_barras=True,
                grupos=grupos,
                referencias=[
                    ReferenciaBarras(
                        nome=f"Média de {len(historico)} meses", valor=q(media, 1), estilo="media"
                    )
                ],
            ),
        )
    ]
    if validos and atual.ticket is not None and tickets[0][1] is not None:
        media_ticket = sum(validos, ZERO) / len(validos)
        primeiro, ultimo = tickets[0][1], atual.ticket
        verbo = "Subiu" if ultimo > primeiro else "Caiu" if ultimo < primeiro else "Ficou"
        nota = (
            f"{verbo} de {moeda_cheia(primeiro)} para {moeda_cheia(ultimo)} "
            f"desde {rotulo_eixo(tickets[0][0])}."
        )
        graficos.append(
            GraficoCartao(
                titulo="Ticket médio por atendimento",
                nota=nota,
                grafico=GraficoLinhas(
                    tipo="linhas",
                    unidade="brl",
                    rotulos=[g.rotulo for g in grupos],
                    titulos=[g.titulo or g.rotulo for g in grupos],
                    series=[
                        SerieLinha(
                            nome="Ticket médio",
                            valores=[None if t is None else q(t) for t in serie],
                            estilo="principal",
                            area=True,
                        )
                    ],
                    referencia=ReferenciaLinha(
                        valor=q(media_ticket), rotulo=f"média {moeda_cheia(media_ticket)}"
                    ),
                    marcadores=[
                        Marcador(serie=0, i=0, rotulo=moeda_cheia(primeiro)),
                        Marcador(serie=0, i=len(serie) - 1, rotulo=moeda_cheia(ultimo)),
                    ],
                ),
            )
        )
    return CartaoGraficos(
        id="atendimentos-ticket",
        tipo="graficos",
        largura=12,
        titulo="Atendimentos e ticket médio",
        subtitulo=(
            "Quantos atendimentos houve em cada mês e quanto cada um valeu."
            + (f" A barra amarela é a projeção de {nome_mes(mes)}." if aberto else "")
        ),
        graficos=graficos,
    )


def cartao_semana(semana: list[DiaDaSemana]) -> CartaoGraficos | None:
    if len(semana) < 2:
        return None
    return CartaoGraficos(
        id="dia-semana",
        tipo="graficos",
        largura=7,
        titulo="Movimento por dia da semana",
        subtitulo=(
            f"Média de atendimentos por dia nos últimos {MESES_SEMANA} meses; o traço são "
            f"os {MESES_SEMANA} meses anteriores. Linha: ticket médio."
        ),
        graficos=[
            GraficoCartao(
                grafico=GraficoCombo(
                    tipo="combo",
                    unidade="qtd",
                    nomes=NomesCombo(
                        valor=f"Últimos {MESES_SEMANA} meses",
                        referencia=f"{MESES_SEMANA} meses anteriores",
                        linha="Ticket médio",
                    ),
                    escala_linha=Escala(unidade="brl"),
                    pontos=[
                        PontoCombo(
                            rotulo=SEMANA_CURTA[s.dia],
                            titulo=SEMANA_NOME[s.dia],
                            valor=q(s.recente, 1),
                            referencia=None if s.anterior is None else q(s.anterior, 1),
                            status=(
                                "critico"
                                if s.anterior is not None and s.recente < s.anterior * TOLERANCIA
                                else None
                            ),
                            linha=None if s.ticket is None else q(s.ticket),
                        )
                        for s in semana
                    ],
                )
            )
        ],
    )


def rotulos_faixas(limites: list[Decimal]) -> list[str]:
    """[500, 2000, 5000, 10000] → "Até R$ 500", "R$ 500 a 2 mil", "R$ 2 a 5 mil",
    "R$ 5 a 10 mil", "Acima de R$ 10 mil"."""

    def curto(v: Decimal) -> str:
        return f"{numero(v / 1000)} mil" if v >= 1000 else numero(v)

    rotulos = [f"Até R$ {curto(limites[0])}"]
    for a, b in pairwise(limites):
        if a >= 1000:
            rotulos.append(f"R$ {numero(a / 1000)} a {curto(b)}")
        else:
            rotulos.append(f"R$ {curto(a)} a {curto(b)}")
    rotulos.append(f"Acima de R$ {curto(limites[-1])}")
    return rotulos


def cartao_faixas(dados: DadosClientes, limites: list[Decimal]) -> CartaoFaixas | None:
    pedidos = sum(n for n, _ in dados.faixas.values())
    valor = sum((v for _, v in dados.faixas.values()), ZERO)
    if not pedidos or valor <= 0:
        return None
    return CartaoFaixas(
        id="faixas-valor",
        tipo="faixas",
        largura=5,
        titulo="Atendimentos por faixa de valor",
        subtitulo=(f"Participação nos atendimentos e na receita, últimos {MESES_SEMANA} meses"),
        series=("Atendimentos", "Receita"),
        escala=Escala(unidade="pct", casas=0),
        linhas=[
            LinhaFaixa(
                rotulo=r,
                valores=(
                    q(Decimal(dados.faixas.get(i, (0, ZERO))[0]) / pedidos * CEM, 1),
                    q(dados.faixas.get(i, (0, ZERO))[1] / valor * CEM, 1),
                ),
            )
            for i, r in enumerate(rotulos_faixas(limites))
        ],
    )


# ---------- curva ABC ----------


@dataclass(frozen=True)
class ItemAbc:
    p: Produto
    classe: str
    # receita acumulada até este item, em % do total
    acumulado: Decimal


def curva_abc(produtos: list[Produto]) -> list[ItemAbc] | None:
    """Produtos com venda, do que mais vende para o que menos vende, com a classe."""
    validos = sorted((p for p in produtos if p.valor > 0), key=lambda p: (-p.valor, p.codigo))
    total = sum((p.valor for p in validos), ZERO)
    if len(validos) < 3 or total <= 0:
        return None
    itens: list[ItemAbc] = []
    antes = ZERO
    for p in validos:
        # a classe é a do ponto em que o item começa: o que cruza 80% ainda é A
        classe = "A" if antes < LIMITE_A else "B" if antes < LIMITE_B else "C"
        antes += p.valor / total * CEM
        itens.append(ItemAbc(p=p, classe=classe, acumulado=antes))
    return itens


@dataclass(frozen=True)
class CortesMargem:
    # margem % a partir de `a`: A; abaixo de `b`: C
    a: Decimal
    b: Decimal
    tercos: bool

    def classe(self, margem: Decimal | None) -> str | None:
        if margem is None:
            return None
        return "A" if margem >= self.a else "C" if margem < self.b else "B"


def cortes_margem(curva: list[ItemAbc], parametro: Any) -> CortesMargem:
    """Limites do parâmetro `clientes.curva_margem` ou, sem ele, os terços da margem %."""
    try:
        a, b = Decimal(str(parametro["a"])), Decimal(str(parametro["b"]))
        if a > b:
            return CortesMargem(a=a, b=b, tercos=False)
    except (TypeError, KeyError, ArithmeticError, ValueError):
        pass
    margens = sorted(m for i in curva if (m := i.p.margem) is not None)
    if not margens:
        return CortesMargem(a=ZERO, b=ZERO, tercos=True)
    n = len(margens)
    return CortesMargem(a=margens[(2 * n) // 3], b=margens[n // 3], tercos=True)


_CLASSES = {
    "A": ("Proteger", "Nunca pode faltar; acompanhe preço e custo toda semana.", "saudavel"),
    "B": ("Desenvolver", "Itens que podem subir para A com exposição e venda casada.", "atencao"),
    "C": ("Enxugar", "Muitos itens e pouca receita: revise o catálogo.", None),
}


def cartao_curva(curva: list[ItemAbc]) -> CartaoCurvaAbc:
    n = len(curva)
    total = sum((i.p.valor for i in curva), ZERO)
    margem_total = sum((i.p.valor - i.p.custo for i in curva), ZERO)
    classes: list[ClasseAbc] = []
    for letra, (acao, texto, status) in _CLASSES.items():
        da_classe = [i for i in curva if i.classe == letra]
        if not da_classe:
            continue
        receita = sum((i.p.valor for i in da_classe), ZERO) / total * CEM
        margem = (
            sum((i.p.valor - i.p.custo for i in da_classe), ZERO) / margem_total * CEM
            if margem_total > 0
            else None
        )
        resumo = [
            Trecho(texto=f"{pct_texto(Decimal(len(da_classe)) / n * CEM, 0)} dos itens · "),
            Trecho(texto=pct_texto(receita, 0), destaque=True),
            Trecho(texto=" da receita"),
        ]
        if margem is not None:
            resumo += [
                Trecho(texto=" · "),
                Trecho(texto=pct_texto(margem, 0), destaque=True),
                Trecho(texto=" da margem"),
            ]
        classes.append(
            ClasseAbc(
                rotulo=f"Curva {letra}",
                acao=acao,
                quantidade=qtd(Decimal(len(da_classe)), sufixo="itens"),
                textos=[resumo, [Trecho(texto=texto)]],
                status=status,  # type: ignore[arg-type]
            )
        )

    # receita acumulada nos pontos do eixo (% dos itens) e no fim da curva A
    n_a = sum(1 for i in curva if i.classe == "A")
    pct_a = q(Decimal(n_a) / n * CEM, 0)
    pontos = sorted({Decimal(p) for p in PONTOS_CURVA} | {pct_a})

    def acumulado(pct_itens: Decimal) -> Decimal:
        k = int((pct_itens / CEM * n).to_integral_value())
        return ZERO if k <= 0 else curva[min(k, n) - 1].acumulado

    valores: list[Decimal | None] = [q(acumulado(p), 1) for p in pontos]
    i_a = pontos.index(pct_a)
    return CartaoCurvaAbc(
        id="curva-abc",
        tipo="curva-abc",
        largura=12,
        titulo="Curva ABC de produtos e serviços",
        subtitulo=(
            f"O que sustenta a receita e a margem nos últimos {MESES_HISTORICO} meses. "
            "Vale para prato, peça, serviço ou produto."
        ),
        classes=classes,
        grafico=GraficoLinhas(
            tipo="linhas",
            unidade="pct",
            casas=0,
            piso=ZERO,
            rotulos=[f"{numero(p)}%" for p in pontos],
            titulos=[f"{numero(p)}% dos itens" for p in pontos],
            series=[
                SerieLinha(nome="Receita acumulada", valores=valores, estilo="principal", area=True)
            ],
            marcadores=[
                Marcador(
                    serie=0,
                    i=i_a,
                    rotulo=f"{numero(pct_a)}% dos itens → {numero(acumulado(pct_a))}%",
                )
            ],
        ),
        nota_grafico=(
            "Receita acumulada contra o percentual de itens, do que mais vende para o que "
            "menos vende."
        ),
    )


def _subtitulo_margem(c: CortesMargem) -> str:
    regra = "em terços da margem percentual" if c.tercos else "pela margem percentual"
    return (
        f"Quantos itens em cada combinação. Curva de margem {regra}: A a partir de "
        f"{pct_texto(c.a)}, B de {pct_texto(c.b)} a {pct_texto(c.a)}, C abaixo de "
        f"{pct_texto(c.b)}."
    )


def cartao_cruzamento(curva: list[ItemAbc], cortes: CortesMargem) -> CartaoMatriz | None:
    contagem: dict[tuple[str, str], int] = {}
    for i in curva:
        m = cortes.classe(i.p.margem)
        if m is not None:
            contagem[(i.classe, m)] = contagem.get((i.classe, m), 0) + 1
    if not contagem:
        return None
    letras = ("A", "B", "C")
    destaque: dict[tuple[str, str], Status] = {("A", "C"): "critico", ("A", "A"): "saudavel"}
    criticos = contagem.get(("A", "C"), 0)
    return CartaoMatriz(
        id="cruzamento-abc",
        tipo="matriz",
        largura=5,
        titulo="Cruzamento: curva de receita × curva de margem",
        subtitulo=_subtitulo_margem(cortes),
        linhas=[f"Receita {x}" for x in letras],
        colunas=[f"Margem {x}" for x in letras],
        valores=[
            [
                CelulaMatriz(valor=Decimal(contagem.get((r, m), 0)), status=destaque.get((r, m)))
                for m in letras
            ]
            for r in letras
        ],
        chamada=(
            Chamada(
                status="critico",
                texto=[
                    Trecho(
                        texto=f"{criticos} {'itens' if criticos != 1 else 'item'}", destaque=True
                    ),
                    Trecho(
                        texto=(
                            " na curva A de receita e na C de margem: vendem muito e deixam "
                            "pouco. São os primeiros a rever preço ou custo."
                            if criticos != 1
                            else " na curva A de receita e na C de margem: vende muito e deixa "
                            "pouco. É o primeiro a rever preço ou custo."
                        )
                    ),
                ],
            )
            if criticos
            else None
        ),
    )


def cartao_mais_vendem(curva: list[ItemAbc], cortes: CortesMargem) -> CartaoTabela | None:
    topo = [i for i in curva if i.classe == "A"][:ITENS_NA_TABELA]
    if not topo:
        return None
    fundo: dict[str, Status] = {"A": "saudavel", "B": "atencao", "C": "critico"}
    linhas: list[LinhaTabela] = []
    for i in topo:
        p = i.p
        classe = cortes.classe(p.margem)
        variacao = (p.recente / p.anterior - 1) * CEM if p.anterior > 0 else None
        linhas.append(
            LinhaTabela(
                celulas=[
                    Celula(valor=p.nome, sub=p.linha),
                    Celula(valor=brl(p.valor)),
                    Celula(valor=pct(i.acumulado, 0), tom="apagado"),
                    Celula(valor="–", tom="apagado")
                    if p.margem is None
                    else Celula(valor=pct(p.margem)),
                    Celula(valor="–", tom="apagado")
                    if classe is None
                    else Celula(valor=classe, negrito=True, fundo=fundo[classe]),
                    Celula(valor="–", tom="apagado")
                    if variacao is None
                    else Celula(
                        valor=pct(variacao),
                        com_sinal=True,
                        seta=True,
                        tom="critico" if variacao < 0 else "saudavel",
                    ),
                ]
            )
        )
    return CartaoTabela(
        id="itens-mais-vendem",
        tipo="tabela",
        variante="compacta",
        largura=7,
        titulo="Os itens que mais vendem",
        subtitulo="Curva A de receita, com a curva de margem e a tendência",
        colunas=[
            ColunaTabela(rotulo="Item", alinhar="esquerda"),
            ColunaTabela(rotulo=f"Receita {MESES_HISTORICO} meses"),
            ColunaTabela(rotulo="Acumulado", ocultar_no_celular=True),
            ColunaTabela(rotulo="Margem"),
            ColunaTabela(rotulo="Curva de margem", alinhar="centro"),
            ColunaTabela(rotulo=f"vs {MESES_SEMANA} meses", ocultar_no_celular=True),
        ],
        linhas=linhas,
    )


def cartao_itens(dados: DadosClientes, historico: list[date]) -> CartaoGraficos | None:
    pontos = [(m, c) for m in historico if (c := dados.meses[m].itens_por_pedido) is not None]
    pontos = [(m, c) for m, c in pontos if dados.meses[m].itens]
    if len(pontos) < 2:
        return None
    media = sum((c for _, c in pontos), ZERO) / len(pontos)
    return CartaoGraficos(
        id="itens-atendimento",
        tipo="graficos",
        largura=12,
        titulo="Itens por atendimento, mês a mês",
        subtitulo=(
            "Quantos itens diferentes entram em cada atendimento. O tracejado é a média de "
            f"{len(pontos)} meses."
        ),
        graficos=[
            GraficoCartao(
                grafico=GraficoLinhas(
                    tipo="linhas",
                    unidade="qtd",
                    casas=1,
                    rotulos=[rotulo_eixo(m) for m, _ in pontos],
                    series=[
                        SerieLinha(
                            nome="Itens por atendimento",
                            valores=[q(c, 2) for _, c in pontos],
                            estilo="principal",
                        )
                    ],
                    referencia=ReferenciaLinha(
                        valor=q(media, 2), rotulo=f"média {numero(media, 1)}"
                    ),
                    marcadores=[
                        Marcador(serie=0, i=len(pontos) - 1, rotulo=numero(pontos[-1][1], 1))
                    ],
                )
            )
        ],
    )

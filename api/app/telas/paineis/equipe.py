"""COM-02 — Equipe comercial. REGRAS PROVISÓRIAS: confirmar com o negócio (ver TODO.md).

Fontes e mês: ver base.py. Mostra nomes de vendedores: a rota exige perfil que veja dado
nominal. Filtros: canal e supervisor (cadastro do vendedor).

- equipe: os vendedores com meta no mês (meta pelo código, en_vendedor);
- projeção de cada vendedor: o vendido dele no ritmo do mês (dias úteis); atingimento =
  projeção ÷ meta. Farol: farol.vendas_meta (como na Visão geral);
- histórico mensal por vendedor: as tabelas de vendas nos meses que elas têm e, antes, o
  resumo mensal (faturado_vendedor, gravado pela carga a partir do arquivo inteiro);
- rampa: o primeiro mês com venda de cada vendedor vem do resumo (sem ele, não há rampa
  nem "novo"). Novo: até o 6º mês de casa. Quem já vendia no primeiro mês do histórico não
  é novo nem entra na curva (o começo dele é desconhecido). Curva esperada: média do
  atingimento, mês a mês, nos 6 primeiros meses de casa dos 5 vendedores atuais com maior
  atingimento médio nos 6 meses fechados anteriores (só quem tem os 6 primeiros meses com
  meta);
- em queda: atingimento menor que o do mês anterior por 3 meses seguidos ou mais;
- clientes atendidos: clientes distintos. Clientes no fim do mês (farol contra a meta de
  clientes): como na Vendas, pela curva de clientes do mês anterior;
- vencido na carteira: títulos a receber (f_receitas_*) do vendedor (en_codigovendedor do
  título) em aberto e vencidos na data da posição, no modelo D+2 do Financeiro. Prazo de
  recebimento: dias da emissão ao pagamento, ponderados pelo valor, dos títulos pagos no
  mês e nos 2 anteriores. Sem farol nos dois, até o negócio definir as faixas.

Fora, por enquanto: Mix (meta e real), a chamada do mapa e "clientes em risco".
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import cast

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
    Tipo,
    Vendedor,
    escolher,
    fontes,
    ler_inicio,
    mediana,
    mes_painel,
    meses_antes,
    opcoes,
    parametros,
    rotulo,
    selecao,
    tem_papel,
    vendas_sql,
)
from app.telas.paineis.contrato import (
    Cartao,
    CartaoContraMeta,
    CartaoDestaques,
    CartaoGraficos,
    CartaoTabela,
    Celula,
    ColunaTabela,
    EixoDispersao,
    GraficoCartao,
    GraficoDispersao,
    GraficoLinhas,
    ItemContraMeta,
    ItemDestaque,
    ItemValor,
    LinhaTabela,
    PontoDispersao,
    Quadrante,
    SerieLinha,
)
from app.telas.paineis.formato import (
    CEM,
    brl,
    contra_referencia,
    dias,
    lista,
    nome_mes,
    pct,
    pct_texto,
    pp,
    q,
    qtd,
)
from app.telas.paineis.vendas import clientes_no_fim, lado_a_lado
from app.telas.paralelo import em_paralelo
from app.telas.visao_geral.contra_referencia import MESES_CURTOS
from app.telas.visao_geral.financeiro import ultimo_vencimento_vencido
from app.telas.visao_geral.texto import moeda, numero
from app.telas.visao_geral.vendas import filtro_empresa, mes_menos

MESES_HISTORICO = 12
MESES_RAMPA = 6
MELHORES_NA_CURVA = 5
MESES_CALOR = 6
MESES_EVOLUCAO = 3
QUEDA_MINIMA = 3
MESES_PRAZO = 3
ZERO = Decimal(0)


@dataclass(frozen=True)
class DoMes:
    valor: Decimal
    custo: Decimal
    clientes: int


@dataclass
class DadosEquipe:
    # (mês, vendedor) → vendido, das tabelas de vendas
    mensal: dict[tuple[date, str], Decimal] = field(default_factory=dict)
    # vendedor → números do mês da tela
    mes: dict[str, DoMes] = field(default_factory=dict)
    # a equipe toda no mês da tela (clientes distintos na equipe)
    equipe: DoMes = field(default_factory=lambda: DoMes(ZERO, ZERO, 0))
    novos_anterior: dict[date, int] = field(default_factory=dict)


def ler_dados(
    conn: Conexao,
    ctx: ContextoConsolidado,
    f: Fontes,
    mes: date,
    vendedores: list[str] | None,
    equipe: list[str],
) -> DadosEquipe:
    rows = conn.execute(
        sql.SQL(
            """
            with v as materialized (
                select * from ({vendas}) t
                 where dia >= %(de)s and dia < %(ate)s and vendedor is not null
                   and (%(vendedores)s::text[] is null or vendedor = any(%(vendedores)s))
            )
            select 'mes' as parte, date_trunc('month', dia)::date as data, vendedor as chave,
                   sum(valor) as valor, sum(custo) as custo,
                   count(distinct cliente) filter (where dia >= %(mes)s) as clientes
              from v group by 2, 3
            union all
            select 'equipe', null, null, sum(valor), sum(custo), count(distinct cliente)
              from v where dia >= %(mes)s and vendedor = any(%(equipe)s)
            union all
            select 'novos', primeiro, null, null, null, count(*)
              from (select cliente, min(dia) as primeiro
                      from v
                     where dia >= %(anterior)s and dia < %(mes)s and cliente is not null
                       and vendedor = any(%(equipe)s)
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
            equipe=equipe,
        ),
    ).fetchall()
    d = DadosEquipe()
    for r in rows:
        if r["parte"] == "mes":
            d.mensal[(r["data"], r["chave"])] = r["valor"] or ZERO
            if r["data"] == mes:
                d.mes[r["chave"]] = DoMes(r["valor"] or ZERO, r["custo"] or ZERO, r["clientes"])
        elif r["parte"] == "equipe":
            d.equipe = DoMes(r["valor"] or ZERO, r["custo"] or ZERO, r["clientes"] or 0)
        else:
            d.novos_anterior[r["data"]] = r["clientes"]
    return d


@dataclass(frozen=True)
class Carteira:
    vencido: Decimal
    # prazo médio de recebimento, em dias; None sem pagamento no período
    prazo: Decimal | None


def ler_carteiras(
    conn: Conexao, ctx: ContextoConsolidado, f: Fontes, mp: MesPainel, posicao: date
) -> dict[str, Carteira]:
    """Vencido e prazo de recebimento por vendedor (títulos a receber)."""
    if not f.receber or not tem_papel(f.receber, "vendedor"):
        return {}
    campos: Mapping[str, tuple[str, Tipo]] = {
        "vendedor": ("vendedor", "texto"),
        "emissao": ("emissao", "data"),
        "vencimento": ("vencimento", "data"),
        "pagamento": ("pagamento", "data"),
        "valor": ("valor", "numero"),
    }
    fer = feriados(conn, ctx.cliente_id, mes_menos(mp.mes, 1), posicao)
    rows = conn.execute(
        sql.SQL(
            """
            with t as materialized (
                select vendedor, emissao,
                       coalesce(vencimento, emissao) as vencimento,
                       -- antes de 1901: data vazia gravada como 1899/1900
                       case when pagamento >= date '1901-01-01' then pagamento end as pagamento,
                       valor
                  from ({titulos}) x
                 where emissao is not null and valor > 0 and vendedor is not null
            )
            select vendedor,
                   coalesce(sum(valor) filter (
                       where emissao <= %(hoje)s
                         and (pagamento is null or pagamento > %(hoje)s)
                         and vencimento <= %(limite)s), 0) as vencido,
                   sum(greatest(pagamento - emissao, 0) * valor)
                       filter (where pagamento >= %(de)s and pagamento <= %(hoje)s)
                       as dias_valor,
                   sum(valor) filter (where pagamento >= %(de)s and pagamento <= %(hoje)s)
                       as pago
              from t
             group by vendedor
            """
        ).format(titulos=selecao(f.receber, campos, filtro_empresa(ctx))),
        parametros(
            ctx,
            hoje=posicao,
            limite=ultimo_vencimento_vencido(posicao, fer),
            de=mes_menos(mp.mes, MESES_PRAZO - 1),
        ),
    ).fetchall()
    return {
        r["vendedor"]: Carteira(
            vencido=r["vencido"],
            prazo=r["dias_valor"] / r["pago"] if r["pago"] else None,
        )
        for r in rows
    }


# ---------- regras puras ----------


def historico(
    tabelas: Mapping[tuple[date, str], Decimal], resumo: Mapping[tuple[date, str], Decimal]
) -> dict[tuple[date, str], Decimal]:
    """Vendido por mês e vendedor: as tabelas nos meses que elas têm; antes, o resumo."""
    meses_tabela = {m for m, _ in tabelas}
    return {**{k: v for k, v in resumo.items() if k[0] not in meses_tabela}, **tabelas}


def meses_de_casa(primeiro: date, mes: date) -> int:
    """1 no mês da primeira venda."""
    return (mes.year - primeiro.year) * 12 + mes.month - primeiro.month + 1


def primeiros_meses(resumo: Mapping[tuple[date, str], Decimal]) -> dict[str, date]:
    """Primeiro mês com venda de cada vendedor."""
    primeiros: dict[str, date] = {}
    for (m, vendedor), valor in resumo.items():
        if valor > 0 and (vendedor not in primeiros or m < primeiros[vendedor]):
            primeiros[vendedor] = m
    return primeiros


def em_queda(serie: list[Decimal | None]) -> int:
    """Meses seguidos de queda até o último ponto (atingimento menor que o anterior)."""
    n = 0
    for i in range(len(serie) - 1, 0, -1):
        atual, antes = serie[i], serie[i - 1]
        if atual is None or antes is None or atual >= antes:
            break
        n += 1
    return n


def curva_de_rampa(
    atual: list[str],
    mes: date,
    primeiros: Mapping[str, date],
    inicio_dados: date,
    atingimento: Callable[[date, str], Decimal | None],
) -> list[Decimal] | None:
    """Atingimento esperado do 1º ao 6º mês de casa; None com menos de 2 vendedores na conta.

    Entram os vendedores de `atual` que começaram depois do início dos dados, já passaram
    dos 6 primeiros meses e têm atingimento em todos eles; ficam os 5 com maior
    atingimento médio nos 6 meses fechados antes de `mes`.
    """
    candidatos: list[tuple[Decimal, list[Decimal]]] = []
    for v in atual:
        primeiro = primeiros.get(v)
        if primeiro is None or primeiro <= inicio_dados:
            continue
        if meses_de_casa(primeiro, mes) <= MESES_RAMPA:
            continue
        rampa = [atingimento(mes_menos(primeiro, -k), v) for k in range(MESES_RAMPA)]
        recentes = [atingimento(m, v) for m in meses_antes(mes, MESES_RAMPA)]
        validos = [x for x in recentes if x is not None]
        if any(x is None for x in rampa) or not validos:
            continue
        media = sum(validos, ZERO) / len(validos)
        candidatos.append((media, cast(list[Decimal], rampa)))
    melhores = [r for _, r in sorted(candidatos, key=lambda x: -x[0])[:MELHORES_NA_CURVA]]
    if len(melhores) < 2:
        return None
    return [sum((r[k] for r in melhores), ZERO) / len(melhores) for k in range(MESES_RAMPA)]


# ---------- montagem ----------


@dataclass(frozen=True)
class Linha:
    """Um vendedor da equipe no mês."""

    v: Vendedor
    meta: Decimal
    vendido: Decimal
    custo: Decimal
    clientes: int
    projecao: Decimal
    atingimento: Decimal
    status: Status
    # meses de casa (None sem rampa)
    casa: int | None
    novo: bool
    nome: str

    @property
    def margem(self) -> Decimal | None:
        return (1 - self.custo / self.vendido) * CEM if self.vendido > 0 and self.custo else None


def montar(
    ctx: ContextoConsolidado,
    escolhido: date | None,
    carga: UltimaCarga,
    pedidos: Mapping[str, str | None],
    a_decidir: bool = False,
) -> Conteudo:
    conn = ctx.sessao.conn
    f = fontes(conn, ctx)
    inicio = ler_inicio(conn, ctx, f, escolhido, com_resumo=True)
    panorama, vendedores, metas = inicio.panorama, inicio.vendedores, inicio.metas
    resumo = inicio.resumo_vendedor

    com_vendedor = tem_papel(f.vendas, "vendedor")
    canal_o: Opcoes | None = None
    supervisor_o: Opcoes | None = None
    if com_vendedor and any(v.canal for v in vendedores.values()):
        canal_o = opcoes("canal", "Canal", (v.canal for v in vendedores.values()))
    if com_vendedor and any(v.supervisor for v in vendedores.values()):
        supervisor_o = opcoes(
            "supervisor", "Supervisor", (v.supervisor for v in vendedores.values())
        )
    filtros = [o.filtro for o in (canal_o, supervisor_o) if o is not None]
    canais = escolher(canal_o, pedidos.get("canal"))
    supervisores = escolher(supervisor_o, pedidos.get("supervisor"))
    escolhidos: list[str] | None = None
    if canais is not None or supervisores is not None:
        escolhidos = [
            c
            for c, v in vendedores.items()
            if (canais is None or v.canal in canais)
            and (supervisores is None or v.supervisor in supervisores)
        ]

    mes = panorama.mes
    if not com_vendedor or mes is None or panorama.ultimo_dia is None:
        return Conteudo(filtros=filtros)
    fechado = mes < carga.data_snapshot.replace(day=1)
    mp = mes_painel(conn, ctx, mes, panorama.ultimo_dia, fechado)
    permitidos = None if escolhidos is None else set(escolhidos)
    equipe = sorted(
        v
        for (m, v) in metas.valor
        if m == mes and v is not None and (permitidos is None or v in permitidos)
    )
    if not equipe:
        return Conteudo(filtros=filtros)

    posicao = ultimo_dia_do_mes(mes) if fechado else carga.data_snapshot
    dados_e_carteiras = em_paralelo(
        ctx,
        [
            lambda c: ler_dados(c.sessao.conn, c, f, mes, escolhidos, equipe),
            lambda c: ler_carteiras(c.sessao.conn, c, f, mp, posicao),
        ],
    )
    dados = cast(DadosEquipe, dados_e_carteiras[0])
    carteiras = cast(dict[str, Carteira], dados_e_carteiras[1])

    f_meta = farol(conn, ctx.cliente_id, "vendas_meta", mes)
    hist = historico(dados.mensal, resumo)

    def atingimento(m: date, v: str) -> Decimal | None:
        meta = metas.de(m, [v])
        if meta is None:
            return None
        vendido = hist.get((m, v), ZERO)
        if m == mes:
            vendido = mp.projetar(vendido)
        return vendido / meta * CEM

    def atingimento_no_resumo(m: date, v: str) -> Decimal | None:
        """Sem filtro: a curva é da empresa, não do canal ou do supervisor escolhido."""
        meta = metas.de(m, [v])
        return None if meta is None else resumo.get((m, v), ZERO) / meta * CEM

    # rampa: só com o resumo por vendedor (o histórico inteiro)
    primeiros = primeiros_meses(resumo)
    inicio_dados = min((m for m, _ in resumo), default=None)
    curva: list[Decimal] | None = None
    if inicio_dados is not None:
        todos = sorted(v for (m, v) in metas.valor if m == mes and v is not None)
        curva = curva_de_rampa(todos, mes, primeiros, inicio_dados, atingimento_no_resumo)

    linhas = _linhas(equipe, vendedores, metas, dados, mp, f_meta, primeiros, inicio_dados)
    if not linhas:
        return Conteudo(filtros=filtros)
    linhas.sort(key=lambda x: -x.atingimento)

    series = {
        x.v.codigo: [atingimento(m, x.v.codigo) for m in meses_antes(mes, MESES_CALOR - 1)]
        + [x.atingimento]
        for x in linhas
    }
    ranking = cartao_ranking(linhas, mp)
    rampa = cartao_rampa(linhas, curva, atingimento, primeiros, mes) if curva else None
    cartoes: list[Cartao | None] = [
        cartao_destaques_mes(linhas, series, carteiras, mp),
        *lado_a_lado(ranking, rampa),
        cartao_mapa(linhas, mp),
        cartao_calor(linhas, series, curva, primeiros, mes, mp),
        cartao_report(
            linhas, metas, carteiras, mp, dados, conn, ctx, escolhidos is None, vendedores
        ),
    ]
    return Conteudo(
        filtros=filtros,
        destaques=destaques(linhas, curva, mp, f_meta),
        apoios=apoios(linhas, dados, series, carteiras),
        cartoes=[c for c in cartoes if c is not None],
    )


def _linhas(
    equipe: list[str],
    vendedores: Mapping[str, Vendedor],
    metas: Metas,
    dados: DadosEquipe,
    mp: MesPainel,
    f_meta: Farol,
    primeiros: Mapping[str, date],
    inicio_dados: date | None,
) -> list[Linha]:
    cadastro = {c: vendedores.get(c, Vendedor(c, None, None, None)) for c in equipe}
    primeiros_nomes = [v.primeiro_nome for v in cadastro.values()]
    linhas: list[Linha] = []
    for codigo, v in cadastro.items():
        meta = metas.de(mp.mes, [codigo])
        if meta is None:
            continue
        do_mes = dados.mes.get(codigo, DoMes(ZERO, ZERO, 0))
        projecao = mp.projetar(do_mes.valor)
        atingimento = projecao / meta * CEM
        primeiro = primeiros.get(codigo)
        casa = meses_de_casa(primeiro, mp.mes) if primeiro is not None else None
        novo = (
            casa is not None
            and casa <= MESES_RAMPA
            and inicio_dados is not None
            and primeiro is not None
            and primeiro > inicio_dados
        )
        repetido = primeiros_nomes.count(v.primeiro_nome) > 1
        linhas.append(
            Linha(
                v=v,
                meta=meta,
                vendido=do_mes.valor,
                custo=do_mes.custo,
                clientes=do_mes.clientes or 0,
                projecao=projecao,
                atingimento=atingimento,
                status=f_meta.status(atingimento),
                casa=casa,
                novo=novo,
                nome=v.exibicao if repetido else v.primeiro_nome,
            )
        )
    return linhas


def _status_contagem(ok: int, total: int) -> Status:
    """Todos, saudável; algum, atenção; nenhum, crítico."""
    if ok == total:
        return "saudavel"
    return "atencao" if ok else "critico"


def destaques(
    linhas: list[Linha], curva: list[Decimal] | None, mp: MesPainel, f_meta: Farol
) -> list[ItemValor]:
    no_ritmo = [x for x in linhas if x.atingimento >= CEM]
    nomes = [x.nome for x in no_ritmo]
    verbo = "projeta" if mp.aberto else "bateu"
    plural = "projetam" if mp.aberto else "bateram"
    if not nomes:
        contexto = "nenhum projeta a meta" if mp.aberto else "nenhum bateu a meta"
    elif len(nomes) <= 3:
        contexto = f"{lista(nomes)} {verbo if len(nomes) == 1 else plural} 100% ou mais"
    else:
        contexto = f"{len(nomes)} vendedores {plural} 100% ou mais"
    meta = sum((x.meta for x in linhas), ZERO)
    projecao = sum((x.projecao for x in linhas), ZERO)
    itens = [
        ItemValor(
            rotulo="Vendedores no ritmo da meta" if mp.aberto else "Vendedores que bateram a meta",
            valor=f"{len(no_ritmo)} de {len(linhas)}",
            contexto=contexto,
            status=_status_contagem(len(no_ritmo), len(linhas)),
        ),
        ItemValor(
            rotulo="Projeção da equipe" if mp.aberto else "Atingimento da equipe",
            valor=pct(projecao / meta * CEM),
            contexto=f"da meta de {moeda(meta)}",
            status=f_meta.status(projecao / meta * CEM),
        ),
    ]
    novos = [x for x in linhas if x.novo and x.casa is not None]
    if curva and novos:
        acima = [x for x in novos if x.atingimento >= curva[cast(int, x.casa) - 1]]
        itens.append(
            ItemValor(
                rotulo="Novos acima da curva de rampa",
                valor=f"{len(acima)} de {len(novos)}",
                contexto=lista([f"{x.nome} no {x.casa}º mês" for x in novos]),
                status=_status_contagem(len(acima), len(novos)),
            )
        )
    return itens


def apoios(
    linhas: list[Linha],
    dados: DadosEquipe,
    series: Mapping[str, list[Decimal | None]],
    carteiras: Mapping[str, Carteira],
) -> list[ItemValor]:
    itens: list[ItemValor] = []
    e = dados.equipe
    if e.valor > 0 and e.custo:
        itens.append(
            ItemValor(rotulo="Margem média da equipe", valor=pct((1 - e.custo / e.valor) * CEM))
        )
    itens.append(ItemValor(rotulo="Clientes atendidos", valor=qtd(Decimal(e.clientes))))
    queda = sum(1 for x in linhas if em_queda(series[x.v.codigo]) >= QUEDA_MINIMA)
    itens.append(
        ItemValor(rotulo=f"Em queda há {QUEDA_MINIMA} meses ou mais", valor=qtd(Decimal(queda)))
    )
    if carteiras:
        vencido = sum(
            (carteiras[x.v.codigo].vencido for x in linhas if x.v.codigo in carteiras), ZERO
        )
        itens.append(ItemValor(rotulo="Vencido nas carteiras", valor=brl(vencido)))
    return itens


def cartao_destaques_mes(
    linhas: list[Linha],
    series: Mapping[str, list[Decimal | None]],
    carteiras: Mapping[str, Carteira],
    mp: MesPainel,
) -> CartaoDestaques | None:
    if len(linhas) < 2:
        return None
    proj = "projeção" if mp.aberto else "atingimento"
    itens: list[ItemDestaque] = []

    mais = max(linhas, key=lambda x: x.vendido)
    if mais.vendido > 0:
        itens.append(
            ItemDestaque(
                categoria="Mais vendas",
                titulo=mais.v.exibicao,
                texto=(
                    f"{moeda(mais.vendido)} vendidos; {proj} de "
                    f"{pct_texto(mais.atingimento, 0)} da meta"
                ),
                status="saudavel",
            )
        )

    com_margem = [x for x in linhas if x.margem is not None]
    if len(com_margem) >= 2:
        vendido = sum((x.vendido for x in com_margem), ZERO)
        custo = sum((x.custo for x in com_margem), ZERO)
        media = (1 - custo / vendido) * CEM
        melhor = max(com_margem, key=lambda x: cast(Decimal, x.margem))
        margem = cast(Decimal, melhor.margem)
        if margem > media:
            itens.append(
                ItemDestaque(
                    categoria="Melhor margem",
                    titulo=melhor.v.exibicao,
                    texto=(
                        f"{pct_texto(margem)} de margem bruta, {numero(margem - media, 1)} "
                        "p.p. acima da média da equipe"
                    ),
                    status="saudavel",
                )
            )

    evolucoes: list[tuple[Decimal, Linha, Decimal, int]] = []
    for x in linhas:
        serie = series[x.v.codigo][-(MESES_EVOLUCAO + 1) :]
        inicio = next(((i, a) for i, a in enumerate(serie) if a is not None), None)
        if inicio is None or inicio[0] == len(serie) - 1:
            continue
        evolucoes.append((x.atingimento - inicio[1], x, inicio[1], len(serie) - 1 - inicio[0]))
    if evolucoes:
        ganho, x, de, meses = max(evolucoes, key=lambda e: e[0])
        if ganho > 0:
            casa = f", no {x.casa}º mês de casa" if x.novo else ""
            itens.append(
                ItemDestaque(
                    categoria="Melhor evolução",
                    titulo=x.v.exibicao,
                    texto=(
                        f"de {pct_texto(de, 0)} para {pct_texto(x.atingimento, 0)} da meta em "
                        f"{meses} {'meses' if meses > 1 else 'mês'}{casa}"
                    ),
                    status="saudavel",
                )
            )

    pior = min(linhas, key=lambda x: x.atingimento)
    if pior.atingimento < CEM:
        partes = [f"{proj} de {pct_texto(pior.atingimento, 0)} da meta"]
        serie = series[pior.v.codigo]
        queda = em_queda(serie)
        if queda >= 2:
            antes = serie[-1 - queda]
            partes.append(
                f"{queda} meses de queda: de {pct_texto(cast(Decimal, antes), 0)} para "
                f"{pct_texto(pior.atingimento, 0)}"
            )
        carteira = carteiras.get(pior.v.codigo)
        if carteira is not None and carteira.vencido > 0:
            partes.append(f"{moeda(carteira.vencido)} vencidos na carteira")
        itens.append(
            ItemDestaque(
                categoria="Precisa de apoio",
                titulo=pior.v.exibicao,
                texto="; ".join(partes),
                status=pior.status if pior.status != "saudavel" else "atencao",
            )
        )
    if not itens:
        return None
    return CartaoDestaques(
        id="destaques-mes",
        tipo="destaques",
        largura=12,
        titulo="Destaques do mês",
        subtitulo="Para reconhecer quem entrega e apoiar quem está com dificuldade",
        itens=itens,
    )


def _detalhe(x: Linha) -> str | None:
    partes = [rotulo(x.v.canal)] if x.v.canal else []
    if x.novo:
        partes.append(f"novo, {x.casa}º mês")
    return " · ".join(partes) or None


def cartao_ranking(linhas: list[Linha], mp: MesPainel) -> CartaoContraMeta:
    return CartaoContraMeta(
        id="ranking",
        tipo="contra-meta",
        largura=7,
        titulo="Ranking por projeção" if mp.aberto else "Ranking por atingimento",
        subtitulo=(
            "Projeção de cada vendedor contra a própria meta. O traço é a meta dele."
            if mp.aberto
            else "Vendido por cada vendedor contra a própria meta. O traço é a meta dele."
        ),
        layout="ranking",
        rotulo_realizado="projeção" if mp.aberto else "vendido",
        rotulo_meta="meta",
        itens=[
            ItemContraMeta(
                nome=x.v.exibicao,
                detalhe=_detalhe(x),
                status=x.status,
                atingimento=pct(x.atingimento, 0),
                realizado=brl(x.projecao),
                meta=brl(x.meta),
            )
            for x in linhas
        ],
    )


def cartao_rampa(
    linhas: list[Linha],
    curva: list[Decimal],
    atingimento: Callable[[date, str], Decimal | None],
    primeiros: Mapping[str, date],
    mes: date,
) -> CartaoGraficos | None:
    novos = [x for x in linhas if x.novo]
    if not novos:
        return None
    series = [
        SerieLinha(
            nome="Curva esperada",
            valores=[q(c, 1) for c in curva],
            estilo="pontilhada",
            rotulo_fim="Curva",
        )
    ]
    for i, x in enumerate(novos):
        primeiro = primeiros[x.v.codigo]
        valores: list[Decimal | None] = []
        for k in range(MESES_RAMPA):
            m = mes_menos(primeiro, -k)
            a = atingimento(m, x.v.codigo) if m <= mes else None
            valores.append(None if a is None else q(a, 1))
        series.append(
            SerieLinha(
                nome=x.nome,
                valores=valores,
                estilo="principal" if i == 0 else "secundaria",
                rotulo_fim=x.nome,
            )
        )
    return CartaoGraficos(
        id="rampa",
        tipo="graficos",
        largura=5,
        titulo="Rampa dos vendedores novos",
        subtitulo="Atingimento da meta por mês de casa, contra a curva esperada",
        nota=(
            "A curva é calculada pelo histórico da própria empresa: a média dos "
            f"{MELHORES_NA_CURVA} vendedores atuais com melhor atingimento nos últimos "
            f"{MESES_RAMPA} meses, nos seus primeiros {MESES_RAMPA} meses. O início de cada "
            "vendedor é o primeiro mês com venda."
        ),
        graficos=[
            GraficoCartao(
                grafico=GraficoLinhas(
                    tipo="linhas",
                    unidade="pct",
                    casas=0,
                    piso=Decimal(0),
                    rotulos=["1º mês", *(f"{k}º" for k in range(2, MESES_RAMPA + 1))],
                    titulos=[f"{k}º mês de casa" for k in range(1, MESES_RAMPA + 1)],
                    series=series,
                )
            )
        ],
    )


def cartao_mapa(linhas: list[Linha], mp: MesPainel) -> CartaoGraficos | None:
    pontos = [x for x in linhas if x.clientes > 0 and x.vendido > 0]
    if len(pontos) < 2:
        return None
    proj = "Projeção" if mp.aberto else "Atingimento"
    return CartaoGraficos(
        id="mapa-produtividade",
        tipo="graficos",
        largura=12,
        titulo="Mapa de produtividade",
        subtitulo=(
            "Como cada vendedor vende: quantos clientes atende e quanto vende para cada um. "
            "O tamanho é o vendido; a cor, o atingimento da meta."
        ),
        graficos=[
            GraficoCartao(
                grafico=GraficoDispersao(
                    tipo="dispersao",
                    eixo_x=EixoDispersao(rotulo="Clientes atendidos no mês", unidade="qtd"),
                    eixo_y=EixoDispersao(rotulo="Venda por cliente", unidade="brl"),
                    corte_x=mediana([Decimal(x.clientes) for x in pontos]),
                    corte_y=q(mediana([x.vendido / x.clientes for x in pontos])),
                    quadrantes=[
                        Quadrante(
                            posicao="superior-esquerdo", rotulo="Poucos clientes, compra alta"
                        ),
                        Quadrante(
                            posicao="superior-direito", rotulo="Muitos clientes, compra alta"
                        ),
                        Quadrante(
                            posicao="inferior-esquerdo", rotulo="Poucos clientes, compra baixa"
                        ),
                        Quadrante(
                            posicao="inferior-direito", rotulo="Muitos clientes, compra baixa"
                        ),
                    ],
                    pontos=[
                        PontoDispersao(
                            nome=x.nome,
                            x=Decimal(x.clientes),
                            y=q(x.vendido / x.clientes),
                            tamanho=q(x.vendido),
                            status=x.status,
                            rotular=True,
                            dica=[
                                ItemValor(rotulo="Vendedor", valor=x.v.exibicao),
                                ItemValor(
                                    rotulo="Clientes atendidos", valor=qtd(Decimal(x.clientes))
                                ),
                                ItemValor(
                                    rotulo="Venda por cliente",
                                    valor=brl(x.vendido / x.clientes, compacto=False),
                                ),
                                ItemValor(rotulo="Vendido", valor=brl(x.vendido)),
                                ItemValor(
                                    rotulo=proj,
                                    valor=f"{pct_texto(x.atingimento, 0)} da meta",
                                ),
                            ],
                        )
                        for x in pontos
                    ],
                )
            )
        ],
    )


def _fundo(valor: Decimal, referencia: Decimal) -> Status:
    """Calor: na referência ou acima, saudável; até 10 p.p. abaixo, atenção."""
    if valor >= referencia:
        return "saudavel"
    return "atencao" if valor >= referencia - 10 else "critico"


def cartao_calor(
    linhas: list[Linha],
    series: Mapping[str, list[Decimal | None]],
    curva: list[Decimal] | None,
    primeiros: Mapping[str, date],
    mes: date,
    mp: MesPainel,
) -> CartaoTabela:
    meses = [*meses_antes(mes, MESES_CALOR - 1), mes]
    rotulos = [MESES_CURTOS[m.month - 1] for m in meses]
    if mp.aberto:
        rotulos[-1] += "*"
    tabela: list[LinhaTabela] = []
    for x in linhas:
        serie = series[x.v.codigo]
        celulas = [Celula(valor=x.v.exibicao, etiqueta="rampa" if x.novo and curva else None)]
        for m, a in zip(meses, serie, strict=True):
            if a is None:
                celulas.append(Celula(valor="–", tom="apagado"))
                continue
            referencia = Decimal(100)
            sub = None
            primeiro = primeiros.get(x.v.codigo)
            if x.novo and curva and primeiro is not None:
                k = meses_de_casa(primeiro, m) - 1
                if 0 <= k < MESES_RAMPA:
                    referencia = q(curva[k], 0)
                    sub = f"curva: {numero(referencia)}%"
            celulas.append(Celula(valor=pct(a, 0), fundo=_fundo(a, referencia), sub=sub))
        # "Em 3 meses": do 3º mês antes até o da tela; sem ele, desde o primeiro com número
        janela = serie[-(MESES_EVOLUCAO + 1) :]
        base = janela[0]
        desde_entrada = False
        if base is None:
            base = next((a for a in janela[1:-1] if a is not None), None)
            desde_entrada = True
        if base is None or serie[-1] is None:
            celulas.append(Celula(valor="–", tom="apagado"))
        else:
            d = serie[-1] - base
            celulas.append(
                Celula(
                    valor=pp(d, 0),
                    com_sinal=True,
                    seta=True,
                    negrito=True,
                    tom="saudavel" if d > 0 else "critico" if d < 0 else "apagado",
                    sub="desde a entrada" if desde_entrada else None,
                )
            )
        tabela.append(LinhaTabela(celulas=celulas))
    subtitulo = "Percentual da meta em cada mês."
    if mp.aberto:
        subtitulo += f" {nome_mes(mes).capitalize()} é projeção."
    if curva and any(x.novo for x in linhas):
        subtitulo += " Novos são comparados com a curva de rampa."
    return CartaoTabela(
        id="atingimento-6-meses",
        tipo="tabela",
        variante="calor",
        largura=12,
        titulo=f"Atingimento nos últimos {MESES_CALOR} meses",
        subtitulo=subtitulo,
        colunas=[
            ColunaTabela(rotulo="Vendedor", alinhar="esquerda"),
            *(ColunaTabela(rotulo=r, alinhar="centro") for r in rotulos),
            ColunaTabela(rotulo=f"Em {MESES_EVOLUCAO} meses", alinhar="centro"),
        ],
        linhas=tabela,
    )


def cartao_report(
    linhas: list[Linha],
    metas: Metas,
    carteiras: Mapping[str, Carteira],
    mp: MesPainel,
    dados: DadosEquipe,
    conn: Conexao,
    ctx: ContextoConsolidado,
    sem_filtro: bool,
    vendedores: Mapping[str, Vendedor],
) -> CartaoTabela:
    metas_clientes = {x.v.codigo: metas.de(mp.mes, [x.v.codigo], clientes=True) for x in linhas}
    com_clientes = any(m is not None for m in metas_clientes.values())
    # clientes no fim do mês para cada cliente de hoje (curva do mês anterior)
    fator = clientes_no_fim(conn, ctx, mp, 1, dados.novos_anterior)
    sem = Celula(valor="–", tom="apagado")

    colunas = [
        ColunaTabela(rotulo="Vendedor", alinhar="esquerda"),
        ColunaTabela(rotulo="Canal", alinhar="esquerda"),
        ColunaTabela(rotulo="Meta do mês"),
        ColunaTabela(rotulo="Vendido"),
        ColunaTabela(rotulo="Média diária meta"),
        ColunaTabela(rotulo="Média diária real"),
        ColunaTabela(rotulo="Projeção" if mp.aberto else "Fechamento"),
        ColunaTabela(rotulo="%"),
    ]
    if mp.aberto:
        colunas.append(ColunaTabela(rotulo="Necessário por dia"))
    if com_clientes:
        colunas.append(ColunaTabela(rotulo="Clientes meta"))
    colunas += [ColunaTabela(rotulo="Clientes atendidos"), ColunaTabela(rotulo="Margem")]
    if carteiras:
        colunas += [
            ColunaTabela(rotulo="Vencido na carteira"),
            ColunaTabela(rotulo="Prazo de recebimento"),
        ]

    tabela: list[LinhaTabela] = []
    for x in linhas:
        media_meta = x.meta / mp.du_mes if mp.du_mes else None
        media_real = x.vendido / mp.du_passados if mp.du_passados else None
        celulas = [
            Celula(valor=x.v.exibicao),
            Celula(valor=rotulo(x.v.canal) if x.v.canal else "–", tom="apagado"),
            Celula(valor=brl(x.meta)),
            Celula(valor=brl(x.vendido)),
            sem if media_meta is None else Celula(valor=brl(media_meta)),
            sem
            if media_real is None or media_meta is None
            else Celula(valor=brl(media_real), fundo=contra_referencia(media_real, media_meta)),
            Celula(valor=brl(x.projecao)),
            Celula(valor=pct(x.atingimento, 0), fundo=x.status, negrito=True),
        ]
        if mp.aberto:
            necessario = (x.meta - x.vendido) / mp.du_restantes
            celulas.append(Celula(valor=brl(max(necessario, ZERO))))
        meta_cli = metas_clientes[x.v.codigo]
        if com_clientes:
            celulas.append(sem if meta_cli is None else Celula(valor=qtd(meta_cli)))
        fundo_cli: Status | None = None
        if meta_cli is not None and fator is not None:
            fundo_cli = contra_referencia(x.clientes * fator, meta_cli)
        celulas.append(Celula(valor=qtd(Decimal(x.clientes)), fundo=fundo_cli))
        celulas.append(sem if x.margem is None else Celula(valor=pct(x.margem)))
        if carteiras:
            carteira = carteiras.get(x.v.codigo)
            celulas.append(sem if carteira is None else Celula(valor=brl(carteira.vencido)))
            celulas.append(
                sem
                if carteira is None or carteira.prazo is None
                else Celula(valor=dias(carteira.prazo))
            )
        tabela.append(LinhaTabela(celulas=celulas))

    nota = None
    if sem_filtro:
        na_equipe = {x.v.codigo for x in linhas}
        fora = sorted(
            (c for c, d in dados.mes.items() if d.valor > 0 and c not in na_equipe),
            key=lambda c: (len(c), c),
        )
        sem_nome = sorted(
            (c for c in na_equipe if not (vendedores.get(c) and vendedores[c].nome)),
            key=lambda c: (len(c), c),
        )
        partes = []
        if fora:
            partes.append(
                "Ficam fora da tabela os códigos de vendedor com venda no mês e sem meta: "
                f"{lista(fora)}."
            )
        if sem_nome:
            partes.append(f"Sem nome no cadastro de vendedores: {lista(sem_nome)}.")
        nota = " ".join(partes) or None
    return CartaoTabela(
        id="report-equipe",
        tipo="tabela",
        variante="compacta",
        largura=12,
        titulo="Report da equipe",
        subtitulo="Os números de cada vendedor no mês, com farol nas colunas que têm meta",
        nota=nota,
        colunas=colunas,
        linhas=tabela,
    )

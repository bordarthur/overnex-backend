"""Seção Vendas da Visão geral. REGRAS PROVISÓRIAS: confirmar com o negócio (ver TODO.md).

Lê as tabelas de vendas (f_vendas_*) e de metas (f_metas_*) que o cliente tem registradas
em cfg.fonte_carga, e o histórico mensal de mart.resumo_mensal (gravado pela carga). Com
mais de uma tabela de vendas (ex.: PJ Cortes: a atual e a do sistema antigo), as linhas se
somam: os períodos não podem se sobrepor.

Colunas por papel: as do DW padrão (COLUNAS_VENDAS, COLUNAS_METAS), trocadas pelas de
cfg.fonte_carga.colunas_modelo quando a fonte informa (migration 11). Abaixo, as padrão.

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

from collections.abc import Mapping
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
    rotulo_eixo,
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

# papel → coluna no DW padrão (os mesmos de jobs/carga/carga/resumo.py)
COLUNAS_VENDAS: Mapping[str, str] = {
    "data": "en_datemicao",
    "valor": "en_VALTOT",
    # custo total da linha (não o unitário)
    "custo": "en_precus",
    "pedido": "en_numdoc",
    "cliente": "en_codcli",
    "produto": "en_codigo",
    # abertura do CMV da seção Custos
    "abertura": "en_ramo",
}
COLUNAS_METAS: Mapping[str, str] = {"data": "Competencia", "valor": "Valor"}
# papéis somados e papéis de data; os outros são códigos e viram texto (ver de_todas)
NUMERICOS = frozenset({"valor", "custo"})
DATAS = frozenset({"data", "emissao", "vencimento", "pagamento"})

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
class TabelaDoCliente:
    """Tabela de dados do mart e o nome de cada papel nas colunas dela."""

    tabela: str
    colunas: Mapping[str, str]
    # colunas já do tipo date no mart (a carga converte); as outras datas vêm em texto
    datas: frozenset[str] = frozenset()


@dataclass(frozen=True)
class BaseVendas:
    """O que a seção Vendas já calculou e a seção Custos reaproveita."""

    tabelas: list[TabelaDoCliente]
    mes: MesDeVendas
    # histórico do resumo mensal, com o mês de referência vindo da tabela
    mensal: Mensal
    # primeiro mês do histórico lido
    inicio: date
    # feriados do mês de referência
    feriados: set[date]


def data_texto(coluna: str) -> sql.Composable:
    """Data guardada como texto no mart → date (mesmos formatos da janela da carga). Só
    para tabela que ainda não tem a coluna como date (TabelaDoCliente.datas).

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


def tabelas_do_cliente(
    conn: Conexao, cliente_id: UUID, prefixo: str, padrao: Mapping[str, str]
) -> list[TabelaDoCliente]:
    """Tabelas de dados do cliente que começam com `prefixo` (ex.: f_vendas_) e existem no
    mart, com a coluna de cada papel (`padrao` + cfg.fonte_carga.colunas_modelo)."""
    # to_jsonb(f): sem a migration 11 a coluna não existe e vale o padrão
    return [
        TabelaDoCliente(
            r["tabela_destino"], {**padrao, **r["colunas_modelo"]}, frozenset(r["datas"])
        )
        for r in conn.execute(
            """
            select f.tabela_destino,
                   coalesce(to_jsonb(f) -> 'colunas_modelo', '{}'::jsonb) as colunas_modelo,
                   array(select c.column_name::text
                           from information_schema.columns c
                          where c.table_schema = 'mart'
                            and c.table_name = f.tabela_destino
                            and c.data_type = 'date') as datas
              from cfg.fonte_carga f
             where f.cliente_id = %s
               and f.tipo = 'tabela'
               and starts_with(f.tabela_destino, %s)
               and to_regclass('mart.' || quote_ident(f.tabela_destino)) is not null
             order by f.tabela_destino
            """,
            (cliente_id, prefixo),
        )
    ]


def de_todas(
    tabelas: list[TabelaDoCliente], campos: Mapping[str, str], filtro: sql.Composable
) -> sql.Composable:
    """`select <campos> from <tabela> where <cliente e empresa>` de cada tabela, em union all.

    `campos`: apelido → papel. As datas (DATAS) viram date (data_texto, se ainda em texto),
    valor e custo ficam como estão e os códigos viram texto (o tipo pode mudar de uma tabela
    para outra).
    """

    def expressao(t: TabelaDoCliente, papel: str) -> sql.Composable:
        coluna = t.colunas[papel]
        if papel in DATAS:
            return sql.Identifier(coluna) if coluna in t.datas else data_texto(coluna)
        if papel in NUMERICOS:
            return sql.Identifier(coluna)
        return sql.SQL("{}::text").format(sql.Identifier(coluna))

    return sql.SQL(" union all ").join(
        sql.SQL(
            "select {campos} from {tabela} where _cliente_id = %(cliente)s and {filtro}"
        ).format(
            campos=sql.SQL(", ").join(
                sql.SQL("{} as {}").format(expressao(t, papel), sql.Identifier(apelido))
                for apelido, papel in campos.items()
            ),
            tabela=sql.Identifier("mart", t.tabela),
            filtro=filtro,
        )
        for t in tabelas
    )


def filtro_empresa(ctx: ContextoConsolidado, coluna: str = "_empresa_id") -> sql.Composable:
    c = sql.Identifier(coluna)
    if ctx.empresa_id is not None:
        return sql.SQL("{c} = any(%(empresas)s)").format(c=c)
    return sql.SQL("({c} is null or {c} = any(%(empresas)s))").format(c=c)


def _vendas_do_mes(
    conn: Conexao, ctx: ContextoConsolidado, tabelas: list[TabelaDoCliente]
) -> MesDeVendas | None:
    campos = {
        "dia": "data",
        "valor": "valor",
        "custo": "custo",
        "cliente": "cliente",
        "pedido": "pedido",
        "produto": "produto",
    }
    linha = conn.execute(
        sql.SQL(
            """
            with v as ({vendas}),
            ref as (select date_trunc('month', max(dia))::date as mes from v)
            select ref.mes,
                   max(v.dia) as ultimo_dia,
                   coalesce(sum(v.valor), 0) as faturado,
                   coalesce(sum(v.custo), 0) as custo,
                   count(distinct v.cliente) as clientes,
                   count(distinct v.pedido) as pedidos,
                   count(distinct (v.cliente, v.produto)) as clientes_produtos
              from v
              join ref on v.dia >= ref.mes and v.dia < ref.mes + interval '1 month'
             group by ref.mes
            """
        ).format(vendas=de_todas(tabelas, campos, filtro_empresa(ctx))),
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
    tabelas = tabelas_do_cliente(conn, ctx.cliente_id, "f_metas_", COLUNAS_METAS)
    if not tabelas:
        return {}
    campos = {"competencia": "data", "valor": "valor"}
    return {
        r["mes"]: r["meta"]
        for r in conn.execute(
            sql.SQL(
                """
                with m as ({metas})
                select date_trunc('month', competencia)::date as mes, sum(valor) as meta
                  from m
                 where competencia >= %(de)s
                   and competencia < %(ate)s::date + interval '1 month'
                 group by 1
                """
            ).format(metas=de_todas(tabelas, campos, filtro_empresa(ctx))),
            {"cliente": ctx.cliente_id, "empresas": list(ctx.empresas), "de": de, "ate": ate},
        )
        if r["meta"]
    }


def _resumo_mensal(
    conn: Conexao, ctx: ContextoConsolidado, tabelas: list[TabelaDoCliente], de: date, ate: date
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
             where cliente_id = %(cliente)s and tabela = any(%(tabelas)s) and {filtro}
               and mes between %(de)s and %(ate)s
             group by mes, medida
            """
        ).format(filtro=filtro_empresa(ctx, "empresa_id")),
        {
            "cliente": ctx.cliente_id,
            "empresas": list(ctx.empresas),
            "tabelas": [t.tabela for t in tabelas],
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
            rotulos=[rotulo_eixo(m) for m in tendencia],
            real=[
                projecao if m == ref and projecao is not None else faturado[m] for m in tendencia
            ],
            referencia=[metas[m] for m in tendencia],
            unidade="brl",
            casas=2,
        ),
    )


def mes_menos(mes: date, meses: int) -> date:
    indice = mes.year * 12 + mes.month - 1 - meses
    return date(indice // 12, indice % 12 + 1, 1)


def secao_vendas(ctx: ContextoConsolidado) -> tuple[SecaoDoMes, BaseVendas] | None:
    """Seção Vendas e a base para a de Custos; None se o cliente não tem tabela de vendas
    ou não há venda no recorte."""
    conn = ctx.sessao.conn
    tabelas = tabelas_do_cliente(conn, ctx.cliente_id, "f_vendas_", COLUNAS_VENDAS)
    if not tabelas:
        return None
    v = _vendas_do_mes(conn, ctx, tabelas)
    if v is None:
        return None

    # histórico: tendência, acumulado do ano e média dos 6 meses anteriores
    inicio = min(date(v.mes.year, 1, 1), mes_menos(v.mes, MESES_TENDENCIA))
    metas = _metas(conn, ctx, inicio, v.mes)
    mensal = _resumo_mensal(conn, ctx, tabelas, inicio, v.mes)
    # o mês de referência vem das tabelas, que são dos mesmos arquivos do resumo
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
    base = BaseVendas(tabelas=tabelas, mes=v, mensal=mensal, inicio=inicio, feriados=feriados_mes)
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

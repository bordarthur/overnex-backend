"""FIN-03 — Receber e pagar. REGRAS PROVISÓRIAS: confirmar com o negócio (ver TODO.md).

Lê os títulos a receber (f_receitas_*) e a pagar (f_despesas_*) com as regras da seção
Financeiro da Visão geral (app/telas/visao_geral/financeiro.py): título em aberto numa data
= emitido até ela e sem pagamento até ela (pagamento antes de 1901 = sem pagamento), valor
inteiro do título (baixa parcial fora), modelo D+2 (vencido = vencimento + 2 dias úteis até
a data; antes disso, em compensação). Colunas por papel (padrão: as da PJ Cortes, trocadas
por cfg.fonte_carga.colunas_modelo): os do Financeiro e mais cliente e vendedor no a
receber, fornecedor no a pagar. Nomes e limite de crédito vêm de d_cliente_*,
d_fornecedor_* e d_vendedor_*.

- posição ("hoje"): a data da última carga; com `mes` fechado, o último dia do mês;
- filtro vendedor (só se os títulos a receber têm vendedor): muda todo o lado a receber
  (KPIs, janelas, carteira, cobrança, inadimplência e prazo de recebimento); o a pagar não
  muda, e as janelas mostram só o a receber, sem a sobra;
- filtro atraso: só a lista "Quem cobrar" (atraso do título vencido mais antigo do
  cliente);
- KPIs: a receber e a pagar nos próximos 30 dias (vencimento de hoje até hoje + 30, como na
  Visão geral) e a inadimplência (vencido ÷ carteira) contra a média do fim dos 12 meses
  fechados (farol.inadimplencia, em p.p.). O a pagar sai em % da média paga por mês nos 6
  meses fechados (farol.pagar_lancado). Sem saldo bancário, ficam fora "Saldo em caixa
  hoje", "Saldo em 30 dias" e o "Saldo no fim" das janelas;
- janelas de 7, 15 e 30 dias: o que vence a receber e a pagar e a sobra; alerta "Dado
  incompleto" com o a pagar de 30 dias fora do saudável de farol.pagar_lancado;
- carteira: idade em 5 faixas (a vencer inclui o que está em compensação), os 6 maiores
  saldos (etiqueta "vencido" com algum título vencido), prazo médio do último mês fechado
  contra a média de 12 meses (farol.prazo_recebimento e farol.prazo_pagamento, em dias).
  Sem PDD: falta a regra;
- quem cobrar: os 8 clientes com mais vencido. Chance de receber: a parte do valor dos
  títulos do cliente com vencimento entre 12 meses e 30 dias atrás que foi paga até 30 dias
  depois do vencimento (Alta a partir de 90%, Média a partir de 70%, Baixa abaixo; sem
  título nesse período, "sem histórico"). "Parou de comprar": nenhum título emitido nos
  últimos 60 dias. Fundo do atraso: até 30 dias saudável, até 60 atenção, acima crítico;
- a pagar por semana: as 4 semanas a partir de hoje, só o lançado, contra a média paga
  por semana (média por mês × 12 ÷ 52): acima dela, atenção; acima de 130%, crítico;
- inadimplência por mês de vencimento (6 meses fechados): % do valor ainda em aberto 15,
  30, 60 e 90 dias depois do vencimento, só quando o fim do mês mais esse prazo já passou
  ("ainda não"). Fundo pela média da coluna: acima, atenção; 15% acima, crítico;
- idade dos vencidos e inadimplência no fim de cada um dos 12 meses fechados (D+2 no fim do
  mês), e os prazos médios de recebimento e de pagamento nesses meses;
- limite de crédito: só se d_cliente_* tem limite cadastrado (> 0) para algum cliente;
  clientes com o total em aberto (todos os vendedores) acima do limite.

Fora, por falta de fonte ou de regra: saldo bancário, PDD e ciclo de caixa (precisa do
prazo de estoque).
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from psycopg import sql

from app.core.db import Conexao, em_uma_ida
from app.farol.parametro import Farol, deslocado, farol
from app.seguranca.acesso import ContextoConsolidado
from app.telas.calendario import feriados, ultimo_dia_do_mes
from app.telas.contrato import Status
from app.telas.moldura import UltimaCarga
from app.telas.paineis.base import (
    Consulta,
    Conteudo,
    Opcoes,
    Tipo,
    Vendedor,
    em_fileiras,
    escolher,
    parametros,
    selecao,
    slug,
    tem_papel,
)
from app.telas.paineis.contrato import (
    AlertaJanelas,
    BarraHorizontal,
    Cartao,
    CartaoBarrasHorizontais,
    CartaoDoisLados,
    CartaoGraficos,
    CartaoJanelas,
    CartaoTabela,
    Celula,
    Chamada,
    ColunaTabela,
    Escala,
    EstiloLinha,
    FaixaIdade,
    FiltroPainel,
    GraficoCartao,
    GraficoEmpilhado,
    GraficoLinhas,
    ItemMaior,
    Janela,
    LadoConta,
    LinhaJanela,
    LinhaTabela,
    Marcador,
    OpcaoFiltro,
    PillComRotulo,
    ReferenciaLinha,
    SerieEmpilhada,
    SerieLinha,
)
from app.telas.paineis.formato import CEM, brl, pct, q, qtd
from app.telas.visao_geral.contra_referencia import MESES_CURTOS, Mensal, rotulo_eixo
from app.telas.visao_geral.contrato import Kpi, Trecho, Valor
from app.telas.visao_geral.financeiro import (
    COLUNAS_PAGAR,
    COLUNAS_RECEBER,
    media_paga,
    ultimo_vencimento_vencido,
)
from app.telas.visao_geral.router import recorte
from app.telas.visao_geral.texto import moeda, numero
from app.telas.visao_geral.vendas import (
    TabelaDoCliente,
    filtro_empresa,
    mes_menos,
    tabelas_do_cliente,
)

# papel → coluna (as da PJ Cortes)
COLUNAS_RECEBER_PAINEL: Mapping[str, str] = {
    **COLUNAS_RECEBER,
    "cliente": "en_codigocliente",
    "vendedor": "en_codigovendedor",
}
COLUNAS_PAGAR_PAINEL: Mapping[str, str] = {**COLUNAS_PAGAR, "fornecedor": "en_codigofornecedor"}
COLUNAS_CLIENTE: Mapping[str, str] = {
    "codigo": "en_codigocliente",
    "nome": "en_fantasia",
    "razao": "en_razaosocial",
    "limite": "en_limitecredito",
}
COLUNAS_FORNECEDOR: Mapping[str, str] = {
    "codigo": "en_codigofornecedor",
    "nome": "en_fantasia",
    "razao": "en_razaosocial",
}
COLUNAS_VENDEDOR: Mapping[str, str] = {"codigo": "en_codigovendedor", "nome": "en_nome"}

MESES_SERIE = 12
MESES_SAFRA = 6
DIAS_SAFRA = (15, 30, 60, 90)
JANELAS = (7, 15, 30)
SEMANAS = 4
QUEM_COBRAR = 8
MAIORES = 6
LIMITES = 10
# chance de receber: pago até este número de dias depois do vencimento
DIAS_CHANCE = 30
CHANCE_ALTA = Decimal(90)
CHANCE_MEDIA = Decimal(70)
DIAS_SEM_COMPRA = 60
# safra: acima da média da coluna, atenção; acima de 115% dela, crítico
TOLERANCIA_SAFRA = Decimal("1.15")
# a pagar na semana contra a média paga por semana
SEMANA_CRITICA = Decimal("1.3")
FAIXAS_SERIE = ("1 a 15 dias", "16 a 30", "31 a 60", "61 a 90", "Mais de 90")
# atraso do filtro: id → (de, até) em dias
ATRASOS: Mapping[str, tuple[int, int]] = {
    "ate-30": (1, 30),
    "31-90": (31, 90),
    "mais-90": (91, 1_000_000),
}
TODOS_OS_ATRASOS = (1, 1_000_000)
ATRASO = Opcoes(
    filtro=FiltroPainel(
        id="atraso",
        rotulo="Atraso",
        opcoes=[
            OpcaoFiltro(id="todos", rotulo="Todos"),
            OpcaoFiltro(id="ate-30", rotulo="Até 30 dias"),
            OpcaoFiltro(id="31-90", rotulo="31 a 90 dias"),
            OpcaoFiltro(id="mais-90", rotulo="Mais de 90 dias"),
        ],
        padrao="todos",
    ),
    valores={k: (k,) for k in ATRASOS},
)
ZERO = Decimal(0)


# ---------- fontes ----------


@dataclass(frozen=True)
class Fontes:
    receber: list[TabelaDoCliente]
    pagar: list[TabelaDoCliente]
    clientes: list[TabelaDoCliente]
    fornecedores: list[TabelaDoCliente]
    vendedores: list[TabelaDoCliente]


def fontes(conn: Conexao, ctx: ContextoConsolidado) -> Fontes:
    c = ctx.cliente_id
    return Fontes(
        receber=tabelas_do_cliente(conn, c, "f_receitas_", COLUNAS_RECEBER_PAINEL),
        pagar=tabelas_do_cliente(conn, c, "f_despesas_", COLUNAS_PAGAR_PAINEL),
        clientes=tabelas_do_cliente(conn, c, "d_cliente_", COLUNAS_CLIENTE),
        fornecedores=tabelas_do_cliente(conn, c, "d_fornecedor_", COLUNAS_FORNECEDOR),
        vendedores=tabelas_do_cliente(conn, c, "d_vendedor_", COLUNAS_VENDEDOR),
    )


# ---------- posição ----------


@dataclass(frozen=True)
class Posicao:
    hoje: date
    # vencimento mais recente já vencido em `hoje` (D+2)
    limite: date
    # fim de cada um dos 12 meses fechados antes do mês de `hoje`, e o limite do D+2 nele
    fins: tuple[date, ...]
    limites: tuple[date, ...]

    @property
    def aberto(self) -> date:
        return self.hoje.replace(day=1)

    @property
    def meses(self) -> list[date]:
        return [d.replace(day=1) for d in self.fins]


def posicao(conn: Conexao, ctx: ContextoConsolidado, hoje: date) -> Posicao:
    aberto = hoje.replace(day=1)
    fins = tuple(ultimo_dia_do_mes(mes_menos(aberto, n)) for n in range(MESES_SERIE, 0, -1))
    # um mês antes do primeiro fim: o D+2 de um vencimento volta alguns dias
    fer = feriados(conn, ctx.cliente_id, mes_menos(aberto, MESES_SERIE + 1), hoje)
    return Posicao(
        hoje=hoje,
        limite=ultimo_vencimento_vencido(hoje, fer),
        fins=fins,
        limites=tuple(ultimo_vencimento_vencido(d, fer) for d in fins),
    )


# ---------- leitura ----------


def _titulos(
    ctx: ContextoConsolidado, tabelas: list[TabelaDoCliente], papel_pessoa: str
) -> sql.Composable:
    """Títulos limpos (vencimento vazio = emissão; pagamento antes de 1901 = sem
    pagamento), com `do_vendedor` = entra no filtro de vendedor."""
    campos: Mapping[str, tuple[str, Tipo]] = {
        "emissao": ("emissao", "data"),
        "vencimento": ("vencimento", "data"),
        "pagamento": ("pagamento", "data"),
        "valor": ("valor", "numero"),
        "pessoa": (papel_pessoa, "texto"),
        "vendedor": ("vendedor", "texto"),
    }
    return sql.SQL(
        """
        select emissao,
               coalesce(vencimento, emissao) as vencimento,
               -- antes de 1901: data vazia gravada como 1899/1900
               case when pagamento >= date '1901-01-01' then pagamento end as pagamento,
               valor, pessoa, vendedor,
               (%(vendedores)s::text[] is null or vendedor = any(%(vendedores)s)) as do_vendedor
          from ({linhas}) x
         where emissao is not null and valor > 0
        """
    ).format(linhas=selecao(tabelas, campos, filtro_empresa(ctx)))


# nomes das posições do array `v` de cada parte da consulta_lado
CARTEIRA = (
    "a_vencer",
    "compensando",
    "ate_30",
    "ate_60",
    "ate_90",
    "mais_90",
    *(f"prox_{d}" for d in JANELAS),
    "pessoas_atraso",
    "pessoas",
    *(f"semana_{i}" for i in range(1, SEMANAS + 1)),
)
SERIE = ("ate_15", "ate_30", "ate_60", "ate_90", "mais_90", "aberto")
PAGOS = ("valor", "dias_valor")
SAFRA = ("total", *(f"d{d}" for d in DIAS_SAFRA))


def consulta_lado(
    ctx: ContextoConsolidado,
    tabelas: list[TabelaDoCliente],
    papel_pessoa: str,
    pos: Posicao,
    vendedores: Sequence[str] | None,
) -> Consulta:
    """Uma leitura dos títulos (CTE materializada) e quatro partes, cada uma com os
    valores num array (nomes em CARTEIRA, SERIE, PAGOS e SAFRA):
    - carteira: em aberto hoje por faixa, a vencer nas janelas e nas semanas, pessoas;
    - serie: no fim de cada mês da série, vencido por faixa de atraso e em aberto;
    - pagos: por mês de pagamento, valor e dias até o pagamento × valor (prazo médio;
      pagamento antes da emissão conta 0 dias);
    - safra: por mês de vencimento, o valor e o que ainda estava em aberto 15, 30, 60 e 90
      dias depois do vencimento."""
    janelas = sql.SQL(", ").join(
        sql.SQL("coalesce(sum(valor) filter (where atraso between {} and 0), 0)").format(
            sql.Literal(-d)
        )
        for d in JANELAS
    )
    semanas = sql.SQL(", ").join(
        sql.SQL("coalesce(sum(valor) filter (where atraso between {} and {}), 0)").format(
            sql.Literal(-(7 * i + 6)), sql.Literal(-7 * i)
        )
        for i in range(SEMANAS)
    )
    safra = sql.SQL(", ").join(
        sql.SQL(
            "coalesce(sum(valor) filter ("
            "where pagamento is null or pagamento > vencimento + {}), 0)"
        ).format(sql.Literal(d))
        for d in DIAS_SAFRA
    )
    return sql.SQL(
        """
        with t as materialized (select * from ({titulos}) x where do_vendedor),
        a as (
            select %(hoje)s::date - vencimento as atraso,
                   vencimento <= %(limite)s as vencido,
                   valor, pessoa
              from t
             where emissao <= %(hoje)s and (pagamento is null or pagamento > %(hoje)s)
        ),
        fim as (select * from unnest(%(fins)s::date[], %(limites)s::date[]) as f (dia, limite))
        select 'carteira' as parte, null::date as dia, array[
                   coalesce(sum(valor) filter (where atraso <= 0), 0),
                   coalesce(sum(valor) filter (where atraso > 0 and not vencido), 0),
                   coalesce(sum(valor) filter (where vencido and atraso <= 30), 0),
                   coalesce(sum(valor) filter (where vencido and atraso between 31 and 60), 0),
                   coalesce(sum(valor) filter (where vencido and atraso between 61 and 90), 0),
                   coalesce(sum(valor) filter (where vencido and atraso > 90), 0),
                   {janelas},
                   count(distinct pessoa) filter (where vencido),
                   count(distinct pessoa),
                   {semanas}
               ]::numeric[] as v
          from a
        union all
        select 'serie', fim.dia, array[
                   coalesce(sum(t.valor) filter (
                       where t.vencimento <= fim.limite and fim.dia - t.vencimento <= 15), 0),
                   coalesce(sum(t.valor) filter (
                       where t.vencimento <= fim.limite
                         and fim.dia - t.vencimento between 16 and 30), 0),
                   coalesce(sum(t.valor) filter (
                       where t.vencimento <= fim.limite
                         and fim.dia - t.vencimento between 31 and 60), 0),
                   coalesce(sum(t.valor) filter (
                       where t.vencimento <= fim.limite
                         and fim.dia - t.vencimento between 61 and 90), 0),
                   coalesce(sum(t.valor) filter (
                       where t.vencimento <= fim.limite and fim.dia - t.vencimento > 90), 0),
                   coalesce(sum(t.valor), 0)
               ]::numeric[]
          from fim
          left join t
            on t.emissao <= fim.dia and (t.pagamento is null or t.pagamento > fim.dia)
         group by fim.dia
        union all
        select 'pagos', date_trunc('month', pagamento)::date,
               array[sum(valor), sum(greatest(pagamento - emissao, 0) * valor)]::numeric[]
          from t
         where pagamento >= %(de)s and pagamento <= %(hoje)s
         group by 2
        union all
        select 'safra', date_trunc('month', vencimento)::date,
               array[sum(valor), {safra}]::numeric[]
          from t
         where vencimento >= %(safra_de)s and vencimento < %(safra_ate)s
         group by 2
        """
    ).format(
        titulos=_titulos(ctx, tabelas, papel_pessoa),
        janelas=janelas,
        semanas=semanas,
        safra=safra,
    ), parametros(
        ctx,
        vendedores=None if vendedores is None else list(vendedores),
        hoje=pos.hoje,
        limite=pos.limite,
        fins=list(pos.fins),
        limites=list(pos.limites),
        de=pos.meses[0],
        safra_de=mes_menos(pos.aberto, MESES_SAFRA),
        safra_ate=pos.aberto,
    )


@dataclass(frozen=True)
class Carteira:
    """Títulos em aberto hoje."""

    # nomes em CARTEIRA
    valores: Mapping[str, Decimal]

    @property
    def a_vencer(self) -> Decimal:
        """Vencimento a partir de hoje, mais o que está em compensação (D+2)."""
        return self.valores["a_vencer"] + self.valores["compensando"]

    @property
    def faixas_vencidas(self) -> list[Decimal]:
        """1 a 30, 31 a 60, 61 a 90 e mais de 90 dias."""
        return [self.valores[k] for k in ("ate_30", "ate_60", "ate_90", "mais_90")]

    @property
    def vencido(self) -> Decimal:
        return sum(self.faixas_vencidas, ZERO)

    @property
    def total(self) -> Decimal:
        return self.a_vencer + self.vencido

    def proximos(self, dias: int) -> Decimal:
        return self.valores[f"prox_{dias}"]

    @property
    def semanas(self) -> list[Decimal]:
        return [self.valores[f"semana_{i}"] for i in range(1, SEMANAS + 1)]


@dataclass(frozen=True)
class PontoMes:
    # último dia do mês
    dia: date
    # vencido por faixa (FAIXAS_SERIE)
    faixas: tuple[Decimal, ...]
    aberto: Decimal

    @property
    def vencido(self) -> Decimal:
        return sum(self.faixas, ZERO)


@dataclass(frozen=True)
class Safra:
    mes: date
    total: Decimal
    # dias depois do vencimento → valor ainda em aberto
    em_aberto: Mapping[int, Decimal]


@dataclass(frozen=True)
class Lado:
    carteira: Carteira
    serie: list[PontoMes]
    # por mês de pagamento: valor e dias_valor
    pagos: Mensal
    safras: list[Safra]

    def prazo(self, mes: date) -> Decimal | None:
        p = self.pagos.get(mes)
        return p["dias_valor"] / p["valor"] if p and p["valor"] else None


def lado_das_linhas(linhas: Iterable[Mapping[str, Any]]) -> Lado:
    carteira: dict[str, Decimal] = dict.fromkeys(CARTEIRA, ZERO)
    serie: list[PontoMes] = []
    pagos: Mensal = {}
    safras: list[Safra] = []
    for r in linhas:
        v = [x if x is not None else ZERO for x in r["v"]]
        if r["parte"] == "carteira":
            carteira = dict(zip(CARTEIRA, v, strict=True))
        elif r["parte"] == "serie":
            serie.append(PontoMes(dia=r["dia"], faixas=tuple(v[:5]), aberto=v[5]))
        elif r["parte"] == "pagos":
            pagos[r["dia"]] = dict(zip(PAGOS, v, strict=True))
        else:
            safras.append(
                Safra(mes=r["dia"], total=v[0], em_aberto=dict(zip(DIAS_SAFRA, v[1:], strict=True)))
            )
    return Lado(
        carteira=Carteira(carteira),
        serie=sorted(serie, key=lambda p: p.dia),
        pagos=pagos,
        safras=sorted(safras, key=lambda s: s.mes),
    )


def _cadastro(
    ctx: ContextoConsolidado, tabelas: list[TabelaDoCliente], com_limite: bool
) -> sql.Composable:
    """Uma linha por código (codigo, nome, limite); sem cadastro, nenhuma."""
    if not tabelas:
        return sql.SQL("select null::text as codigo, null::text as nome, null::numeric as limite")
    campos: dict[str, tuple[str, Tipo]] = {
        "codigo": ("codigo", "texto"),
        "nome": ("nome", "texto"),
        "razao": ("razao", "texto"),
    }
    if com_limite:
        campos["limite"] = ("limite", "numero")
    limite = sql.SQL("limite") if com_limite else sql.SQL("null::numeric")
    return sql.SQL(
        """
        select distinct on (codigo) codigo, coalesce(nome, razao) as nome, {limite} as limite
          from ({linhas}) x
         where codigo is not null
         order by codigo, coalesce(nome, razao) is null
        """
    ).format(limite=limite, linhas=selecao(tabelas, campos, filtro_empresa(ctx)))


def consulta_pessoas(
    ctx: ContextoConsolidado,
    tabelas: list[TabelaDoCliente],
    papel_pessoa: str,
    cadastro: list[TabelaDoCliente],
    pos: Posicao,
    vendedores: Sequence[str] | None,
    atraso: tuple[int, int],
    com_limite: bool,
) -> Consulta:
    """Clientes (ou fornecedores) que algum cartão mostra: os QUEM_COBRAR com mais vencido
    na faixa de `atraso`, os MAIORES saldos e os LIMITES mais acima do limite de crédito.

    `aberto`, `vencido` e `atraso` respeitam o filtro de vendedor; `aberto_total` e
    `vencido_total`, não (o limite é do cliente)."""
    return sql.SQL(
        """
        with t as materialized (select * from ({titulos}) x where pessoa is not null),
        a as (
            select pessoa, vendedor, valor, vencimento, do_vendedor,
                   vencimento <= %(limite)s as vencido
              from t
             where emissao <= %(hoje)s and (pagamento is null or pagamento > %(hoje)s)
        ),
        c as (
            select pessoa,
                   coalesce(sum(valor) filter (where do_vendedor), 0) as aberto,
                   coalesce(sum(valor) filter (where do_vendedor and vencido), 0) as vencido,
                   max(%(hoje)s::date - vencimento) filter (where do_vendedor and vencido)
                       as atraso,
                   sum(valor) as aberto_total,
                   bool_or(vencido) as vencido_total
              from a
             group by pessoa
        ),
        v as (
            select distinct on (pessoa) pessoa, vendedor
              from (select pessoa, vendedor, sum(valor) as s
                      from a
                     where vencido and do_vendedor and vendedor is not null
                     group by 1, 2) x
             order by pessoa, s desc, vendedor
        ),
        h as (
            select pessoa, sum(valor) as historico,
                   coalesce(sum(valor) filter (
                       where pagamento <= vencimento + %(dias_chance)s), 0) as historico_pago
              from t
             where do_vendedor
               and vencimento > %(hoje)s::date - 365
               and vencimento <= %(hoje)s::date - %(dias_chance)s
             group by pessoa
        ),
        e as (select pessoa, max(emissao) as ultima from t where emissao <= %(hoje)s group by 1),
        n as ({cadastro}),
        r as (
            select c.*, v.vendedor, h.historico, h.historico_pago, e.ultima, n.nome, n.limite,
                   case when c.vencido > 0 and c.atraso between %(atraso_de)s and %(atraso_ate)s
                        then row_number() over (
                            partition by c.vencido > 0
                                     and c.atraso between %(atraso_de)s and %(atraso_ate)s
                            order by c.vencido desc, c.pessoa) end as ordem_cobrar,
                   case when c.aberto > 0
                        then row_number() over (
                            partition by c.aberto > 0 order by c.aberto desc, c.pessoa)
                   end as ordem_aberto,
                   case when n.limite > 0 and c.aberto_total > n.limite and c.aberto > 0
                        then row_number() over (
                            partition by n.limite > 0 and c.aberto_total > n.limite
                                     and c.aberto > 0
                            order by c.aberto_total - n.limite desc, c.pessoa) end
                       as ordem_limite,
                   (select coalesce(bool_or(limite > 0), false) from n) as tem_limite
              from c
              left join v using (pessoa)
              left join h using (pessoa)
              left join e using (pessoa)
              left join n on n.codigo = c.pessoa
        )
        select * from r
         where ordem_cobrar <= %(cobrar)s or ordem_aberto <= %(maiores)s
            or ordem_limite <= %(limites)s
        """
    ).format(
        titulos=_titulos(ctx, tabelas, papel_pessoa),
        cadastro=_cadastro(ctx, cadastro, com_limite),
    ), parametros(
        ctx,
        vendedores=None if vendedores is None else list(vendedores),
        hoje=pos.hoje,
        limite=pos.limite,
        dias_chance=DIAS_CHANCE,
        atraso_de=atraso[0],
        atraso_ate=atraso[1],
        cobrar=QUEM_COBRAR,
        maiores=MAIORES,
        limites=LIMITES,
    )


@dataclass(frozen=True)
class Pessoa:
    codigo: str
    nome: str | None
    aberto: Decimal
    vencido: Decimal
    # dias do título vencido mais antigo
    atraso: int | None
    aberto_total: Decimal
    vencido_total: bool
    # vendedor com mais vencido do cliente
    vendedor: str | None
    # valor dos títulos do período do histórico e o que deles foi pago no prazo da chance
    historico: Decimal | None
    historico_pago: Decimal | None
    ultima: date | None
    limite: Decimal | None
    ordem_cobrar: int | None
    ordem_aberto: int | None
    ordem_limite: int | None

    def exibicao(self, tipo: str) -> str:
        if self.nome:
            limpo = " ".join(self.nome.split())
            return limpo.title() if limpo.isupper() else limpo
        return f"{tipo} {self.codigo}"


def pessoas_das_linhas(linhas: Iterable[Mapping[str, Any]]) -> tuple[list[Pessoa], bool]:
    """As pessoas e se o cadastro tem limite de crédito para alguém."""
    pessoas: list[Pessoa] = []
    tem_limite = False
    for r in linhas:
        tem_limite = tem_limite or bool(r["tem_limite"])
        pessoas.append(
            Pessoa(
                codigo=r["pessoa"],
                nome=r["nome"],
                aberto=r["aberto"],
                vencido=r["vencido"],
                atraso=r["atraso"],
                aberto_total=r["aberto_total"],
                vencido_total=bool(r["vencido_total"]),
                vendedor=r["vendedor"],
                historico=r["historico"],
                historico_pago=r["historico_pago"],
                ultima=r["ultima"],
                limite=r["limite"],
                ordem_cobrar=r["ordem_cobrar"],
                ordem_aberto=r["ordem_aberto"],
                ordem_limite=r["ordem_limite"],
            )
        )
    return pessoas, tem_limite


def _consulta_vendedores(ctx: ContextoConsolidado, f: Fontes) -> dict[str, Consulta]:
    """Os códigos de vendedor dos títulos a receber e o cadastro dos vendedores."""
    consultas: dict[str, Consulta] = {
        "codigos": (
            sql.SQL("select distinct vendedor from ({linhas}) x where vendedor is not null").format(
                linhas=selecao(f.receber, {"vendedor": ("vendedor", "texto")}, filtro_empresa(ctx))
            ),
            parametros(ctx),
        )
    }
    if f.vendedores:
        campos: Mapping[str, tuple[str, Tipo]] = {
            "codigo": ("codigo", "texto"),
            "nome": ("nome", "texto"),
        }
        consultas["cadastro"] = (
            sql.SQL(
                """
                select distinct on (codigo) codigo, nome
                  from ({linhas}) x
                 where codigo is not null
                 order by codigo, nome is null
                """
            ).format(linhas=selecao(f.vendedores, campos, filtro_empresa(ctx))),
            parametros(ctx),
        )
    return consultas


def opcoes_vendedor(codigos: Iterable[str], nomes: Mapping[str, str | None]) -> Opcoes | None:
    """Filtro de vendedor: uma opção por código dos títulos, com o nome do cadastro."""
    itens: list[tuple[str, str, str]] = []
    usados: set[str] = {"todos"}
    for codigo in sorted(set(codigos)):
        exibicao = Vendedor(codigo, nomes.get(codigo), None, None).exibicao
        id_ = slug(exibicao) or slug(codigo)
        if id_ in usados:
            id_ = f"{id_}-{slug(codigo)}"
        usados.add(id_)
        itens.append((exibicao, id_, codigo))
    if not itens:
        return None
    itens.sort()
    return Opcoes(
        filtro=FiltroPainel(
            id="vendedor",
            rotulo="Vendedor",
            opcoes=[
                OpcaoFiltro(id="todos", rotulo="Todos"),
                *(OpcaoFiltro(id=i, rotulo=r) for r, i, _ in itens),
            ],
            padrao="todos",
        ),
        valores={i: (c,) for _, i, c in itens},
    )


# ---------- montagem ----------


@dataclass
class Leitura:
    receber: Lado | None = None
    pagar: Lado | None = None
    clientes: list[Pessoa] = field(default_factory=list)
    fornecedores: list[Pessoa] = field(default_factory=list)
    tem_limite: bool = False


def ler(
    conn: Conexao,
    ctx: ContextoConsolidado,
    f: Fontes,
    pos: Posicao,
    vendedores: Sequence[str] | None,
    atraso: tuple[int, int],
) -> Leitura:
    """Os dois lados e as pessoas, numa ida ao banco."""
    com_limite = any(
        "limite" in t.colunas and t.colunas["limite"] in t.existentes for t in f.clientes
    )
    consultas: dict[str, Consulta] = {}
    if f.receber:
        consultas["receber"] = consulta_lado(ctx, f.receber, "cliente", pos, vendedores)
        consultas["clientes"] = consulta_pessoas(
            ctx, f.receber, "cliente", f.clientes, pos, vendedores, atraso, com_limite
        )
    if f.pagar:
        consultas["pagar"] = consulta_lado(ctx, f.pagar, "fornecedor", pos, None)
        consultas["fornecedores"] = consulta_pessoas(
            ctx, f.pagar, "fornecedor", f.fornecedores, pos, None, TODOS_OS_ATRASOS, False
        )
    linhas = em_uma_ida(conn, consultas)
    leitura = Leitura()
    if f.receber:
        leitura.receber = lado_das_linhas(linhas["receber"])
        leitura.clientes, leitura.tem_limite = pessoas_das_linhas(linhas["clientes"])
        leitura.tem_limite = leitura.tem_limite and com_limite
    if f.pagar:
        leitura.pagar = lado_das_linhas(linhas["pagar"])
        leitura.fornecedores, _ = pessoas_das_linhas(linhas["fornecedores"])
    return leitura


@dataclass(frozen=True)
class Farois:
    inadimplencia: Farol
    pagar_lancado: Farol
    prazo_recebimento: Farol
    prazo_pagamento: Farol


def farois(conn: Conexao, ctx: ContextoConsolidado, hoje: date) -> Farois:
    return Farois(
        inadimplencia=farol(conn, ctx.cliente_id, "inadimplencia", hoje),
        pagar_lancado=farol(conn, ctx.cliente_id, "pagar_lancado", hoje),
        prazo_recebimento=farol(conn, ctx.cliente_id, "prazo_recebimento", hoje),
        prazo_pagamento=farol(conn, ctx.cliente_id, "prazo_pagamento", hoje),
    )


def montar(
    ctx: ContextoConsolidado,
    escolhido: date | None,
    carga: UltimaCarga,
    pedidos: Mapping[str, str | None],
    a_decidir: bool = False,
) -> Conteudo:
    conn = ctx.sessao.conn
    f = fontes(conn, ctx)
    if not f.receber and not f.pagar:
        for valor in pedidos.values():
            escolher(None, valor)
        return Conteudo()

    vendedor_o: Opcoes | None = None
    nomes_vendedores: dict[str, str | None] = {}
    if f.receber and tem_papel(f.receber, "vendedor"):
        inicio = em_uma_ida(conn, _consulta_vendedores(ctx, f))
        nomes_vendedores = {r["codigo"]: r["nome"] for r in inicio.get("cadastro", [])}
        vendedor_o = opcoes_vendedor((r["vendedor"] for r in inicio["codigos"]), nomes_vendedores)
    atraso_o = ATRASO if f.receber else None
    filtros = [o.filtro for o in (vendedor_o, atraso_o) if o is not None]
    vendedores = escolher(vendedor_o, pedidos.get("vendedor"))
    faixa = escolher(atraso_o, pedidos.get("atraso"))
    atraso = ATRASOS[faixa[0]] if faixa else TODOS_OS_ATRASOS

    hoje = recorte(escolhido, carga.data_snapshot).posicao
    pos = posicao(conn, ctx, hoje)
    leitura = ler(conn, ctx, f, pos, vendedores, atraso)
    fs = farois(conn, ctx, hoje)
    calc = Calculo(pos=pos, leitura=leitura, farois=fs, por_vendedor=vendedores is not None)

    def nome_vendedor(codigo: str | None) -> str | None:
        if codigo is None:
            return None
        return Vendedor(codigo, nomes_vendedores.get(codigo), None, None).primeiro_nome

    cartoes: list[Cartao | None] = [
        cartao_janelas(calc),
        cartao_dois_lados(calc),
        cartao_quem_cobrar(calc, nome_vendedor if vendedor_o is not None else None),
        cartao_pagar_semana(calc),
        cartao_safra(calc),
        cartao_inadimplencia_12_meses(calc),
        cartao_idade_vencidos(calc),
        cartao_prazos(calc),
        cartao_limite(calc),
    ]
    return Conteudo(
        filtros=filtros,
        kpis=kpis(calc),
        cartoes=em_fileiras([c for c in cartoes if c is not None]),
    )


@dataclass(frozen=True)
class Calculo:
    pos: Posicao
    leitura: Leitura
    farois: Farois
    # filtro de vendedor escolhido: o a receber é só dele
    por_vendedor: bool

    @property
    def receber(self) -> Lado | None:
        r = self.leitura.receber
        return r if r is not None and (r.carteira.total or r.pagos or r.serie) else None

    @property
    def pagar(self) -> Lado | None:
        p = self.leitura.pagar
        return p if p is not None and (p.carteira.total or p.pagos) else None

    @property
    def media_paga(self) -> Decimal | None:
        return media_paga(self.pagar.pagos, self.pos.hoje) if self.pagar else None

    @property
    def cobertura(self) -> Decimal | None:
        """A pagar lançado nos próximos 30 dias, em % da média paga por mês."""
        media = self.media_paga
        if self.pagar is None or not media:
            return None
        return self.pagar.carteira.proximos(30) / media * CEM

    @property
    def lancado_incompleto(self) -> bool:
        cob = self.cobertura
        return cob is not None and self.farois.pagar_lancado.status(cob) != "saudavel"

    def inadimplencia(self) -> tuple[Decimal, Decimal, int, Status] | None:
        """(% vencido hoje, média da série, meses da série, status); None sem carteira ou com
        menos de 2 meses de série."""
        r = self.receber
        if r is None or not r.carteira.total:
            return None
        pontos = [p.vencido / p.aberto * CEM for p in r.serie if p.aberto]
        if len(pontos) < 2:
            return None
        atual = r.carteira.vencido / r.carteira.total * CEM
        media = sum(pontos, ZERO) / len(pontos)
        return atual, media, len(pontos), deslocado(self.farois.inadimplencia, media).status(atual)


# ---------- topo ----------


def kpis(c: Calculo) -> list[Kpi]:
    itens: list[Kpi] = []
    if c.receber is not None:
        itens.append(
            Kpi(
                rotulo="A receber em 30 dias",
                valor=brl(c.receber.carteira.proximos(30)),
                contexto="títulos do vendedor" if c.por_vendedor else "títulos emitidos",
            )
        )
    if c.pagar is not None:
        cob = c.cobertura
        itens.append(
            Kpi(
                rotulo="A pagar em 30 dias",
                valor=brl(c.pagar.carteira.proximos(30)),
                contexto=(
                    f"só o que está lançado: {numero(cob)}% da média paga"
                    if cob is not None
                    else "só o que está lançado"
                ),
                status=c.farois.pagar_lancado.status(cob) if cob is not None else None,
            )
        )
    inad = c.inadimplencia()
    if inad is not None:
        atual, media, n, status = inad
        itens.append(
            Kpi(
                rotulo="Inadimplência",
                valor=pct(atual),
                contexto=f"média de {n} meses: {numero(media, 1)}%",
                status=status,
            )
        )
    return itens


# ---------- cartões ----------


def cartao_janelas(c: Calculo) -> CartaoJanelas | None:
    receber, pagar = c.receber, c.pagar
    # com filtro de vendedor, o a pagar (da empresa) não se compara ao a receber dele
    if c.por_vendedor:
        pagar = None
    if receber is None and pagar is None:
        return None
    janelas: list[Janela] = []
    for dias in JANELAS:
        linhas: list[LinhaJanela] = []
        entra = receber.carteira.proximos(dias) if receber else ZERO
        sai = pagar.carteira.proximos(dias) if pagar else ZERO
        if receber is not None:
            linhas.append(LinhaJanela(rotulo="A receber", valor=brl(entra)))
        if pagar is not None:
            linhas.append(LinhaJanela(rotulo="A pagar", valor=brl(-sai)))
        if receber is not None and pagar is not None:
            linhas.append(
                LinhaJanela(
                    rotulo="Sobra no período",
                    valor=brl(entra - sai),
                    com_sinal=True,
                    tipo="total",
                )
            )
        janelas.append(Janela(titulo=f"Próximos {dias} dias", linhas=linhas))
    alerta = None
    cob = c.cobertura
    if pagar is not None and cob is not None and c.lancado_incompleto:
        alerta = AlertaJanelas(
            etiqueta="Dado incompleto",
            texto=[
                Trecho(texto="O a pagar só tem o que está lançado no sistema: "),
                Trecho(texto=f"{numero(cob)}% da média paga", destaque=True),
                Trecho(
                    texto=" por mês. Folha, impostos e despesas fixas podem não estar lançados, "
                    "então a sobra está maior do que será."
                ),
            ],
            rodape="Alerta de qualidade do dado: lançado nos próximos 30 dias ÷ média paga "
            "por mês nos 6 meses fechados.",
        )
    if c.por_vendedor:
        titulo, subtitulo = "Próximos dias: a receber", "O que vence a receber do vendedor"
    elif pagar is None:
        titulo, subtitulo = "Próximos dias: a receber", "O que vence a receber em cada janela"
    elif receber is None:
        titulo, subtitulo = "Próximos dias: a pagar", "O que vence a pagar em cada janela"
    else:
        titulo = "Próximos dias: entra, sai e quanto sobra"
        subtitulo = "O que vence a receber, menos o que vence a pagar em cada janela"
    return CartaoJanelas(
        id="proximos-dias",
        tipo="janelas",
        largura=12,
        titulo=titulo,
        subtitulo=subtitulo,
        janelas=janelas,
        alerta=alerta,
    )


def _status_idade(i: int, valor: Decimal) -> Status | None:
    if not valor:
        return None
    if i == 0:
        return "saudavel"
    return "atencao" if i == 1 else "critico"


def _idade(carteira: Carteira) -> list[FaixaIdade]:
    """5 faixas; a vencer inclui o que está em compensação (D+2)."""
    faixas = list(
        zip(
            ("A vencer", "1 a 30 dias", "31 a 60", "61 a 90", "Mais de 90"),
            (carteira.a_vencer, *carteira.faixas_vencidas),
            strict=True,
        )
    )
    total = carteira.total
    return [
        FaixaIdade(
            rotulo=rotulo,
            valor=brl(valor),
            participacao=q(valor / total * CEM, 1) if total else ZERO,
            status=_status_idade(i, valor),
        )
        for i, (rotulo, valor) in enumerate(faixas)
    ]


def _maiores(pessoas: list[Pessoa], total: Decimal, tipo: str) -> list[ItemMaior]:
    escolhidas = sorted(
        (p for p in pessoas if p.ordem_aberto is not None and p.ordem_aberto <= MAIORES),
        key=lambda p: p.ordem_aberto or 0,
    )
    return [
        ItemMaior(
            nome=p.exibicao(tipo),
            valor=brl(p.aberto),
            participacao=q(p.aberto / total * CEM, 1) if total else ZERO,
            etiqueta="vencido" if p.vencido else None,
        )
        for p in escolhidas
    ]


def prazos_mensais(lado: Lado, meses: Sequence[date]) -> list[Decimal | None]:
    return [lado.prazo(m) for m in meses]


def _kpi_prazo(lado: Lado, meses: Sequence[date], base: Farol) -> Kpi | None:
    """Prazo médio do último mês fechado contra a média dos meses da série."""
    valores = [v for v in prazos_mensais(lado, meses) if v is not None]
    atual = lado.prazo(meses[-1]) if meses else None
    if atual is None or not valores:
        return None
    media = sum(valores, ZERO) / len(valores)
    return Kpi(
        rotulo="Prazo médio",
        valor=Valor(valor=q(atual, 1), unidade="dias", casas=0),
        contexto=f"média de {len(valores)} meses: {numero(media)}",
        status=deslocado(base, media).status(atual),
    )


def cartao_dois_lados(c: Calculo) -> CartaoDoisLados | None:
    lados: list[LadoConta] = []
    meses = c.pos.meses
    r = c.receber
    if r is not None and r.carteira.total:
        cart = r.carteira
        inad = c.inadimplencia()
        kpis_r = [
            Kpi(
                rotulo="Vencido",
                valor=brl(cart.vencido),
                contexto=f"{numero(cart.vencido / cart.total * CEM, 1)}% da carteira",
                status=inad[3] if inad else None,
            )
        ]
        if (kp := _kpi_prazo(r, meses, c.farois.prazo_recebimento)) is not None:
            kpis_r.append(kp)
        pessoas = int(cart.valores["pessoas"])
        atraso = int(cart.valores["pessoas_atraso"])
        kpis_r.append(
            Kpi(
                rotulo="Clientes em atraso",
                valor=qtd(Decimal(atraso)),
                contexto=(
                    f"{numero(Decimal(atraso) / pessoas * CEM, 1)}% dos {pessoas} clientes "
                    "com título em aberto"
                    if pessoas
                    else "nenhum cliente com título em aberto"
                ),
            )
        )
        lados.append(
            LadoConta(
                titulo="Contas a receber do vendedor" if c.por_vendedor else "Contas a receber",
                total=f"em aberto: {moeda(cart.total)}",
                kpis=kpis_r,
                idade=_idade(cart),
                maiores=_maiores(c.leitura.clientes, cart.total, "Cliente"),
            )
        )
    p = c.pagar
    if p is not None and p.carteira.total:
        cart = p.carteira
        kpis_p = [
            Kpi(
                rotulo="Vencido",
                valor=brl(cart.vencido),
                contexto=f"{numero(cart.vencido / cart.total * CEM, 1)}% do total",
            )
        ]
        if (kp := _kpi_prazo(p, meses, c.farois.prazo_pagamento)) is not None:
            kpis_p.append(kp)
        kpis_p.append(
            Kpi(
                rotulo="Vence em 7 dias",
                valor=brl(cart.proximos(7)),
                contexto="só o que está lançado",
            )
        )
        cob = c.cobertura
        lados.append(
            LadoConta(
                titulo="Contas a pagar",
                total=f"em aberto: {moeda(cart.total)}",
                alerta=(
                    f"lançado: {numero(cob)}% da média paga"
                    if cob is not None and c.lancado_incompleto
                    else None
                ),
                kpis=kpis_p,
                idade=_idade(cart),
                maiores=_maiores(c.leitura.fornecedores, cart.total, "Fornecedor"),
            )
        )
    if not lados:
        return None
    return CartaoDoisLados(
        id="contas-receber-pagar",
        tipo="dois-lados",
        largura=12,
        titulo="Contas a receber e a pagar" if len(lados) == 2 else lados[0].titulo,
        subtitulo="O que está em aberto hoje, por idade, e os maiores saldos",
        lados=lados,
    )


def chance(p: Pessoa) -> Celula:
    """Chance de receber pelo histórico do cliente."""
    if not p.historico:
        return Celula(valor="sem histórico", tom="apagado")
    pago = (p.historico_pago or ZERO) / p.historico * CEM
    if pago >= CHANCE_ALTA:
        return Celula(pill=PillComRotulo(status="saudavel", rotulo="Alta"))
    if pago >= CHANCE_MEDIA:
        return Celula(pill=PillComRotulo(status="atencao", rotulo="Média"))
    return Celula(pill=PillComRotulo(status="critico", rotulo="Baixa"))


def _fundo_atraso(dias: int) -> Status:
    return "critico" if dias > 60 else "atencao" if dias > 30 else "saudavel"


def cartao_quem_cobrar(
    c: Calculo, nome_vendedor: Callable[[str | None], str | None] | None
) -> CartaoTabela | None:
    """`nome_vendedor`: código → primeiro nome; None quando os títulos não têm vendedor."""
    if c.receber is None:
        return None
    hoje = c.pos.hoje
    pessoas = sorted(
        (
            p
            for p in c.leitura.clientes
            if p.ordem_cobrar is not None and p.ordem_cobrar <= QUEM_COBRAR
        ),
        key=lambda p: p.ordem_cobrar or 0,
    )
    colunas = [
        ColunaTabela(rotulo="Cliente"),
        ColunaTabela(rotulo="Vencido"),
        ColunaTabela(rotulo="Atraso"),
        ColunaTabela(rotulo="Chance", alinhar="esquerda"),
    ]
    if nome_vendedor is not None:
        colunas.append(ColunaTabela(rotulo="Vendedor", alinhar="esquerda", ocultar_no_celular=True))
    linhas: list[LinhaTabela] = []
    for p in pessoas:
        atraso = p.atraso or 0
        parou = p.ultima is None or p.ultima < hoje - timedelta(days=DIAS_SEM_COMPRA)
        celulas = [
            Celula(valor=p.exibicao("Cliente"), etiqueta="parou de comprar" if parou else None),
            Celula(valor=brl(p.vencido)),
            Celula(
                valor=Valor(valor=Decimal(atraso), unidade="dias", casas=0),
                fundo=_fundo_atraso(atraso),
            ),
            chance(p),
        ]
        if nome_vendedor is not None:
            nome = nome_vendedor(p.vendedor)
            celulas.append(Celula(valor=nome) if nome else Celula(valor="–", tom="apagado"))
        linhas.append(LinhaTabela(celulas=celulas))
    return CartaoTabela(
        id="quem-cobrar",
        tipo="tabela",
        largura=7,
        titulo="Quem cobrar hoje",
        subtitulo="Maiores vencidos. A chance de receber vem do histórico de pagamento de cada "
        "cliente.",
        colunas=colunas,
        linhas=linhas,
        vazio="Nenhum título vencido com esses filtros.",
    )


def status_semana(valor: Decimal, media_semana: Decimal | None) -> Status | None:
    if not media_semana:
        return None
    if valor > media_semana * SEMANA_CRITICA:
        return "critico"
    return "atencao" if valor > media_semana else "saudavel"


def cartao_pagar_semana(c: Calculo) -> CartaoBarrasHorizontais | None:
    if c.pagar is None:
        return None
    semanas = c.pagar.carteira.semanas
    if not any(semanas):
        return None
    media = c.media_paga
    media_semana = media * 12 / 52 if media else None
    itens: list[BarraHorizontal] = []
    for i, valor in enumerate(semanas):
        inicio = c.pos.hoje + timedelta(days=7 * i)
        itens.append(
            BarraHorizontal(
                rotulo=f"Semana {i + 1}",
                sub=f"{inicio:%d/%m} a {inicio + timedelta(days=6):%d/%m}",
                valor=q(valor),
                status=status_semana(valor, media_semana),
            )
        )
    maior = max(range(len(itens)), key=lambda i: semanas[i])
    complemento = (
        f" É {numero(semanas[maior] / media_semana * CEM)}% da média paga por semana."
        if media_semana
        else ""
    )
    return CartaoBarrasHorizontais(
        id="pagar-semana",
        tipo="barras-horizontais",
        largura=5,
        titulo="A pagar por semana",
        subtitulo="Próximas 4 semanas, só o que está lançado",
        escala=Escala(unidade="brl"),
        itens=itens,
        chamada=Chamada(
            status=itens[maior].status or "atencao",
            texto=[
                Trecho(
                    texto=f"Semana {maior + 1} ({itens[maior].sub}): "
                    f"{moeda(semanas[maior])} a pagar",
                    destaque=True,
                ),
                Trecho(texto=f", a maior das quatro.{complemento}"),
            ],
        ),
    )


def status_safra(valor: Decimal, media: Decimal) -> Status:
    if valor > media * TOLERANCIA_SAFRA:
        return "critico"
    return "atencao" if valor > media else "saudavel"


def cartao_safra(c: Calculo) -> CartaoTabela | None:
    r = c.receber
    if r is None:
        return None
    safras = [s for s in r.safras if s.total > 0]
    if not safras:
        return None
    hoje = c.pos.hoje
    # (mês, dias) → % em aberto, só quando o prazo já passou para o mês inteiro
    valores: dict[tuple[date, int], Decimal] = {}
    for s in safras:
        for dias in DIAS_SAFRA:
            if ultimo_dia_do_mes(s.mes) + timedelta(days=dias) <= hoje:
                valores[(s.mes, dias)] = s.em_aberto[dias] / s.total * CEM
    medias = {
        dias: sum(vs, ZERO) / len(vs)
        for dias in DIAS_SAFRA
        if (vs := [v for (_, d), v in valores.items() if d == dias])
    }
    linhas = []
    for s in safras:
        celulas = [Celula(valor=rotulo_eixo(s.mes))]
        for dias in DIAS_SAFRA:
            v = valores.get((s.mes, dias))
            if v is None:
                celulas.append(Celula(valor="ainda não", tom="apagado"))
            else:
                celulas.append(Celula(valor=pct(v), fundo=status_safra(v, medias[dias])))
        linhas.append(LinhaTabela(celulas=celulas))
    return CartaoTabela(
        id="inadimplencia-safra",
        tipo="tabela",
        variante="calor",
        largura=7,
        titulo="Inadimplência por mês de vencimento",
        subtitulo="Percentual que ainda estava em aberto 15, 30, 60 e 90 dias depois do "
        "vencimento. Compara meses na mesma idade.",
        colunas=[
            ColunaTabela(rotulo="Mês de vencimento"),
            ColunaTabela(rotulo="15 dias depois", alinhar="centro"),
            ColunaTabela(rotulo="30 dias", alinhar="centro"),
            ColunaTabela(rotulo="60 dias", alinhar="centro"),
            ColunaTabela(rotulo="90 dias", alinhar="centro"),
        ],
        linhas=linhas,
    )


def _eixo(meses: Sequence[date]) -> tuple[list[str], list[str]]:
    return [MESES_CURTOS[m.month - 1] for m in meses], [rotulo_eixo(m) for m in meses]


def cartao_inadimplencia_12_meses(c: Calculo) -> CartaoGraficos | None:
    r = c.receber
    if r is None:
        return None
    pontos = [(p.dia.replace(day=1), p.vencido / p.aberto * CEM) for p in r.serie if p.aberto]
    if len(pontos) < 2:
        return None
    media = sum((v for _, v in pontos), ZERO) / len(pontos)
    rotulos, titulos = _eixo([m for m, _ in pontos])
    return CartaoGraficos(
        id="inadimplencia-12-meses",
        tipo="graficos",
        largura=5,
        titulo=f"Inadimplência em {len(pontos)} meses",
        subtitulo="Vencido sobre a carteira no último dia de cada mês, recalculado pelos títulos",
        graficos=[
            GraficoCartao(
                grafico=GraficoLinhas(
                    tipo="linhas",
                    unidade="pct",
                    casas=1,
                    rotulos=rotulos,
                    titulos=titulos,
                    piso=ZERO,
                    series=[
                        SerieLinha(
                            nome="Vencido sobre a carteira",
                            valores=[q(v, 1) for _, v in pontos],
                            estilo="principal",
                        )
                    ],
                    referencia=ReferenciaLinha(
                        valor=q(media, 1),
                        rotulo=f"média de {len(pontos)} meses {numero(media, 1)}%",
                    ),
                    marcadores=[
                        Marcador(serie=0, i=len(pontos) - 1, rotulo=f"{numero(pontos[-1][1], 1)}%")
                    ],
                )
            )
        ],
    )


def cartao_idade_vencidos(c: Calculo) -> CartaoGraficos | None:
    r = c.receber
    if r is None:
        return None
    serie = [p for p in r.serie if p.aberto]
    if len(serie) < 2 or not any(p.vencido for p in serie):
        return None
    rotulos, _ = _eixo([p.dia for p in serie])
    return CartaoGraficos(
        id="idade-vencidos",
        tipo="graficos",
        largura=12,
        titulo="Idade dos vencidos mês a mês",
        subtitulo="Vencido no fim de cada mês por faixa de atraso",
        graficos=[
            GraficoCartao(
                grafico=GraficoEmpilhado(
                    tipo="empilhado",
                    unidade="brl",
                    paleta="atraso",
                    rotulos=rotulos,
                    series=[
                        SerieEmpilhada(nome=nome, valores=[q(p.faixas[i]) for p in serie])
                        for i, nome in enumerate(FAIXAS_SERIE)
                    ],
                )
            )
        ],
    )


def cartao_prazos(c: Calculo) -> CartaoGraficos | None:
    meses = c.pos.meses
    series: list[SerieLinha] = []
    lados: tuple[tuple[Lado | None, str, EstiloLinha], ...] = (
        (c.pagar, "Pagamento", "secundaria"),
        (c.receber, "Recebimento", "principal"),
    )
    for lado, nome, estilo in lados:
        if lado is None:
            continue
        valores = prazos_mensais(lado, meses)
        if sum(1 for v in valores if v is not None) < 2:
            continue
        ultimo = next((v for v in reversed(valores) if v is not None), None)
        series.append(
            SerieLinha(
                nome=nome,
                valores=[None if v is None else q(v, 1) for v in valores],
                estilo=estilo,
                rotulo_fim=f"{nome}, {numero(ultimo)} d" if ultimo is not None else None,
            )
        )
    if not series:
        return None
    rotulos, titulos = _eixo(meses)
    return CartaoGraficos(
        id="prazos",
        tipo="graficos",
        largura=7,
        titulo="Prazos de recebimento e de pagamento",
        subtitulo=f"Prazo médio, em dias, nos últimos {len(meses)} meses. Quanto maior a "
        "distância, mais tempo o dinheiro fica preso.",
        nota="O ciclo de caixa completo (com o prazo de estoque) entra quando houver o custo do "
        "estoque.",
        graficos=[
            GraficoCartao(
                grafico=GraficoLinhas(
                    tipo="linhas",
                    unidade="dias",
                    rotulos=rotulos,
                    titulos=titulos,
                    series=series,
                )
            )
        ],
    )


def cartao_limite(c: Calculo) -> CartaoTabela | None:
    if c.receber is None or not c.leitura.tem_limite:
        return None
    pessoas = sorted(
        (p for p in c.leitura.clientes if p.ordem_limite is not None and p.ordem_limite <= LIMITES),
        key=lambda p: p.ordem_limite or 0,
    )
    linhas = [
        LinhaTabela(
            celulas=[
                Celula(
                    valor=p.exibicao("Cliente"),
                    etiqueta="título vencido" if p.vencido_total else None,
                ),
                Celula(valor=brl(p.limite or ZERO)),
                Celula(valor=brl(p.aberto_total)),
                Celula(valor=brl(p.aberto_total - (p.limite or ZERO)), tom="critico", negrito=True),
            ]
        )
        for p in pessoas
    ]
    return CartaoTabela(
        id="limite-credito",
        tipo="tabela",
        largura=5,
        titulo="Clientes acima do limite de crédito",
        subtitulo="Em aberto contra o limite cadastrado no ERP",
        nota="Cliente sem limite cadastrado fica fora da lista.",
        colunas=[
            ColunaTabela(rotulo="Cliente"),
            ColunaTabela(rotulo="Limite"),
            ColunaTabela(rotulo="Em aberto"),
            ColunaTabela(rotulo="Excesso"),
        ],
        linhas=linhas,
        vazio="Nenhum cliente acima do limite.",
    )


# ---------- para o Resultado (tabela financeira) ----------


@dataclass(frozen=True)
class IndicadoresMensais:
    """Prazos e inadimplência de cada mês da série (mês → valor)."""

    recebimento: dict[date, Decimal]
    pagamento: dict[date, Decimal]
    inadimplencia: dict[date, Decimal]


def indicadores_mensais(
    conn: Conexao, ctx: ContextoConsolidado, ate: date
) -> IndicadoresMensais | None:
    """Os 12 meses até o mês de `ate` (inclusive), sem filtro; None sem títulos."""
    f = fontes(conn, ctx)
    if not f.receber and not f.pagar:
        return None
    pos = posicao(conn, ctx, ultimo_dia_do_mes(ate) + timedelta(days=1))
    consultas: dict[str, Consulta] = {}
    if f.receber:
        consultas["receber"] = consulta_lado(ctx, f.receber, "cliente", pos, None)
    if f.pagar:
        consultas["pagar"] = consulta_lado(ctx, f.pagar, "fornecedor", pos, None)
    linhas = em_uma_ida(conn, consultas)
    receber = lado_das_linhas(linhas["receber"]) if f.receber else None
    pagar = lado_das_linhas(linhas["pagar"]) if f.pagar else None

    def prazos(lado: Lado | None) -> dict[date, Decimal]:
        if lado is None:
            return {}
        return {m: v for m in pos.meses if (v := lado.prazo(m)) is not None}

    return IndicadoresMensais(
        recebimento=prazos(receber),
        pagamento=prazos(pagar),
        inadimplencia=(
            {p.dia.replace(day=1): p.vencido / p.aberto * CEM for p in receber.serie if p.aberto}
            if receber
            else {}
        ),
    )

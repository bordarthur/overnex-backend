"""Partes comuns dos painéis comerciais. REGRAS PROVISÓRIAS: confirmar com o negócio (ver
TODO.md).

Fontes do cliente, pelo prefixo da tabela (cfg.fonte_carga), com a coluna de cada papel
(padrão abaixo, trocado por cfg.fonte_carga.colunas_modelo):

- vendas (f_vendas_*): os papéis da Visão geral e mais três: vendedor, linha e quantidade
  (padrão: as colunas da PJ Cortes, en_codigovendedor, it_grupo e en_quantidade). Papel
  cuja coluna não existe na tabela fica de fora, e o que depende dele não sai (filtros de
  canal, supervisor e linha, cartões por vendedor e por linha, visão Volume). A carga só
  grava o faturado por vendedor (rampa) quando a fonte informa o papel em colunas_modelo;
- metas (f_metas_*): data, valor, vendedor (código do vendedor) e clientes (meta de
  clientes que compram). Sem filtro, a meta do mês é a soma de todas as linhas; com filtro
  de canal ou supervisor, a dos vendedores escolhidos;
- vendedores (d_vendedor_*): código, nome, canal e supervisor;
- produtos (d_bitem_*): código e nome;
- títulos a receber (f_receitas_*): os do Financeiro e o código do vendedor.

Mês: o escolhido (`mes`) ou o mais recente com venda. Mês fechado (antes do mês da última
carga): os números são do mês inteiro, sem projeção. Dias úteis e projeção, como na Visão
geral: ritmo = vendido ÷ dias úteis até a última venda; projeção = ritmo × dias úteis do mês.
"""

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from fastapi import HTTPException, status
from psycopg import sql

from app.core.cache import existe, existe_coluna
from app.core.db import Conexao, em_uma_ida
from app.seguranca.acesso import ContextoConsolidado
from app.telas.calendario import dias_uteis, feriados, ultimo_dia_do_mes
from app.telas.paineis.contrato import Aviso, Cartao, FiltroPainel, ItemValor, OpcaoFiltro
from app.telas.visao_geral.contrato import Kpi
from app.telas.visao_geral.financeiro import COLUNAS_RECEBER
from app.telas.visao_geral.vendas import (
    COLUNAS_METAS,
    COLUNAS_VENDAS,
    TabelaDoCliente,
    data_texto,
    filtro_empresa,
    mes_menos,
    tabelas_do_cliente,
)

# os três últimos não existem no DW padrão (Nilo Maia): sem a coluna, o papel fica de fora
COLUNAS_VENDAS_PAINEL: Mapping[str, str] = {
    **COLUNAS_VENDAS,
    "vendedor": "en_codigovendedor",
    "linha": "it_grupo",
    "quantidade": "en_quantidade",
}
COLUNAS_METAS_PAINEL: Mapping[str, str] = {
    **COLUNAS_METAS,
    "vendedor": "en_vendedor",
    "clientes": "NDeClientes",
}
COLUNAS_VENDEDOR: Mapping[str, str] = {
    "codigo": "en_codigovendedor",
    "nome": "en_nome",
    "canal": "Canal",
    "supervisor": "sp_supervisor",
}
COLUNAS_PRODUTO: Mapping[str, str] = {"codigo": "en_codigoproduto", "nome": "en_nome"}
COLUNAS_RECEBER_PAINEL: Mapping[str, str] = {**COLUNAS_RECEBER, "vendedor": "en_codigovendedor"}

MSG_FILTRO_INVALIDO = "Filtro inválido."

Tipo = Literal["data", "numero", "texto"]
_TIPOS_SQL = {"data": "date", "numero": "numeric", "texto": "text"}


# ---------- fontes ----------


@dataclass(frozen=True)
class Fontes:
    vendas: list[TabelaDoCliente]
    metas: list[TabelaDoCliente]
    vendedores: list[TabelaDoCliente]
    produtos: list[TabelaDoCliente]
    receber: list[TabelaDoCliente]


def fontes(conn: Conexao, ctx: ContextoConsolidado) -> Fontes:
    c = ctx.cliente_id
    return Fontes(
        vendas=tabelas_do_cliente(conn, c, "f_vendas_", COLUNAS_VENDAS_PAINEL),
        metas=tabelas_do_cliente(conn, c, "f_metas_", COLUNAS_METAS_PAINEL),
        vendedores=tabelas_do_cliente(conn, c, "d_vendedor_", COLUNAS_VENDEDOR),
        produtos=tabelas_do_cliente(conn, c, "d_bitem_", COLUNAS_PRODUTO),
        receber=tabelas_do_cliente(conn, c, "f_receitas_", COLUNAS_RECEBER_PAINEL),
    )


def coluna(t: TabelaDoCliente, papel: str) -> str | None:
    """Coluna do papel na tabela; None se o papel não tem coluna ou ela não existe no mart."""
    nome = t.colunas.get(papel)
    return nome if nome and nome in t.existentes else None


def tem_papel(tabelas: list[TabelaDoCliente], papel: str) -> bool:
    return any(coluna(t, papel) for t in tabelas)


def selecao(
    tabelas: list[TabelaDoCliente],
    campos: Mapping[str, tuple[str, Tipo]],
    filtro: sql.Composable,
) -> sql.Composable:
    """`select <campos> from <tabela> where <cliente e empresa>` de cada tabela, em union all.

    `campos`: apelido → (papel, tipo). Papel sem coluna na tabela vira null; data em texto
    vira date (data_texto); código vira texto sem espaços nas pontas (vazio = null).
    """

    def expressao(t: TabelaDoCliente, papel: str, tipo: Tipo) -> sql.Composable:
        nome = coluna(t, papel)
        if nome is None:
            return sql.SQL("null::{}").format(sql.SQL(_TIPOS_SQL[tipo]))
        if tipo == "data":
            return sql.Identifier(nome) if nome in t.datas else data_texto(nome)
        if tipo == "numero":
            return sql.Identifier(nome)
        return sql.SQL("nullif(trim({}::text), '')").format(sql.Identifier(nome))

    return sql.SQL(" union all ").join(
        sql.SQL(
            "select {campos} from {tabela} where _cliente_id = %(cliente)s and {filtro}"
        ).format(
            campos=sql.SQL(", ").join(
                sql.SQL("{} as {}").format(expressao(t, papel, tipo), sql.Identifier(apelido))
                for apelido, (papel, tipo) in campos.items()
            ),
            tabela=sql.Identifier("mart", t.tabela),
            filtro=filtro,
        )
        for t in tabelas
    )


CAMPOS_VENDAS: Mapping[str, tuple[str, Tipo]] = {
    "dia": ("data", "data"),
    "valor": ("valor", "numero"),
    "custo": ("custo", "numero"),
    "quantidade": ("quantidade", "numero"),
    "pedido": ("pedido", "texto"),
    "cliente": ("cliente", "texto"),
    "produto": ("produto", "texto"),
    "vendedor": ("vendedor", "texto"),
    "linha": ("linha", "texto"),
}


def vendas_sql(ctx: ContextoConsolidado, f: Fontes) -> sql.Composable:
    """As linhas de venda de todas as tabelas f_vendas_* do cliente (CAMPOS_VENDAS)."""
    return selecao(f.vendas, CAMPOS_VENDAS, filtro_empresa(ctx))


def parametros(ctx: ContextoConsolidado, **outros: Any) -> dict[str, Any]:
    return {"cliente": ctx.cliente_id, "empresas": list(ctx.empresas), **outros}


# ---------- filtros ----------


def slug(texto: str) -> str:
    """ "Televendas Sul" → "televendas-sul"; sem acento."""
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"[^a-z0-9]+", "-", sem_acento.lower()).strip("-")


def rotulo(texto: str) -> str:
    """Valor do DW como rótulo: "CARNES BOVINAS" → "Carnes bovinas"; o resto fica igual."""
    limpo = " ".join(texto.split())
    return limpo.capitalize() if limpo.isupper() else limpo


@dataclass(frozen=True)
class Opcoes:
    """Um filtro e, para cada opção, os valores do DW que ela junta."""

    filtro: FiltroPainel
    valores: Mapping[str, tuple[str, ...]]


def opcoes(
    id: str, rotulo_filtro: str, valores: Iterable[str | None], *, todas: bool = False
) -> Opcoes:
    """Filtro com a opção padrão ("todos" ou "todas") e uma opção por valor distinto.

    Valores que só mudam em acento, caixa ou espaços viram a mesma opção.
    """
    padrao = "todas" if todas else "todos"
    grupos: dict[str, list[str]] = {}
    for v in valores:
        if v is None or not v.strip():
            continue
        chave = slug(v)
        if chave and chave != padrao:
            grupos.setdefault(chave, []).append(v)
    itens = sorted(((rotulo(vs[0]), chave) for chave, vs in grupos.items()), key=lambda x: x[0])
    return Opcoes(
        filtro=FiltroPainel(
            id=id,
            rotulo=rotulo_filtro,
            opcoes=[
                OpcaoFiltro(id=padrao, rotulo="Todas" if todas else "Todos"),
                *(OpcaoFiltro(id=chave, rotulo=r) for r, chave in itens),
            ],
            padrao=padrao,
        ),
        valores={chave: tuple(vs) for chave, vs in grupos.items()},
    )


def escolher(o: Opcoes | None, valor: str | None) -> tuple[str, ...] | None:
    """Valores do DW da opção escolhida; None = sem filtro (sem valor ou a opção padrão).

    Opção fora da lista (ou filtro que a tela não tem) → 422.
    """
    if valor is None:
        return None
    if o is not None and valor == o.filtro.padrao:
        return None
    if o is None or valor not in o.valores:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=MSG_FILTRO_INVALIDO
        )
    return o.valores[valor]


# ---------- leituras comuns ----------


@dataclass(frozen=True)
class Vendedor:
    codigo: str
    nome: str | None
    canal: str | None
    supervisor: str | None

    @property
    def exibicao(self) -> str:
        return " ".join(self.nome.split()).title() if self.nome else f"Vendedor {self.codigo}"

    @property
    def primeiro_nome(self) -> str:
        return self.exibicao.split()[0] if self.nome else self.exibicao


@dataclass(frozen=True)
class Metas:
    """Metas por mês (primeiro dia) e vendedor (None = linha sem vendedor)."""

    valor: Mapping[tuple[date, str | None], Decimal] = field(default_factory=dict)
    clientes: Mapping[tuple[date, str | None], Decimal] = field(default_factory=dict)

    def de(
        self, mes: date, vendedores: Iterable[str] | None = None, *, clientes: bool = False
    ) -> Decimal | None:
        """Meta do mês (soma); `vendedores`: só a deles. None sem meta."""
        fonte = self.clientes if clientes else self.valor
        escolhidos = None if vendedores is None else set(vendedores)
        valores = [
            v
            for (m, vend), v in fonte.items()
            if m == mes and (escolhidos is None or vend in escolhidos)
        ]
        total = sum(valores, Decimal(0))
        return total if valores and total > 0 else None


@dataclass(frozen=True)
class Panorama:
    # mês da tela (primeiro dia); None sem venda e sem mês escolhido
    mes: date | None
    # última venda dentro do mês
    ultimo_dia: date | None
    # valores distintos do papel linha nas tabelas de vendas
    linhas: list[str]


type Consulta = tuple[sql.Composed, dict[str, Any]]


def _consulta_vendedores(ctx: ContextoConsolidado, f: Fontes) -> Consulta:
    campos: Mapping[str, tuple[str, Tipo]] = {
        "codigo": ("codigo", "texto"),
        "nome": ("nome", "texto"),
        "canal": ("canal", "texto"),
        "supervisor": ("supervisor", "texto"),
    }
    return sql.SQL(
        """
        select distinct on (codigo) codigo, nome, canal, supervisor
          from ({vendedores}) t
         where codigo is not null
         order by codigo, nome is null, canal is null
        """
    ).format(vendedores=selecao(f.vendedores, campos, filtro_empresa(ctx))), parametros(ctx)


def _vendedores(linhas: list[Any]) -> dict[str, Vendedor]:
    """Uma linha por código (a com nome, se houver)."""
    return {
        r["codigo"]: Vendedor(r["codigo"], r["nome"], r["canal"], r["supervisor"]) for r in linhas
    }


def _consulta_metas(ctx: ContextoConsolidado, f: Fontes) -> Consulta:
    campos: Mapping[str, tuple[str, Tipo]] = {
        "dia": ("data", "data"),
        "valor": ("valor", "numero"),
        "vendedor": ("vendedor", "texto"),
        "clientes": ("clientes", "numero"),
    }
    return sql.SQL(
        """
        select date_trunc('month', dia)::date as mes, vendedor,
               sum(valor) as valor, sum(clientes) as clientes
          from ({metas}) m
         where dia is not null
         group by 1, 2
        """
    ).format(metas=selecao(f.metas, campos, filtro_empresa(ctx))), parametros(ctx)


def _metas(linhas: list[Any]) -> Metas:
    """Todas as metas do cliente por mês e vendedor (a tabela de metas é pequena)."""
    valor: dict[tuple[date, str | None], Decimal] = {}
    clientes: dict[tuple[date, str | None], Decimal] = {}
    for r in linhas:
        if r["valor"]:
            valor[(r["mes"], r["vendedor"])] = r["valor"]
        if r["clientes"]:
            clientes[(r["mes"], r["vendedor"])] = r["clientes"]
    return Metas(valor=valor, clientes=clientes)


def _consulta_panorama(ctx: ContextoConsolidado, f: Fontes, mes: date | None) -> Consulta:
    return sql.SQL(
        """
        with v as materialized (select dia, linha from ({vendas}) t where dia is not null),
        ref as (select coalesce(%(mes)s::date, date_trunc('month', max(dia))::date) as mes
                  from v)
        select ref.mes,
               (select max(dia) from v
                 where dia >= ref.mes and dia < ref.mes + interval '1 month') as ultimo_dia,
               array(select distinct linha from v where linha is not null) as linhas
          from ref
        """
    ).format(vendas=vendas_sql(ctx, f)), parametros(ctx, mes=mes)


def _consulta_resumo_vendedor(ctx: ContextoConsolidado, f: Fontes) -> Consulta:
    return sql.SQL(
        """
        select mes, trim(dimensao) as dimensao, sum(valor) as valor
          from mart.resumo_mensal
         where cliente_id = %(cliente)s and tabela = any(%(tabelas)s)
           and medida = 'faturado_vendedor' and {filtro}
         group by 1, 2
        """
    ).format(filtro=filtro_empresa(ctx, "empresa_id")), parametros(
        ctx, tabelas=[t.tabela for t in f.vendas]
    )


@dataclass(frozen=True)
class InicioPainel:
    panorama: Panorama
    vendedores: dict[str, Vendedor]
    metas: Metas
    # faturado por mês e vendedor do resumo mensal (histórico inteiro); vazio sem ele
    resumo_vendedor: dict[tuple[date, str], Decimal]


def ler_inicio(
    conn: Conexao,
    ctx: ContextoConsolidado,
    f: Fontes,
    mes: date | None,
    *,
    com_vendedores: bool = True,
    com_resumo: bool = False,
) -> InicioPainel:
    """O que todo painel lê antes da consulta principal, numa ida ao banco só: o mês da tela,
    a última venda nele e as linhas (panorama), o cadastro dos vendedores, as metas e, com
    `com_resumo`, o faturado por vendedor do resumo mensal (gravado pela carga quando a fonte
    informa o papel vendedor)."""
    consultas: dict[str, Consulta] = {}
    if f.vendas:
        consultas["panorama"] = _consulta_panorama(ctx, f, mes)
    if com_vendedores and f.vendedores:
        consultas["vendedores"] = _consulta_vendedores(ctx, f)
    if com_vendedores and f.metas:
        consultas["metas"] = _consulta_metas(ctx, f)
    if (
        com_resumo
        and f.vendas
        and existe(conn, "mart.resumo_mensal")
        and existe_coluna(conn, "mart.resumo_mensal", "dimensao")
    ):
        consultas["resumo"] = _consulta_resumo_vendedor(ctx, f)
    linhas = em_uma_ida(conn, consultas)

    panorama = Panorama(mes=mes, ultimo_dia=None, linhas=[])
    if linhas.get("panorama"):  # agregado sem group by: sempre uma linha
        r = linhas["panorama"][0]
        panorama = Panorama(mes=r["mes"], ultimo_dia=r["ultimo_dia"], linhas=r["linhas"])
    return InicioPainel(
        panorama=panorama,
        vendedores=_vendedores(linhas.get("vendedores", [])),
        metas=_metas(linhas.get("metas", [])),
        resumo_vendedor={(r["mes"], r["dimensao"]): r["valor"] for r in linhas.get("resumo", [])},
    )


# ---------- o mês da tela ----------


@dataclass(frozen=True)
class MesPainel:
    mes: date
    # última venda; no mês fechado, o último dia do mês
    ultimo_dia: date
    fechado: bool
    feriados: frozenset[date]
    du_mes: int
    du_passados: int

    @property
    def du_restantes(self) -> int:
        return self.du_mes - self.du_passados

    @property
    def aberto(self) -> bool:
        """Ainda há dia útil pela frente: vale projetar."""
        return self.du_restantes > 0 and self.du_passados > 0

    def projetar(self, valor: Decimal) -> Decimal:
        """Valor no fim do mês, no ritmo atual (por dia útil); fechado, o próprio valor."""
        if not self.aberto:
            return valor
        return valor / self.du_passados * self.du_mes

    def util(self, dia: date) -> bool:
        return dia.weekday() < 5 and dia not in self.feriados


def mes_painel(
    conn: Conexao, ctx: ContextoConsolidado, mes: date, ultimo_dia: date, fechado: bool
) -> MesPainel:
    fim = ultimo_dia_do_mes(mes)
    fer = feriados(conn, ctx.cliente_id, mes, fim)
    ultimo = fim if fechado else ultimo_dia
    return MesPainel(
        mes=mes,
        ultimo_dia=ultimo,
        fechado=fechado,
        feriados=frozenset(fer),
        du_mes=dias_uteis(mes, fim, fer),
        du_passados=dias_uteis(mes, ultimo, fer),
    )


def meses_antes(mes: date, n: int) -> list[date]:
    """Os `n` meses antes de `mes`, do mais antigo para o mais recente."""
    return [mes_menos(mes, k) for k in range(n, 0, -1)]


# ---------- conteúdo ----------


@dataclass
class Conteudo:
    """O que cada tela monta; a moldura (cliente, datas) é do router."""

    filtros: list[FiltroPainel] = field(default_factory=list)
    destaques: list[ItemValor] = field(default_factory=list)
    kpis: list[Kpi] = field(default_factory=list)
    apoios: list[ItemValor] = field(default_factory=list)
    cartoes: list[Cartao] = field(default_factory=list)
    aviso: Aviso | None = None


def mediana(valores: list[Decimal]) -> Decimal:
    s = sorted(valores)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2

"""FIN-04 — Fluxo de caixa (DFC realizado). REGRAS PROVISÓRIAS: confirmar com o negócio (ver
TODO.md, "Fluxo de caixa pelo plano"). Antes do realizado vem a projeção de 60 dias, que
parte do saldo final daqui no último dia com caixa (projecao.py, com o filtro `projecao`).

Fonte: as tabelas f_fluxo_* do cliente (PJ: mart.f_fluxo_pj, a view f_Fluxo_PJ do Azure: a
645 do Winthor mais o borderô aberto por título). Por linha: Data (compensação), Valor (com
o sinal do caixa: entrada positiva, saída negativa), Id_Descritivo (conta do plano da LANN,
pelo de-para do cliente) e CodConta (conta do ERP). Linha sem empresa vale para o cliente
inteiro: o filtro de empresa só tira as linhas de outra empresa (a PJ não tem nenhuma).

Cada linha pelo plano de contas padrão da LANN (cfg.plano_conta, app/telas/plano.py):
- conta com competência CA ou CO/CA e classe FCO, FCF ou FCI: entra na seção da classe, como
  entrada (fator +1) ou saída (fator −1), aberta pelos níveis do plano;
- conta com competência CO (só resultado: depreciação, PDD…): fora do fluxo e do saldo,
  citada no rodapé. Só com FILTRO_COMPETENCIA: DESLIGADO desde 06/11/2026, para um teste do
  usuário. Desligado, a competência não conta: toda conta do plano com classe de caixa entra
  na seção dela (as CO do plano padrão são todas FCO), e a sem classe (ex.: 900.01.01
  Transferência entre contas) fica em "Sem classificação";
- sem Id_Descritivo, fora do plano ou sem classe de caixa: "Sem classificação", que entra na
  variação e no saldo (o saldo final bate com o banco) e é citada no rodapé pela conta do ERP.
Sem o plano cadastrado (antes da migration 17), todo o caixa fica sem classificação.

Saldo (cfg.saldo_inicial, migration 22: o saldo no início de um dia): o saldo no início do
dia D parte do saldo cadastrado mais recente até D (sem nenhum antes, do primeiro depois de
D, para trás) e soma o caixa entre os dois (o fluxo e o sem classificação; o que ficou fora
não). Mês sem caixa: sem saldo (célula vazia, sem repetir o anterior). Sem saldo cadastrado:
só o movimento, com aviso.

Período (filtro visao), sobre o mês escolhido (`mes`) ou, sem ele, o último mês com caixa (o
mês em aberto vai até o último dia com caixa):
- mes: o mês; a tabela e o gráfico de geração mostram os 6 meses até ele;
- ano: de janeiro (ou do primeiro mês com caixa) até ele; a tabela ganha a coluna do
  acumulado, que abre no saldo do primeiro mês com caixa e fecha no do último;
- historico ("Desde abril de 2025"; só quando o caixa começa antes de janeiro do ano do
  mês): do primeiro mês com caixa até ele, com a coluna do acumulado de tudo, como no "ano".
  Sem comparação com período anterior; o gráfico de geração mostra os últimos 12 meses.

Destaques: saldo final (crítico se negativo), geração operacional (FCO) e variação do caixa
(crítica se negativa, saudável se não), com o mês anterior no contexto (no acumulado, o mesmo
período do ano anterior, quando há caixa em todos os meses dele). Apoios: entradas e saídas
operacionais, FCF, FCI e sem classificação. Cartões: cascata do saldo inicial ao final (só
com saldo), geração de caixa por mês (FCO, FCF e FCI) e a árvore mês a mês (nível 1 =
entradas e saídas de cada seção; 2 a 4 = grupo, subgrupo e conta do plano), com fundo de
farol no FCO e na variação (a mesma regra dos destaques).
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from functools import cached_property
from typing import Any, Literal

from psycopg import sql

from app.seguranca.acesso import ContextoConsolidado
from app.telas.calendario import ultimo_dia_do_mes
from app.telas.contrato import Status
from app.telas.moldura import MESES, UltimaCarga
from app.telas.paineis import projecao
from app.telas.paineis.base import (
    Conteudo,
    Opcoes,
    Tipo,
    em_fileiras,
    escolher,
    meses_antes,
    parametros,
    rotulo,
    selecao,
)
from app.telas.paineis.contrato import (
    Aviso,
    Barra,
    Cartao,
    CartaoArvoreMensal,
    CartaoGraficos,
    ColunaArvore,
    Escala,
    EstiloBarra,
    FiltroPainel,
    GraficoBarras,
    GraficoCartao,
    GraficoCascata,
    GrupoBarras,
    ItemLegenda,
    ItemValor,
    LinhaArvoreMensal,
    MarcaLegenda,
    OpcaoFiltro,
)
from app.telas.paineis.formato import brl, nome_mes, q
from app.telas.paineis.resultado import Cascata, piso_da_escala
from app.telas.plano import COMPETENCIA_CAIXA, Plano, ler_plano, normalizar
from app.telas.saldo import Saldos, ler_saldos
from app.telas.visao_geral.contra_referencia import MESES_CURTOS
from app.telas.visao_geral.contrato import Trecho
from app.telas.visao_geral.texto import moeda, moeda_sinal
from app.telas.visao_geral.vendas import mes_menos, tabelas_do_cliente

ZERO = Decimal(0)
# fluxo só com as contas de competência CA e CO/CA (ver o topo). Desligado em 06/11/2026 para um
# teste do usuário: volte para True ao fim do teste (ver TODO.md)
FILTRO_COMPETENCIA = False
# meses da tabela e do gráfico de geração na visão "mes"
MESES_VISAO_MES = 6
# meses do gráfico de geração na visão "historico" (a tabela mostra todos)
MESES_GRAFICO_HISTORICO = 12
# contas citadas no rodapé, por linha
CITADAS = 5

COLUNAS_FLUXO: Mapping[str, str] = {
    "data": "Data",
    "valor": "Valor",
    "conta": "Id_Descritivo",
    "codigo": "CodConta",
}
CAMPOS_FLUXO: Mapping[str, tuple[str, Tipo]] = {
    "dia": ("data", "data"),
    "valor": ("valor", "numero"),
    "conta": ("conta", "texto"),
    "cod": ("codigo", "texto"),
}


@dataclass(frozen=True)
class Secao:
    classe: Literal["FCO", "FCF", "FCI"]
    id: str
    # "Entradas operacionais", "Saídas de financiamento"
    adjetivo: str
    nome: str
    # passo da cascata e legenda
    curto: str
    # barra do gráfico de geração
    barra: str
    estilo: EstiloBarra
    marca: MarcaLegenda


SECOES: tuple[Secao, ...] = (
    Secao(
        "FCO",
        "fco",
        "operacionais",
        "Geração operacional (FCO)",
        "Operação",
        "Operação (FCO)",
        "serie",
        "quadrado-escuro",
    ),
    Secao(
        "FCF",
        "fcf",
        "de financiamento",
        "Financiamentos (FCF)",
        "Financiamentos",
        "Financiamentos (FCF)",
        "serie-azul",
        "quadrado-azul",
    ),
    Secao(
        "FCI",
        "fci",
        "de investimento",
        "Investimentos (FCI)",
        "Investimentos",
        "Investimentos (FCI)",
        "serie-clara",
        "quadrado-claro",
    ),
)
CLASSES = frozenset(s.classe for s in SECOES)
SEM_CLASSIFICACAO = "Sem classificação"
VARIACAO = "Variação do caixa (FCO + FCF + FCI)"
TEXTO_FORA = (
    "Fora do fluxo de caixa, por não mexerem no caixa: depreciação, PDD e outras contas só de "
    "resultado."
)


# ---------- leitura ----------


def consulta(
    tabelas: Sequence[Any], ctx: ContextoConsolidado, saldos: Saldos
) -> tuple[sql.Composed, dict[str, Any]]:
    """O caixa somado por trecho, conta do plano e (sem conta do plano) conta do ERP.

    Trecho: o mês, partido nos dias com saldo cadastrado (o caixa antes e depois do saldo
    daquele dia fica em trechos diferentes; ver Fluxo.saldo_em)."""
    filtro = sql.SQL("(_empresa_id is null or _empresa_id = any(%(empresas)s))")
    return sql.SQL(
        """
        with f as ({linhas}),
        t as (
            select coalesce(
                       (select max(a) from unnest(%(ancoras)s::date[]) a
                         where a <= f.dia and a >= date_trunc('month', f.dia)),
                       date_trunc('month', f.dia)::date) as trecho,
                   conta,
                   case when conta is null then cod end as cod,
                   valor,
                   dia
              from f
             where dia is not null and valor is not null
        )
        select trecho, conta, cod, sum(valor) as valor, max(dia) as ultimo
          from t
         group by trecho, conta, cod
        """
    ).format(linhas=selecao(list(tabelas), CAMPOS_FLUXO, filtro)), parametros(
        ctx, ancoras=[d for d, _ in saldos]
    )


Classe = Literal["conta", "sem", "fora"]


def classificar(conta: str | None, cod: str | None, plano: Plano) -> tuple[Classe, str]:
    """Para onde vai o caixa da linha: ("conta", Id_Descritivo) entra no fluxo; ("sem",
    rótulo do rodapé) em "Sem classificação"; ("fora", Id_Descritivo) fica fora."""
    codigo = normalizar(conta)
    if codigo is None:
        return "sem", f"conta {cod} do ERP" if cod else "sem conta no ERP"
    c = plano.conta(codigo)
    if c is None:
        return "sem", f"{codigo}, fora do plano"
    if FILTRO_COMPETENCIA and c.competencia not in COMPETENCIA_CAIXA:
        return "fora", codigo
    if c.caixa not in CLASSES:
        return "sem", f"{codigo}, sem classe de caixa no plano"
    return "conta", codigo


type PorMes = dict[date, dict[str, Decimal]]


def _somar(destino: PorMes, mes: date, chave: str, valor: Decimal) -> None:
    contas = destino.setdefault(mes, {})
    contas[chave] = contas.get(chave, ZERO) + valor


@dataclass
class Fluxo:
    # mês → conta do plano → caixa (as contas que entram no fluxo)
    contas: PorMes = field(default_factory=dict)
    # mês → rótulo do rodapé → caixa ("Sem classificação")
    sem: PorMes = field(default_factory=dict)
    # mês → conta do plano → valor (competência CO: fora do fluxo e do saldo)
    fora: PorMes = field(default_factory=dict)
    # início do trecho (1º do mês ou dia com saldo cadastrado) → caixa (contas e sem)
    trechos: dict[date, Decimal] = field(default_factory=dict)
    saldos: Saldos = field(default_factory=list)
    # último dia com caixa
    ultimo_dia: date | None = None

    @cached_property
    def meses(self) -> list[date]:
        """Meses com caixa, do mais antigo ao mais recente."""
        return sorted(set(self.contas) | set(self.sem))

    def tem_caixa(self, mes: date) -> bool:
        return mes in self.contas or mes in self.sem

    def saldo_em(self, dia: date) -> Decimal | None:
        """Saldo no início do dia; None sem saldo cadastrado."""
        if not self.saldos:
            return None
        antes = [s for s in self.saldos if s[0] <= dia]
        base_dia, base = antes[-1] if antes else self.saldos[0]
        if base_dia <= dia:
            return base + sum((v for t, v in self.trechos.items() if base_dia <= t < dia), ZERO)
        return base - sum((v for t, v in self.trechos.items() if dia <= t < base_dia), ZERO)

    def abertura(self, mes: date) -> Decimal | None:
        return self.saldo_em(mes) if self.tem_caixa(mes) else None

    def fechamento(self, mes: date) -> Decimal | None:
        return self.saldo_em(mes_menos(mes, -1)) if self.tem_caixa(mes) else None

    def variacao(self, mes: date) -> Decimal | None:
        if not self.tem_caixa(mes):
            return None
        return sum(self.contas.get(mes, {}).values(), ZERO) + sum(
            self.sem.get(mes, {}).values(), ZERO
        )

    def sem_classificacao(self, mes: date) -> Decimal | None:
        valores = self.sem.get(mes)
        return sum(valores.values(), ZERO) if valores else None


def fluxo_das_linhas(linhas: Iterable[Mapping[str, Any]], saldos: Saldos, plano: Plano) -> Fluxo:
    f = Fluxo(saldos=sorted(saldos))
    for r in linhas:
        trecho: date = r["trecho"]
        valor: Decimal = r["valor"]
        mes = trecho.replace(day=1)
        classe, chave = classificar(r["conta"], r["cod"], plano)
        if classe == "fora":
            _somar(f.fora, mes, chave, valor)
            continue
        _somar(f.contas if classe == "conta" else f.sem, mes, chave, valor)
        f.trechos[trecho] = f.trechos.get(trecho, ZERO) + valor
        if f.ultimo_dia is None or r["ultimo"] > f.ultimo_dia:
            f.ultimo_dia = r["ultimo"]
    return f


# ---------- período ----------


@dataclass(frozen=True)
class Periodo:
    visao: str
    # do período (destaques, apoios, cascata)
    meses: tuple[date, ...]
    # da tabela e do gráfico de geração
    colunas: tuple[date, ...]
    # "setembro", "jan a set de 2026"
    texto: str
    # "Setembro", "De jan a set de 2026"
    titulo: str

    @property
    def total(self) -> bool:
        """A tabela tem a coluna do acumulado."""
        return self.visao in ("ano", "historico")


def _mes_ano_curto(mes: date) -> str:
    """abr-25"""
    return f"{MESES_CURTOS[mes.month - 1]}-{mes.year % 100:02d}"


def opcoes_visao(ref: date, primeiro: date) -> Opcoes:
    """`primeiro`: o primeiro mês com caixa. "Desde ..." só quando ele vem antes de janeiro
    do ano de `ref` (senão seria igual ao acumulado do ano)."""
    opcoes = [
        OpcaoFiltro(id="mes", rotulo=MESES[ref.month - 1]),
        OpcaoFiltro(id="ano", rotulo=f"Acumulado de {ref.year}"),
    ]
    if primeiro < date(ref.year, 1, 1):
        opcoes.append(
            OpcaoFiltro(id="historico", rotulo=f"Desde {nome_mes(primeiro)} de {primeiro.year}")
        )
    return Opcoes(
        filtro=FiltroPainel(id="visao", rotulo="Visão", opcoes=opcoes, padrao="mes"),
        valores={o.id: (o.id,) for o in opcoes},
    )


def periodo(visao: str, ref: date, primeiro: date) -> Periodo:
    """`primeiro`: o primeiro mês com caixa; nenhuma coluna antes dele (fora o próprio `ref`)."""
    if visao == "historico":
        meses = tuple(
            m for m in [*meses_antes(ref, _entre(primeiro, ref)), ref] if m >= primeiro
        ) or (ref,)
        texto = f"{_mes_ano_curto(meses[0])} a {_mes_ano_curto(ref)}"
        return Periodo(visao, meses, meses, texto, f"De {texto}")
    if visao == "ano":
        inicio = max(date(ref.year, 1, 1), primeiro)
        meses = tuple(m for m in [*meses_antes(ref, ref.month - 1), ref] if m >= inicio) or (ref,)
        if len(meses) > 1:
            intervalo = f"{MESES_CURTOS[meses[0].month - 1]} a {MESES_CURTOS[ref.month - 1]}"
            texto = f"{intervalo} de {ref.year}"
            return Periodo(visao, meses, meses, texto, f"De {texto}")
        return Periodo(visao, meses, meses, nome_mes(ref), MESES[ref.month - 1])
    colunas = tuple(
        m for m in [*meses_antes(ref, MESES_VISAO_MES - 1), ref] if m >= primeiro or m == ref
    )
    return Periodo(visao, (ref,), colunas, nome_mes(ref), MESES[ref.month - 1])


def _entre(de: date, ate: date) -> int:
    """Meses de `de` até `ate` (0 no mesmo mês; nunca negativo)."""
    return max(0, (ate.year - de.year) * 12 + ate.month - de.month)


def _soma(valores: Iterable[Decimal | None]) -> Decimal | None:
    vs = [v for v in valores if v is not None]
    return sum(vs, ZERO) if vs else None


def farol(valor: Decimal | None) -> Status | None:
    """FCO e variação: negativo é crítico, o resto saudável."""
    if valor is None:
        return None
    return "critico" if valor < 0 else "saudavel"


# ---------- os números ----------


@dataclass(frozen=True)
class Calculo:
    fluxo: Fluxo
    plano: Plano
    p: Periodo

    def secao(self, mes: date, classe: str, entrada: bool | None = None) -> Decimal | None:
        """Caixa da seção no mês (`entrada`: só as entradas ou só as saídas); None sem
        movimento nela."""
        return _soma(
            v
            for codigo, v in self.fluxo.contas.get(mes, {}).items()
            if self._da_secao(codigo, classe, entrada)
        )

    def da(self, classe: str, entrada: bool | None = None) -> Callable[[date], Decimal | None]:
        """`secao` como função do mês."""
        return lambda m: self.secao(m, classe, entrada)

    def _da_secao(self, codigo: str, classe: str, entrada: bool | None) -> bool:
        c = self.plano.conta(codigo)
        if c is None or c.caixa != classe:
            return False
        return entrada is None or (c.fator > 0) == entrada

    def no_periodo(self, f: Callable[[date], Decimal | None]) -> Decimal:
        return _soma(f(m) for m in self.p.meses) or ZERO

    @property
    def com_caixa(self) -> list[date]:
        """Meses do período com caixa."""
        return [m for m in self.p.meses if self.fluxo.tem_caixa(m)]

    @property
    def abertura(self) -> Decimal | None:
        meses = self.com_caixa
        return self.fluxo.abertura(meses[0]) if meses else None

    @property
    def fechamento(self) -> Decimal | None:
        meses = self.com_caixa
        return self.fluxo.fechamento(meses[-1]) if meses else None

    @property
    def parcial(self) -> date | None:
        """Último dia com caixa, quando o último mês do período ainda não terminou nele."""
        u = self.fluxo.ultimo_dia
        fim = self.p.meses[-1]
        if u is None or u.replace(day=1) != fim or u >= ultimo_dia_do_mes(fim):
            return None
        return u

    @property
    def titulo(self) -> str:
        u = self.parcial
        return self.p.titulo + (f", até {u:%d/%m}" if u else "")

    def anterior(self) -> tuple[tuple[date, ...], str] | None:
        """O período de comparação (o mês anterior; no acumulado, o mesmo período do ano
        anterior) e como citá-lo; None se falta caixa nele ou na visão "historico"."""
        if self.p.visao == "historico":
            return None
        if self.p.visao == "ano":
            meses = tuple(date(m.year - 1, m.month, 1) for m in self.p.meses)
            texto = f"no mesmo período de {self.p.meses[0].year - 1}"
        else:
            meses = (mes_menos(self.p.meses[0], 1),)
            texto = f"em {nome_mes(meses[0])}"
        if not all(self.fluxo.tem_caixa(m) for m in meses):
            return None
        return meses, texto


# ---------- topo ----------


def _contexto_anterior(k: Calculo, f: Callable[[date], Decimal | None]) -> str | None:
    a = k.anterior()
    if a is None:
        return None
    meses, texto = a
    return f"{moeda_sinal(_soma(f(m) for m in meses) or ZERO)} {texto}"


def destaques(k: Calculo) -> list[ItemValor]:
    itens: list[ItemValor] = []
    abertura, fechamento = k.abertura, k.fechamento
    if abertura is not None and fechamento is not None:
        primeiro = k.com_caixa[0]
        if k.p.visao == "mes":
            comeco = "o mês"
        elif k.p.visao == "historico":
            comeco = f"{nome_mes(primeiro)} de {primeiro.year}"
        elif primeiro.month == 1:
            comeco = "o ano"
        else:
            comeco = nome_mes(primeiro)
        itens.append(
            ItemValor(
                rotulo=f"Saldo final de {nome_mes(k.p.meses[-1])}",
                valor=brl(fechamento),
                contexto=f"começou {comeco} em {moeda_sinal(abertura)}",
                status="critico" if fechamento < 0 else None,
            )
        )
    fco = k.no_periodo(k.da("FCO"))
    variacao = k.no_periodo(k.fluxo.variacao)
    itens += [
        ItemValor(
            rotulo=SECOES[0].nome,
            valor=brl(fco),
            contexto=_contexto_anterior(k, k.da("FCO")),
            status=farol(fco),
        ),
        ItemValor(
            rotulo="Variação do caixa",
            valor=brl(variacao),
            contexto=_contexto_anterior(k, k.fluxo.variacao),
            status=farol(variacao),
        ),
    ]
    return itens


def apoios(k: Calculo) -> list[ItemValor]:
    return [
        ItemValor(
            rotulo="Entradas operacionais",
            valor=brl(k.no_periodo(k.da("FCO", True))),
        ),
        ItemValor(
            rotulo="Saídas operacionais",
            valor=brl(k.no_periodo(k.da("FCO", False))),
        ),
        *(ItemValor(rotulo=s.nome, valor=brl(k.no_periodo(k.da(s.classe)))) for s in SECOES[1:]),
        ItemValor(rotulo=SEM_CLASSIFICACAO, valor=brl(k.no_periodo(k.fluxo.sem_classificacao))),
    ]


def aviso(k: Calculo) -> Aviso:
    texto = [Trecho(texto="Caixa realizado, pela data de compensação. ")]
    sem = k.no_periodo(k.fluxo.sem_classificacao)
    if abs(sem) >= Decimal("0.5"):
        texto += [
            Trecho(texto=f"{moeda(sem)} de {k.p.texto}", destaque=True),
            Trecho(
                texto=" entraram ou saíram sem conta no plano e aparecem em “Sem "
                "classificação”, para o saldo final bater com o banco. "
            ),
        ]
    if FILTRO_COMPETENCIA:
        texto.append(Trecho(texto="Depreciação, PDD e outras contas só de resultado ficam fora. "))
    if not k.fluxo.saldos:
        texto.append(Trecho(texto="Sem saldo inicial cadastrado: só o movimento, sem os saldos."))
    # sem espaço sobrando no fim
    texto[-1] = Trecho(texto=texto[-1].texto.rstrip(), destaque=texto[-1].destaque)
    return Aviso(tom="info" if k.fluxo.saldos else "atencao", texto=texto)


# ---------- cartões ----------


def cartao_cascata(k: Calculo) -> CartaoGraficos | None:
    abertura, fechamento = k.abertura, k.fechamento
    if abertura is None or fechamento is None:
        return None
    efeitos = [
        *((s.curto, k.no_periodo(k.da(s.classe))) for s in SECOES),
        (SEM_CLASSIFICACAO, k.no_periodo(k.fluxo.sem_classificacao)),
    ]
    pontos = [abertura]
    for _, v in efeitos:
        pontos.append(pontos[-1] + v)
    piso = piso_da_escala(pontos)
    c = Cascata()
    c.total("Saldo inicial", abertura)
    for rotulo_passo, v in efeitos:
        c.passo(rotulo_passo, v)
    c.total("Saldo final", fechamento)
    return CartaoGraficos(
        id="saldo-cascata",
        tipo="graficos",
        largura=7,
        titulo="Do saldo inicial ao saldo final",
        subtitulo=(
            f"{k.titulo}. O que a operação, os financiamentos e os investimentos fizeram com o "
            "caixa"
        ),
        nota=None
        if piso is None
        else f"Escala a partir de {moeda(piso)} para mostrar as diferenças.",
        graficos=[
            GraficoCartao(
                grafico=GraficoCascata(tipo="cascata", unidade="brl", piso=piso, passos=c.passos)
            )
        ],
    )


def _titulo_mes(mes: date) -> str:
    return f"{MESES[mes.month - 1]} de {mes.year}"


def cartao_geracao(k: Calculo) -> CartaoGraficos | None:
    meses = [m for m in k.p.colunas if k.fluxo.tem_caixa(m)]
    if k.p.visao == "historico":
        meses = meses[-MESES_GRAFICO_HISTORICO:]
    if not meses:
        return None
    if k.p.visao == "ano":
        quando = f"de {k.p.texto}" if len(meses) > 1 else f"em {k.p.texto}"
    else:
        quando = f"nos últimos {len(meses)} meses" if len(meses) > 1 else f"em {k.p.texto}"
    return CartaoGraficos(
        id="geracao-caixa",
        tipo="graficos",
        largura=5,
        titulo="Geração de caixa realizada",
        subtitulo=f"Operação, financiamentos e investimentos {quando}",
        legenda=[ItemLegenda(nome=s.curto, marca=s.marca) for s in SECOES],
        graficos=[
            GraficoCartao(
                grafico=GraficoBarras(
                    tipo="barras",
                    unidade="brl",
                    grupos=[
                        GrupoBarras(
                            rotulo=MESES_CURTOS[m.month - 1],
                            titulo=_titulo_mes(m),
                            barras=[
                                Barra(
                                    nome=s.barra,
                                    valor=q(k.secao(m, s.classe) or ZERO),
                                    estilo=s.estilo,
                                )
                                for s in SECOES
                            ],
                        )
                        for m in meses
                    ],
                )
            )
        ],
    )


class Arvore:
    """As linhas da tabela, na ordem de exibição; cada uma com um valor por coluna (e o do
    acumulado no fim, na visão "ano")."""

    def __init__(self, k: Calculo) -> None:
        self.k = k
        self.linhas: list[LinhaArvoreMensal] = []

    def valores(self, f: Callable[[date], Decimal | None]) -> list[Decimal | None]:
        vs = [f(m) for m in self.k.p.colunas]
        return [*vs, _soma(vs)] if self.k.p.total else vs

    def linha(
        self,
        id_: str,
        nome: str,
        nivel: Literal[0, 1, 2, 3, 4],
        valores: list[Decimal | None],
        *,
        pai: str | None = None,
        final: bool = False,
        fundos: bool = False,
    ) -> None:
        self.linhas.append(
            LinhaArvoreMensal(
                id=id_,
                nome=nome,
                nivel=nivel,
                pai=pai,
                final=final,
                valores=[None if v is None else q(v, 0) for v in valores],
                fundos=[farol(v) for v in valores] if fundos else None,
            )
        )

    def saldo(self, id_: str, nome: str, inicio: bool) -> None:
        k, f = self.k, self.k.fluxo
        por_mes = f.abertura if inicio else f.fechamento
        vs = [por_mes(m) for m in k.p.colunas]
        if k.p.total:
            vs.append(k.abertura if inicio else k.fechamento)
        self.linha(id_, nome, 0, vs, final=not inicio)

    def contas(self, s: Secao, entrada: bool) -> None:
        """Entradas ou saídas da seção, abertas por grupo, subgrupo e conta do plano."""
        k, plano = self.k, self.k.plano
        codigos = sorted(
            {
                codigo
                for m in k.p.colunas
                for codigo in k.fluxo.contas.get(m, {})
                if k._da_secao(codigo, s.classe, entrada)
            }
        )
        if not codigos:
            return
        lado = "ent" if entrada else "sai"
        topo = f"{s.id}-{lado}"
        nome = f"{'Entradas' if entrada else 'Saídas'} {s.adjetivo}"
        self.linha(topo, nome, 1, self.valores(self.das(codigos)))
        grupos: dict[str, dict[str, list[str]]] = {}
        for codigo in codigos:
            c = plano.conta(codigo)
            if c is None:  # só contas do plano chegam aqui (classificar)
                continue
            g = c.id_nivel_1 or codigo.split(".")[0]
            grupos.setdefault(g, {}).setdefault(c.id_nivel_2 or g, []).append(codigo)
        for g, subgrupos in sorted(grupos.items()):
            id_g = f"{topo}-{g}"
            todos = [c for cs in subgrupos.values() for c in cs]
            nome_g = plano.nome_nivel_1(g)
            self.linha(
                id_g,
                rotulo(nome_g) if nome_g else f"Grupo {g}",
                2,
                self.valores(self.das(todos)),
                pai=topo,
            )
            for sg, cs in sorted(subgrupos.items()):
                id_sg = f"{id_g}-{sg}"
                self.linha(
                    id_sg,
                    plano.nome(sg) or f"Conta {sg}",
                    3,
                    self.valores(self.das(cs)),
                    pai=id_g,
                )
                for codigo in cs:
                    self.linha(
                        f"{id_sg}-{codigo}",
                        plano.nome(codigo) or f"Conta {codigo}",
                        4,
                        self.valores(self.das([codigo])),
                        pai=id_sg,
                    )

    def das(self, codigos: Sequence[str]) -> Callable[[date], Decimal | None]:
        """Soma das `codigos` como função do mês."""
        return lambda m: self._de(m, codigos)

    def _de(self, mes: date, codigos: Sequence[str]) -> Decimal | None:
        contas = self.k.fluxo.contas.get(mes, {})
        return _soma(contas.get(c) for c in codigos)

    def montar(self) -> list[LinhaArvoreMensal]:
        k = self.k
        self.saldo("saldo-inicial", "Saldo inicial", inicio=True)
        for s in SECOES:
            self.contas(s, entrada=True)
            self.contas(s, entrada=False)
            self.linha(
                s.id,
                s.nome,
                0,
                self.valores(k.da(s.classe)),
                fundos=s.classe == "FCO",
            )
        sem = self.valores(k.fluxo.sem_classificacao)
        if any(v is not None for v in sem):
            self.linha("sem-classificacao", SEM_CLASSIFICACAO, 1, sem)
        self.linha("variacao", VARIACAO, 0, self.valores(k.fluxo.variacao), fundos=True)
        self.saldo("saldo-final", "Saldo final", inicio=False)
        return self.linhas


def _citar(valores: Mapping[str, Decimal], nome: Callable[[str], str]) -> str:
    """As CITADAS de maior valor (em módulo), com o valor; "e mais N" no fim."""
    itens = sorted(valores.items(), key=lambda kv: (-abs(kv[1]), kv[0]))
    texto = "; ".join(f"{nome(c)} ({moeda_sinal(v)})" for c, v in itens[:CITADAS])
    resto = len(itens) - CITADAS
    return texto + (f"; e mais {resto}" if resto > 0 else "")


def _no_periodo(por_mes: PorMes, meses: Iterable[date]) -> dict[str, Decimal]:
    total: dict[str, Decimal] = {}
    for m in meses:
        for chave, v in por_mes.get(m, {}).items():
            total[chave] = total.get(chave, ZERO) + v
    return {c: v for c, v in total.items() if abs(v) >= Decimal("0.5")}


def rodape(k: Calculo) -> list[str]:
    linhas: list[str] = []
    if k.plano.vazio:
        linhas.append(
            "Sem o plano de contas da LANN cadastrado: todo o caixa fica em “Sem classificação”."
        )
    sem = _no_periodo(k.fluxo.sem, k.p.colunas)
    if sem:
        linhas.append(
            "Sem classificação: caixa sem conta no plano de contas da LANN ("
            + _citar(sem, lambda c: c)
            + ")."
        )
    if not FILTRO_COMPETENCIA:
        return linhas
    fora = _no_periodo(k.fluxo.fora, k.p.colunas)
    texto_fora = TEXTO_FORA
    if fora:

        def nome(codigo: str) -> str:
            n = k.plano.nome(codigo)
            return f"{codigo} {n}" if n else codigo

        texto_fora += " Lançado nelas nos meses da tabela: " + _citar(fora, nome) + "."
    linhas.append(texto_fora)
    return linhas


def cartao_arvore(k: Calculo) -> CartaoArvoreMensal:
    colunas = [ColunaArvore(rotulo=_mes_ano_curto(m), titulo=_titulo_mes(m)) for m in k.p.colunas]
    if k.p.visao == "historico":
        rotulo_total = f"Acumulado desde {_mes_ano_curto(k.p.meses[0])}"
        colunas.append(ColunaArvore(rotulo=rotulo_total, total=True))
    elif k.p.total:
        colunas.append(ColunaArvore(rotulo=f"Acumulado {k.p.meses[-1].year}", total=True))
    com_plano = not k.plano.vazio
    return CartaoArvoreMensal(
        id="fluxo-realizado",
        tipo="arvore-mensal",
        largura=12,
        titulo="Fluxo de caixa realizado",
        subtitulo="O que de fato entrou e saiu, mês a mês.",
        rotulo_conta="Conta (R$)",
        colunas=colunas,
        escala=Escala(unidade="qtd", casas=0),
        nivel_inicial=2 if com_plano else 1,
        niveis=[1, 2, 3, 4] if com_plano else [1],
        linhas=Arvore(k).montar(),
        rodape=rodape(k),
    )


# ---------- a tela ----------


def conteudo(
    fluxo: Fluxo,
    plano: Plano,
    escolhido: date | None,
    pedidos: Mapping[str, str | None],
    entrada: projecao.Entrada | None = None,
) -> Conteudo:
    """A tela a partir do caixa já lido (sem banco). Com `entrada`, a projeção de 60 dias
    (projecao.py) vem antes do realizado, com o filtro `projecao`."""
    if not fluxo.meses:
        return _vazio(pedidos)
    ref = escolhido or fluxo.meses[-1]
    visao_o = opcoes_visao(ref, fluxo.meses[0])
    visao = (escolher(visao_o, pedidos.get("visao")) or ("mes",))[0]
    k = Calculo(fluxo=fluxo, plano=plano, p=periodo(visao, ref, fluxo.meses[0]))
    filtros = [visao_o.filtro]
    cartoes: list[Cartao | None] = []
    if entrada is not None:
        projecao_o = projecao.opcoes_projecao()
        escolha = escolher(projecao_o, pedidos.get("projecao"))
        vista = projecao.PROVISOES if escolha == (projecao.PROVISOES,) else projecao.LANCADOS
        filtros.append(projecao_o.filtro)
        cartoes += projecao.cartoes(projecao.Projecao(entrada), vista)
    else:
        escolher(None, pedidos.get("projecao"))
    cartoes += [cartao_cascata(k), cartao_geracao(k), cartao_arvore(k)]
    return Conteudo(
        filtros=filtros,
        aviso=aviso(k),
        destaques=destaques(k),
        apoios=apoios(k),
        cartoes=em_fileiras([c for c in cartoes if c is not None]),
    )


def montar(
    ctx: ContextoConsolidado,
    escolhido: date | None,
    carga: UltimaCarga,
    pedidos: Mapping[str, str | None],
    a_decidir: bool = False,
) -> Conteudo:
    conn = ctx.sessao.conn
    tabelas = tabelas_do_cliente(conn, ctx.cliente_id, "f_fluxo_", COLUNAS_FLUXO)
    if not tabelas:
        return _vazio(pedidos)
    # o saldo e o plano vêm da pré-leitura (sem ida ao banco); o caixa, numa consulta só
    saldos = ler_saldos(conn, ctx.cliente_id)
    plano = ler_plano(conn)
    linhas = conn.execute(*consulta(tabelas, ctx, saldos)).fetchall()
    fluxo = fluxo_das_linhas(linhas, saldos, plano)
    entrada: projecao.Entrada | None = None
    if fluxo.ultimo_dia is not None:
        # a projeção parte do fim do último dia com caixa; os títulos, numa ida a mais
        d0 = fluxo.ultimo_dia
        entrada = projecao.ler(
            ctx,
            tabelas,
            fluxo.contas,
            fluxo.meses,
            d0,
            fluxo.saldo_em(d0 + timedelta(days=1)),
            plano,
            carga.data_snapshot,
        )
    return conteudo(fluxo, plano, escolhido, pedidos, entrada)


def _vazio(pedidos: Mapping[str, str | None]) -> Conteudo:
    """Sem caixa: nada a mostrar, e nenhum filtro próprio vale."""
    for valor in pedidos.values():
        escolher(None, valor)
    return Conteudo()

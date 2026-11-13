"""FIN-04 — projeção de caixa de 60 dias. REGRAS APROVADAS pelo usuário em 09/11/2026 (ver
TODO.md, "Projeção de caixa"); os grupos e as escolhas salvas estão em provisoes.py.

Partida: o saldo final da DFC (fluxo_caixa.py) no último dia com caixa, D0. A projeção vai
de D0+1 a D0+60; os filtros `mes` e `visao` não mudam nada aqui, e a empresa filtrada (ou
o cliente inteiro) vale para os títulos, o realizado e as escolhas. Títulos carregados mais
de 3 dias úteis depois de D0: nota no saldo projetado.

Lançado (títulos em aberto em D0, pelas regras de app/telas/visao_geral/financeiro.py):
- a receber: no vencimento + 2 dias úteis (D+2); o que está em compensação (vencido, mas
  dentro do D+2) em D0+1; o vencido além disso fica fora (a inadimplência fica para a V2);
- a pagar: no vencimento, levado ao próximo dia útil; o vencido em D0+1. O grupo de
  provisão vem da conta do ERP pelo de-para (cfg.conta_dre.id_descritivo).

Provisões (grupos ligados): média mensal dos N meses fechados antes do mês de D0+1 com
caixa, pelo realizado da DFC (data de compensação), por grupo. O valor do mês é o lado do
grupo: o que entrou nos recebimentos e o que saiu nas saídas (mês em que o grupo mais
recebeu do que pagou conta zero). J1 = D0+1 a D0+30, J2 = D0+31 a D0+60; em cada uma,
max(0, média − lançado do grupo na janela). Valor personalizado V: J1 = V e J2 = max(0,
V + lançado em J1 − lançado em J2). Distribuição nos dias pelo perfil do dia do mês do
grupo nos meses da média (29, 30 e 31 caem no último dia do mês que não os tem), levada ao
próximo dia útil (no fim do horizonte, fica no último dia); com menos de 3 meses, por igual
nos dias úteis da janela.

Saldo do dia: o anterior + entradas − saídas do lançado (e, na visão "provisoes", as
provisões). Caixa mínimo: financeiro.caixa_minimo em cfg.parametro_cliente (número); sem
ele, sem a linha de referência, e o farol da semana só olha o zero.

Pago contra lançado: o pago dos títulos a pagar nos até 6 meses fechados (pela data de
pagamento; financeiro.media_paga) contra o lançado de J1 e J2, com farol.pagar_lancado.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from functools import cached_property
from typing import Any, Literal

from psycopg import sql

from app.core.cache import existe
from app.core.db import Conexao, em_uma_ida
from app.farol.parametro import Farol, farol, parametros_do_cliente, vigente
from app.seguranca.acesso import ContextoConsolidado
from app.telas.calendario import feriados, mais_dias_uteis, ultimo_dia_do_mes
from app.telas.contrato import Status
from app.telas.moldura import MESES
from app.telas.paineis.base import Opcoes, Tipo, meses_antes, parametros, selecao
from app.telas.paineis.contrato import (
    Barra,
    BarraHorizontal,
    Cartao,
    CartaoBarrasHorizontais,
    CartaoGraficos,
    CartaoOrigemProjecao,
    CartaoProvisoes,
    CartaoTabela,
    Celula,
    Chamada,
    ColunasProvisoes,
    ColunaTabela,
    Escala,
    EtiquetaOrigem,
    FiltroPainel,
    GraficoBarras,
    GraficoCartao,
    GraficoLinhas,
    GrupoBarras,
    GrupoOrigem,
    ItemLegenda,
    ItemOrigem,
    LinhaProvisao,
    LinhaTabela,
    Marcador,
    OpcaoFiltro,
    ParteOrigem,
    QuadroCartao,
    ReferenciaBarras,
    ReferenciaLinha,
    SerieLinha,
)
from app.telas.paineis.formato import SEMANA, brl, q
from app.telas.paineis.provisoes import (
    DIVIDA,
    GRUPOS,
    PERIODOS,
    POR_ID,
    RECEBIMENTOS,
    EscolhaGrupo,
    Escolhas,
    grupo_da_conta,
    ler_escolhas,
)
from app.telas.plano import Plano, normalizar
from app.telas.visao_geral.contra_referencia import MESES_CURTOS
from app.telas.visao_geral.contrato import Trecho
from app.telas.visao_geral.financeiro import (
    COLUNAS_PAGAR,
    COLUNAS_RECEBER,
    DIAS_COMPENSACAO,
    media_paga,
    meses_pagos,
    ultimo_vencimento_vencido,
)
from app.telas.visao_geral.texto import moeda, moeda_sinal, numero
from app.telas.visao_geral.vendas import (
    TabelaDoCliente,
    de_todas,
    filtro_empresa,
    mes_menos,
    tabelas_do_cliente,
)

ZERO = Decimal(0)
CEM = Decimal(100)
DIAS = 60
JANELA = 30
# títulos carregados mais de tantos dias úteis depois de D0: nota no saldo projetado
DIAS_UTEIS_NOTA = 3
# com menos meses na média, a provisão vai por igual nos dias úteis
MESES_PERFIL = 3
# meses da série da inadimplência (o a receber vencido ÷ em aberto em cada fim de mês)
MESES_INADIMPLENCIA = 12
CHAVE_CAIXA_MINIMO = "financeiro.caixa_minimo"

Visao = Literal["lancados", "provisoes"]
LANCADOS: Visao = "lancados"
PROVISOES: Visao = "provisoes"

# um dia útil para a distribuição e o D+2: segunda a sexta fora dos feriados
type Feriados = frozenset[date]


def opcoes_projecao() -> Opcoes:
    opcoes = [
        OpcaoFiltro(id=LANCADOS, rotulo="Só lançados"),
        OpcaoFiltro(id=PROVISOES, rotulo="Lançados + provisões"),
    ]
    return Opcoes(
        filtro=FiltroPainel(
            id="projecao",
            rotulo="Projeção",
            opcoes=opcoes,
            padrao=LANCADOS,
            cartao="saldo-projetado",
        ),
        valores={o.id: (o.id,) for o in opcoes},
    )


# ---------- entrada ----------


@dataclass(frozen=True)
class Titulos:
    """Os títulos lidos do banco (ver ler): em aberto em D0, pagos e a série do a receber."""

    # vencimento → valor do a receber em aberto (o vencido além do D+2 já vem de fora)
    receber: Mapping[date, Decimal] = field(default_factory=dict)
    # (vencimento, Id_Descritivo normalizado ou None) → valor do a pagar em aberto; o vencido
    # vem com o vencimento em D0
    pagar: Mapping[tuple[date, str | None], Decimal] = field(default_factory=dict)
    # mês do pagamento → pago (a pagar), nos meses fechados
    pagos: Mapping[date, Decimal] = field(default_factory=dict)
    # fim de mês → (vencido, em aberto) do a receber
    serie_receber: Mapping[date, tuple[Decimal, Decimal]] = field(default_factory=dict)
    tem_receber: bool = False
    tem_pagar: bool = False


@dataclass(frozen=True)
class Entrada:
    """Tudo de que a projeção depende, já lido (as contas não vão ao banco)."""

    d0: date
    # saldo no fim de D0; None sem saldo inicial cadastrado (a linha parte de zero)
    saldo: Decimal | None
    # realizado da DFC: mês → conta do plano → caixa (fluxo_caixa.Fluxo.contas)
    contas: Mapping[date, Mapping[str, Decimal]]
    # meses com caixa no realizado
    meses_com_caixa: frozenset[date]
    plano: Plano
    titulos: Titulos
    # dia do mês (1 a 31) → conta do plano → caixa, somado nos meses da média
    por_dia: Mapping[int, Mapping[str, Decimal]]
    escolhas: Escolhas
    feriados: Feriados
    farol_pagar: Farol
    minimo: Decimal | None = None
    # data da carga dos títulos
    data_titulos: date | None = None
    editavel: bool = True

    @property
    def inicio(self) -> date:
        return self.d0 + timedelta(days=1)

    @property
    def fim(self) -> date:
        return self.d0 + timedelta(days=DIAS)

    @property
    def mes_aberto(self) -> date:
        """O mês de D0+1: os meses da média são os fechados antes dele."""
        return self.inicio.replace(day=1)

    def meses_media(self, n: int) -> list[date]:
        """Os `n` meses fechados antes de `mes_aberto` que têm caixa no realizado."""
        return [m for m in meses_antes(self.mes_aberto, n) if m in self.meses_com_caixa]


def util(dia: date, fer: Feriados) -> bool:
    return dia.weekday() < 5 and dia not in fer


def proximo_util(dia: date, fer: Feriados) -> date:
    while not util(dia, fer):
        dia += timedelta(days=1)
    return dia


def _lado(valor: Decimal, grupo: str) -> Decimal:
    """O valor do grupo no sentido dele: o que entrou (recebimentos) ou o que saiu."""
    return max(ZERO, valor if grupo == RECEBIMENTOS else -valor)


def realizado_por_grupo(
    contas: Mapping[date, Mapping[str, Decimal]], plano: Plano
) -> dict[date, dict[str, Decimal]]:
    """Mês → grupo → caixa (com o sinal do caixa), pelas contas do plano."""
    saida: dict[date, dict[str, Decimal]] = {}
    for mes, cs in contas.items():
        for codigo, v in cs.items():
            c = plano.conta(codigo)
            if c is None:
                continue
            g = grupo_da_conta(codigo, c, c.fator > 0)
            if g is not None:
                grupos = saida.setdefault(mes, {})
                grupos[g] = grupos.get(g, ZERO) + v
    return saida


def perfil_por_grupo(
    por_dia: Mapping[int, Mapping[str, Decimal]], plano: Plano
) -> dict[str, dict[int, Decimal]]:
    """Grupo → dia do mês → peso (o caixa do grupo naquele dia, no sentido dele)."""
    somas: dict[str, dict[int, Decimal]] = {}
    for dia, cs in por_dia.items():
        for codigo, v in cs.items():
            c = plano.conta(codigo)
            if c is None:
                continue
            g = grupo_da_conta(codigo, c, c.fator > 0)
            if g is not None:
                dias = somas.setdefault(g, {})
                dias[dia] = dias.get(dia, ZERO) + v
    return {
        g: {d: _lado(v, g) for d, v in dias.items() if _lado(v, g) > 0} for g, dias in somas.items()
    }


def distribuir(
    total: Decimal,
    janela: Sequence[date],
    perfil: Mapping[int, Decimal] | None,
    fer: Feriados,
    fim: date,
) -> dict[date, Decimal]:
    """`total` nos dias da `janela`: pelo `perfil` (dia do mês → peso; os dias que o mês não
    tem caem no último dia dele) ou, sem perfil, por igual nos dias úteis; cada parte vai
    para o próximo dia útil, sem passar de `fim`."""
    if total <= 0 or not janela:
        return {}
    pesos: dict[date, Decimal] = {}
    if perfil:
        for d in janela:
            w = perfil.get(d.day, ZERO)
            if d == ultimo_dia_do_mes(d):
                w += sum((perfil.get(k, ZERO) for k in range(d.day + 1, 32)), ZERO)
            if w > 0:
                pesos[d] = w
    if not pesos:
        uteis = [d for d in janela if util(d, fer)] or list(janela)
        pesos = {d: Decimal(1) for d in uteis}
    soma = sum(pesos.values(), ZERO)
    saida: dict[date, Decimal] = {}
    for d, w in pesos.items():
        alvo = min(proximo_util(d, fer), fim)
        saida[alvo] = saida.get(alvo, ZERO) + total * w / soma
    return saida


@dataclass(frozen=True)
class LinhaGrupo:
    """Um grupo de provisão: média, lançado e provisão em cada janela."""

    id: str
    escolha: EscolhaGrupo
    media: Decimal
    lancado1: Decimal
    lancado2: Decimal

    @property
    def sugerida(self) -> Decimal:
        return max(ZERO, self.media - self.lancado1)

    @property
    def usado(self) -> Decimal:
        return self.escolha.valor if self.escolha.valor is not None else self.sugerida

    @property
    def provisao1(self) -> Decimal:
        return self.usado if self.escolha.ligado else ZERO

    @property
    def provisao2(self) -> Decimal:
        if not self.escolha.ligado:
            return ZERO
        if self.escolha.valor is not None:
            return max(ZERO, self.escolha.valor + self.lancado1 - self.lancado2)
        return max(ZERO, self.media - self.lancado2)

    @property
    def provisao(self) -> Decimal:
        return self.provisao1 + self.provisao2


# ---------- as contas ----------


class Projecao:
    """Os 60 dias, a partir da entrada (sem banco)."""

    def __init__(self, e: Entrada) -> None:
        self.e = e
        self.dias = [e.inicio + timedelta(days=i) for i in range(DIAS)]
        self.janela1 = self.dias[:JANELA]
        self.janela2 = self.dias[JANELA:]

    # ----- lançado -----

    @cached_property
    def receber(self) -> dict[date, Decimal]:
        """Dia → a receber lançado."""
        e, fer = self.e, self.e.feriados
        saida: dict[date, Decimal] = {}
        for venc, v in e.titulos.receber.items():
            previsto = mais_dias_uteis(venc, DIAS_COMPENSACAO, fer)
            if venc >= e.d0:
                dia = previsto
            elif previsto > e.d0:  # em compensação
                dia = e.inicio
            else:
                continue
            dia = max(dia, e.inicio)
            if dia <= e.fim:
                saida[dia] = saida.get(dia, ZERO) + v
        return saida

    @cached_property
    def pagar(self) -> dict[tuple[date, str | None], Decimal]:
        """(dia, grupo ou None) → a pagar lançado."""
        e, fer = self.e, self.e.feriados
        saida: dict[tuple[date, str | None], Decimal] = {}
        for (venc, codigo), v in e.titulos.pagar.items():
            dia = e.inicio if venc <= e.d0 else proximo_util(venc, fer)
            if dia > e.fim:
                continue
            grupo = grupo_da_conta(codigo, e.plano.conta(codigo) if codigo else None, False)
            saida[(dia, grupo)] = saida.get((dia, grupo), ZERO) + v
        return saida

    def _receber_em(self, dias: Iterable[date]) -> Decimal:
        ds = set(dias)
        return sum((v for d, v in self.receber.items() if d in ds), ZERO)

    def _pagar_em(
        self, dias: Iterable[date], grupo: str | None = None, todos: bool = True
    ) -> Decimal:
        ds = set(dias)
        return sum(
            (v for (d, g), v in self.pagar.items() if d in ds and (todos or g == grupo)), ZERO
        )

    def lancado(self, grupo: str, dias: Sequence[date]) -> Decimal:
        if grupo == RECEBIMENTOS:
            return self._receber_em(dias)
        return self._pagar_em(dias, grupo, todos=False)

    # ----- provisões -----

    @cached_property
    def meses_media(self) -> list[date]:
        return self.e.meses_media(self.e.escolhas.meses)

    @cached_property
    def grupos(self) -> list[LinhaGrupo]:
        realizado = realizado_por_grupo(self.e.contas, self.e.plano)
        meses = self.meses_media
        linhas = []
        for g in GRUPOS:
            media = (
                sum((_lado(realizado.get(m, {}).get(g.id, ZERO), g.id) for m in meses), ZERO)
                / len(meses)
                if meses
                else ZERO
            )
            linhas.append(
                LinhaGrupo(
                    id=g.id,
                    escolha=self.e.escolhas.do_grupo(g.id),
                    media=media,
                    lancado1=self.lancado(g.id, self.janela1),
                    lancado2=self.lancado(g.id, self.janela2),
                )
            )
        return linhas

    @cached_property
    def perfis(self) -> dict[str, dict[int, Decimal]]:
        if len(self.meses_media) < MESES_PERFIL:
            return {}
        return perfil_por_grupo(self.e.por_dia, self.e.plano)

    @cached_property
    def provisao(self) -> dict[str, dict[date, Decimal]]:
        """Grupo → dia → provisão (só os grupos ligados com provisão)."""
        e = self.e
        saida: dict[str, dict[date, Decimal]] = {}
        for linha in self.grupos:
            perfil = self.perfis.get(linha.id)
            dias: dict[date, Decimal] = {}
            for total, janela in ((linha.provisao1, self.janela1), (linha.provisao2, self.janela2)):
                for d, v in distribuir(total, janela, perfil, e.feriados, e.fim).items():
                    dias[d] = dias.get(d, ZERO) + v
            if dias:
                saida[linha.id] = dias
        return saida

    # ----- por dia -----

    def entradas(self, dia: date, visao: Visao) -> Decimal:
        v = self.receber.get(dia, ZERO)
        if visao == PROVISOES:
            v += self.provisao.get(RECEBIMENTOS, {}).get(dia, ZERO)
        return v

    def saidas(self, dia: date, visao: Visao) -> Decimal:
        v = sum((x for (d, _), x in self.pagar.items() if d == dia), ZERO)
        if visao == PROVISOES:
            v += sum(
                (ds.get(dia, ZERO) for g, ds in self.provisao.items() if g != RECEBIMENTOS), ZERO
            )
        return v

    def saldos(self, visao: Visao) -> list[Decimal]:
        """Saldo no fim de cada dia."""
        saldo = self.e.saldo or ZERO
        saida = []
        for d in self.dias:
            saldo += self.entradas(d, visao) - self.saidas(d, visao)
            saida.append(saldo)
        return saida

    # ----- pago contra lançado -----

    @cached_property
    def pagos(self) -> dict[date, dict[str, Decimal]]:
        return {m: {"valor": v} for m, v in self.e.titulos.pagos.items()}

    @cached_property
    def media_paga(self) -> Decimal | None:
        m = media_paga(self.pagos, self.e.inicio)
        return m if m is not None and m > 0 else None

    @cached_property
    def pagar1(self) -> Decimal:
        return self._pagar_em(self.janela1)

    @cached_property
    def pagar2(self) -> Decimal:
        return self._pagar_em(self.janela2)

    def pct(self, lancado: Decimal) -> Decimal | None:
        return None if self.media_paga is None else lancado / self.media_paga * CEM

    @cached_property
    def status1(self) -> Status | None:
        p = self.pct(self.pagar1)
        return None if p is None else self.e.farol_pagar.status(p)

    # ----- inadimplência (V2: só na origem, riscada) -----

    @cached_property
    def inadimplencia(self) -> Decimal | None:
        """Média de vencido ÷ em aberto do a receber nos fins de mês, em %; None sem série."""
        pontos = [v / a * CEM for v, a in self.e.titulos.serie_receber.values() if a > 0]
        return sum(pontos, ZERO) / len(pontos) if pontos else None


# ---------- textos ----------


def _dia(d: date) -> str:
    return f"{d:%d/%m}"


def _periodo_texto(periodo: str) -> str:
    """ "do último mês", "de 6 meses"."""
    n = PERIODOS[periodo][1]
    return "do último mês" if n == 1 else f"de {n} meses"


def _texto_status(status: Status, f: Farol) -> str:
    atencao, critico = numero(f.faixa.atencao), numero(f.faixa.critico)
    if status == "critico":
        return f"Crítico: abaixo de {critico}% faltam muitas contas no sistema"
    if status == "atencao":
        return f"Atenção: abaixo de {atencao}% o caixa tende a parecer melhor do que é"
    return f"Saudável: a partir de {atencao}% o lançado já se aproxima da média"


# ---------- cartões ----------


def cartao_saldo(p: Projecao, visao: Visao) -> CartaoGraficos:
    e = p.e
    saldos = p.saldos(visao)
    chamada: Chamada | None = None
    pct1 = p.pct(p.pagar1)
    if p.status1 in ("atencao", "critico") and pct1 is not None:
        chamada = Chamada(
            status=p.status1,
            texto=[
                Trecho(texto="Contas a pagar incompletas: ", destaque=True),
                Trecho(
                    texto=(
                        f"só {numero(pct1)}% da média paga está lançada para os próximos "
                        f"{JANELA} dias. Em “Só lançados”, o caixa parece melhor do que vai "
                        "ser; “Lançados + provisões” completa com a média."
                    )
                ),
            ],
        )
    if visao == PROVISOES:
        subtitulo = (
            "Títulos lançados mais as provisões pela média "
            f"{_periodo_texto(e.escolhas.periodo)}, nos dias em que cada grupo costuma ser pago."
        )
    else:
        subtitulo = "Só títulos a receber e a pagar já lançados no sistema."
        if chamada is not None:
            subtitulo += " Como faltam saídas (veja o alerta), a linha tende a subir."
    marcadores = [Marcador(serie=0, i=0, rotulo=f"{moeda_sinal(saldos[0])} em {_dia(p.dias[0])}")]
    if visao == PROVISOES:
        i = min(range(len(saldos)), key=lambda k: saldos[k])
        if i != 0:
            marcadores.append(
                Marcador(
                    serie=0,
                    i=i,
                    rotulo=f"Menor saldo {moeda_sinal(saldos[i])} em {_dia(p.dias[i])}",
                )
            )
    notas: list[str] = []
    if e.saldo is None:
        notas.append("Sem saldo inicial cadastrado: a linha parte de zero.")
    if e.data_titulos is not None and e.data_titulos > mais_dias_uteis(
        e.d0, DIAS_UTEIS_NOTA, set(e.feriados)
    ):
        notas.append(
            f"O caixa realizado vai até {_dia(e.d0)}; os títulos, até {_dia(e.data_titulos)}."
        )
    return CartaoGraficos(
        id="saldo-projetado",
        tipo="graficos",
        largura=12,
        titulo=f"Saldo projetado dia a dia, {DIAS} dias",
        subtitulo=subtitulo,
        chamada=chamada,
        nota=" ".join(notas) or None,
        graficos=[
            GraficoCartao(
                alto=True,
                grafico=GraficoLinhas(
                    tipo="linhas",
                    unidade="brl",
                    rotulos=[_dia(d) for d in p.dias],
                    titulos=[f"{_dia(d)} · {SEMANA[d.weekday()]}" for d in p.dias],
                    series=[
                        SerieLinha(
                            nome="Saldo projetado",
                            valores=[q(v, 0) for v in saldos],
                            estilo="principal",
                            area=True,
                        )
                    ],
                    referencia=(
                        ReferenciaLinha(
                            valor=q(e.minimo, 0),
                            rotulo=f"Caixa mínimo {moeda(e.minimo)}",
                            estilo="minimo",
                        )
                        if e.minimo is not None
                        else None
                    ),
                    marcadores=marcadores,
                    piso=ZERO,
                ),
            )
        ],
    )


def _item(nome: str, valor: Decimal, estilo: Literal["lancado", "nao-lancado"]) -> ItemOrigem:
    rotulo = "lançado" if estilo == "lancado" else "não lançado"
    return ItemOrigem(
        nome=nome, valor=brl(valor), etiqueta=EtiquetaOrigem(rotulo=rotulo, estilo=estilo)
    )


def cartao_origem(p: Projecao, visao: Visao) -> CartaoOrigemProjecao | None:
    receber = p._receber_em(p.dias)
    divida = p._pagar_em(p.dias, DIVIDA, todos=False)
    pagar = p._pagar_em(p.dias) - divida
    provisoes = [(POR_ID[g.id], g.provisao) for g in p.grupos if g.provisao > 0]
    riscar = True if visao == LANCADOS else None

    entradas: list[ItemOrigem] = []
    if receber > 0:
        entradas.append(_item("Títulos a receber já emitidos", receber, "lancado"))
        taxa = p.inadimplencia
        if taxa is not None and taxa > 0:
            entradas.append(
                ItemOrigem(
                    nome=f"Inadimplência esperada, pelo histórico ({numero(taxa, 1)}%)",
                    valor=brl(-receber * taxa / CEM),
                    etiqueta=EtiquetaOrigem(rotulo="V2", estilo="futuro"),
                    riscado=True,
                )
            )
    saidas: list[ItemOrigem] = []
    if pagar > 0:
        saidas.append(_item("Títulos a pagar e compras lançados", -pagar, "lancado"))
    if divida > 0:
        saidas.append(_item("Parcelas de empréstimos", -divida, "lancado"))
    for g, valor in provisoes:
        item = _item(g.origem, valor if g.entrada else -valor, "nao-lancado")
        item = item.model_copy(update={"riscado": riscar})
        (entradas if g.entrada else saidas).append(item)

    lancado = receber + pagar + divida
    nao_lancado = sum((v for _, v in provisoes), ZERO)
    if lancado + nao_lancado <= 0:
        return None
    efeito = sum((v if g.entrada else -v for g, v in provisoes), ZERO)
    texto: list[Trecho] | None = None
    if nao_lancado > 0:
        if visao == PROVISOES:
            depois = " de efeito líquido pela média, já somado ao saldo desta visão."
        elif efeito < 0:
            depois = (
                " de efeito líquido pela média. Por isso o caixa “só lançados” sobe; a visão "
                "com provisões completa esse valor."
            )
        else:
            depois = (
                " de efeito líquido pela média. Por isso o caixa “só lançados” fica abaixo do "
                "que deve ser; a visão com provisões completa esse valor."
            )
        texto = [
            Trecho(texto="Ainda não lançado: "),
            Trecho(texto=moeda_sinal(efeito), destaque=True),
            Trecho(texto=depois),
        ]
    total = lancado + nao_lancado
    if visao == LANCADOS:
        subtitulo = (
            f"Próximos {DIAS} dias. Só lançados: o que ainda não foi lançado aparece riscado."
        )
    elif any(i.etiqueta.estilo == "futuro" for i in entradas):
        subtitulo = (
            f"Próximos {DIAS} dias. Lançados + provisões: só a inadimplência (V2) fica de fora."
        )
    else:
        subtitulo = f"Próximos {DIAS} dias. Lançados + provisões: tudo entra no saldo."
    return CartaoOrigemProjecao(
        id="origem-projecao",
        tipo="origem-projecao",
        largura=5,
        titulo="De onde vem a projeção",
        subtitulo=subtitulo,
        barra=[
            ParteOrigem(rotulo="Lançado", proporcao=q(lancado / total, 3), estilo="lancado"),
            ParteOrigem(
                rotulo="Ainda não lançado",
                proporcao=q(Decimal(1) - q(lancado / total, 3), 3),
                estilo="nao-lancado",
            ),
        ],
        texto=texto,
        grupos=[
            GrupoOrigem(titulo=titulo, itens=itens)
            for titulo, itens in (("Entradas", entradas), ("Saídas", saidas))
            if itens
        ],
    )


def _fundo(valor: Decimal, minimo: Decimal | None) -> Status:
    if valor < 0:
        return "critico"
    if minimo is not None and valor < minimo:
        return "atencao"
    return "saudavel"


def semanas(dias: Sequence[date]) -> list[list[int]]:
    """Índices dos dias de cada semana (segunda a domingo); a primeira começa no primeiro
    dia, a última termina no último."""
    saida: list[list[int]] = []
    for i, d in enumerate(dias):
        if not saida or d.weekday() == 0:
            saida.append([])
        saida[-1].append(i)
    return saida


def cartao_semanas(p: Projecao, visao: Visao) -> CartaoTabela:
    saldos = p.saldos(visao)
    linhas: list[LinhaTabela] = []
    mes_anterior: int | None = None
    for indices in semanas(p.dias):
        primeiro, ultimo = p.dias[indices[0]], p.dias[indices[-1]]
        entradas = sum((p.entradas(p.dias[i], visao) for i in indices), ZERO)
        saidas = sum((p.saidas(p.dias[i], visao) for i in indices), ZERO)
        menor = min(saldos[i] for i in indices)
        novo_mes = ultimo.month != mes_anterior
        linhas.append(
            LinhaTabela(
                separar=True if novo_mes and mes_anterior is not None else None,
                celulas=[
                    Celula(
                        valor=f"{_dia(primeiro)} a {_dia(ultimo)}",
                        marca=MESES_CURTOS[ultimo.month - 1] if novo_mes else None,
                    ),
                    Celula(valor=brl(entradas)),
                    Celula(valor=brl(-saidas)),
                    Celula(valor=brl(saldos[indices[-1]]), negrito=True),
                    Celula(valor=brl(menor), negrito=True, fundo=_fundo(menor, p.e.minimo)),
                ],
            )
        )
        mes_anterior = ultimo.month
    return CartaoTabela(
        id="semana-a-semana",
        tipo="tabela",
        largura=7,
        variante="listrada",
        titulo="Semana a semana",
        subtitulo=(
            "Entradas e saídas previstas, com as provisões, e o saldo no fim de cada semana"
            if visao == PROVISOES
            else "Entradas e saídas previstas e o saldo no fim de cada semana"
        ),
        colunas=[
            ColunaTabela(rotulo="Semana", alinhar="esquerda"),
            ColunaTabela(rotulo="Entradas", ocultar_no_celular=True),
            ColunaTabela(rotulo="Saídas", ocultar_no_celular=True),
            ColunaTabela(rotulo="Saldo no fim"),
            ColunaTabela(rotulo="Menor saldo"),
        ],
        linhas=linhas,
    )


def _personalizado(escolha: EscolhaGrupo) -> str | None:
    if escolha.valor is None:
        return None
    quem = f" por {escolha.por}" if escolha.por else ""
    quando = f" em {_dia(escolha.em)}" if escolha.em else ""
    return f"personalizado{quem}{quando}"


def cartao_provisoes(p: Projecao) -> CartaoProvisoes:
    linhas = []
    for linha in p.grupos:
        g, escolha = POR_ID[linha.id], linha.escolha
        linhas.append(
            LinhaProvisao(
                id=g.id,
                nome=g.nome,
                detalhe=g.detalhe if escolha.ligado else g.detalhe_desligado,
                ligado=escolha.ligado,
                media=q(linha.media, 0),
                lancado=q(linha.lancado1, 0) if linha.lancado1 > 0 else None,
                sugerida=q(linha.sugerida, 0),
                usado=q(linha.usado, 0),
                personalizado=_personalizado(escolha),
            )
        )
    return CartaoProvisoes(
        id="provisoes",
        tipo="provisoes",
        largura=12,
        titulo="Provisões pelo histórico",
        subtitulo=(
            "Completa o que ainda não está lançado com a média do período escolhido. Vale na "
            "visão “Lançados + provisões”."
        ),
        nota=(
            "Mesma lógica da planilha de provisões do comitê de caixa: provisão = média do "
            "período − o que já está lançado. Nos dias 31 a 60, vale a mesma conta com o "
            "lançado daquela janela. Investimentos começam desligados porque não se repetem. "
            "Digite um valor para personalizar; apague para voltar ao sugerido."
        ),
        periodos=[OpcaoFiltro(id=id_, rotulo=r) for id_, (r, _) in PERIODOS.items()],
        periodo=p.e.escolhas.periodo,
        colunas=ColunasProvisoes(
            usar="Usar",
            grupo="Grupo",
            media="Média por mês",
            lancado=f"Lançado em {JANELA} dias",
            sugerida="Provisão sugerida",
            usado="Valor usado",
        ),
        grupos=linhas,
        editavel=p.e.editavel,
    )


def cartao_pago_lancado(p: Projecao) -> CartaoGraficos | None:
    media = p.media_paga
    if media is None:
        return None
    f = p.e.farol_pagar
    meses = meses_pagos(p.pagos, p.e.inicio)
    pct1 = p.pagar1 / media * CEM
    pct2 = p.pagar2 / media * CEM
    status1, status2 = f.status(pct1), f.status(pct2)
    j1, j2 = p.janela1, p.janela2
    grupos = [
        GrupoBarras(
            rotulo=MESES_CURTOS[m.month - 1],
            titulo=f"{MESES[m.month - 1]} de {m.year}",
            barras=[Barra(nome="Pago", valor=q(p.pagos[m]["valor"], 0), estilo="serie")],
        )
        for m in meses
    ]
    grupos += [
        GrupoBarras(
            rotulo=f"{JANELA} d",
            titulo=f"Próximos {JANELA} dias ({_dia(j1[0])} a {_dia(j1[-1])})",
            barras=[Barra(nome="Lançado", valor=q(p.pagar1, 0), estilo=status1)],
        ),
        GrupoBarras(
            rotulo=f"{DIAS} d",
            titulo=f"{JANELA + 1} a {DIAS} dias ({_dia(j2[0])} a {_dia(j2[-1])})",
            barras=[Barra(nome="Lançado", valor=q(p.pagar2, 0), estilo=status2)],
        ),
    ]
    atencao = numero(f.faixa.atencao)
    return CartaoGraficos(
        id="pago-lancado",
        tipo="graficos",
        largura=6,
        status=status1,
        titulo="Pago contra lançado",
        subtitulo=(
            "Quanto a empresa pagou por mês e quanto já está lançado para os próximos meses"
        ),
        legenda=[
            ItemLegenda(nome="Pago", marca="quadrado-escuro"),
            ItemLegenda(nome="Lançado", marca="quadrado-laranja"),
            ItemLegenda(nome="Média paga", marca="minimo"),
        ],
        graficos=[
            GraficoCartao(
                grafico=GraficoBarras(
                    tipo="barras",
                    unidade="brl",
                    grupos=grupos,
                    referencias=[
                        ReferenciaBarras(nome="Média paga", valor=q(media, 0), estilo="minimo")
                    ],
                )
            )
        ],
        quadros=[
            QuadroCartao(
                rotulo=f"Próximos {JANELA} dias",
                titulo=f"{numero(pct1)}% da média paga",
                texto=_texto_status(status1, f),
                status=status1,
            ),
            QuadroCartao(
                rotulo=f"{JANELA + 1} a {DIAS} dias",
                titulo=f"{numero(pct2)}% da média paga",
                texto="Normal estar baixo: as contas ainda não chegaram",
            ),
        ],
        rodape=[
            Trecho(texto=f"Lançado para os próximos {JANELA} dias: "),
            Trecho(texto=moeda(p.pagar1), destaque=True),
            Trecho(
                texto=(
                    f", {numero(pct1)}% da média paga ({moeda(media)}). Para {JANELA + 1} a "
                    f"{DIAS} dias: "
                )
            ),
            Trecho(texto=moeda(p.pagar2), destaque=True),
            Trecho(
                texto=(
                    f", {numero(pct2)}%. É normal o mês seguinte estar menos lançado; abaixo de "
                    f"{atencao}% nos próximos {JANELA} dias, o painel avisa."
                )
            ),
        ],
    )


def cartao_para_onde(p: Projecao) -> CartaoBarrasHorizontais | None:
    itens = []
    for linha in p.grupos:
        g = POR_ID[linha.id]
        if g.entrada:
            continue
        valor = linha.lancado1 + linha.provisao1
        if valor > 0:
            itens.append((g.nome, valor))
    if not itens:
        return None
    itens.sort(key=lambda nv: -nv[1])
    return CartaoBarrasHorizontais(
        id="para-onde-vai",
        tipo="barras-horizontais",
        largura=6,
        titulo=f"Para onde vai o caixa em {JANELA} dias",
        subtitulo=(
            f"Saídas previstas de {_dia(p.janela1[0])} a {_dia(p.janela1[-1])}, lançadas e "
            "provisionadas"
        ),
        escala=Escala(unidade="brl"),
        itens=[BarraHorizontal(rotulo=nome, valor=q(valor, 0)) for nome, valor in itens],
    )


def cartoes(p: Projecao, visao: Visao) -> list[Cartao]:
    """Os cartões da projeção, na ordem da tela."""
    lista: list[Cartao | None] = [
        cartao_saldo(p, visao),
        cartao_origem(p, visao),
        cartao_semanas(p, visao),
        cartao_provisoes(p),
        cartao_pago_lancado(p),
        cartao_para_onde(p),
    ]
    return [c for c in lista if c is not None]


# ---------- leitura ----------

CAMPOS_POR_DIA: Mapping[str, tuple[str, Tipo]] = {
    "dia": ("data", "data"),
    "valor": ("valor", "numero"),
    "conta": ("conta", "texto"),
}


def consulta_por_dia(
    tabelas: Sequence[TabelaDoCliente], ctx: ContextoConsolidado, de: date, ate: date
) -> tuple[sql.Composed, dict[str, Any]]:
    """O caixa do realizado por dia do mês e conta, de `de` até antes de `ate`."""
    filtro = sql.SQL("(_empresa_id is null or _empresa_id = any(%(empresas)s))")
    return sql.SQL(
        """
        select extract(day from dia)::int as dia, conta, sum(valor) as valor
          from ({linhas}) f
         where dia >= %(de)s and dia < %(ate)s and conta is not null and valor is not null
         group by 1, 2
        """
    ).format(linhas=selecao(list(tabelas), CAMPOS_POR_DIA, filtro)), parametros(ctx, de=de, ate=ate)


# os títulos em aberto em D0 por vencimento (e conta do plano, no a pagar), o pago por mês e
# a série do vencido ÷ em aberto nos fins de mês (vazia sem `dias`)
SQL_TITULOS = """
    with t as materialized (
        select t.emissao,
               coalesce(t.vencimento, t.emissao) as vencimento,
               -- antes de 1901: data vazia gravada como 1899/1900
               case when t.pagamento >= date '1901-01-01' then t.pagamento end as pagamento,
               t.valor,
               {conta} as conta
          from ({linhas}) t
          {juncao}
         where t.emissao is not null and t.valor > 0
    ),
    fim as (
        select * from unnest(%(dias)s::date[], %(limites)s::date[]) as f (dia, limite)
    )
    select 'aberto' as parte, greatest(vencimento, %(piso)s) as dia, conta,
           sum(valor) as valor, null::numeric as aberto
      from t
     where (pagamento is null or pagamento > %(d0)s)
       and vencimento >= %(corte)s and vencimento <= %(teto)s
     group by 2, 3
    union all
    select 'pago', date_trunc('month', pagamento)::date, null, sum(valor), null
      from t
     where pagamento >= %(de)s and pagamento < %(ate)s
     group by 2
    union all
    select 'serie', fim.dia, null,
           coalesce(sum(t.valor) filter (where t.vencimento <= fim.limite), 0),
           coalesce(sum(t.valor), 0)
      from fim
      left join t on t.emissao <= fim.dia and (t.pagamento is null or t.pagamento > fim.dia)
     group by fim.dia
"""


def consulta_titulos(
    tabelas: list[TabelaDoCliente],
    ctx: ContextoConsolidado,
    *,
    com_conta: bool,
    d0: date,
    piso: date,
    corte: date,
    teto: date,
    de: date,
    ate: date,
    serie: Mapping[date, date],
) -> tuple[sql.Composed, dict[str, Any]]:
    campos = {"emissao": "emissao", "vencimento": "vencimento", "pagamento": "pagamento"}
    campos["valor"] = "valor"
    conta: sql.Composable = sql.SQL("null::text")
    juncao: sql.Composable = sql.SQL("")
    if com_conta:
        campos["conta"] = "conta"
        conta = sql.SQL("g.id_descritivo")
        juncao = sql.SQL(
            "left join cfg.conta_dre g on g.cliente_id = %(cliente)s and g.conta = t.conta"
        )
    return sql.SQL(SQL_TITULOS).format(
        linhas=de_todas(tabelas, campos, filtro_empresa(ctx)), conta=conta, juncao=juncao
    ), {
        "cliente": ctx.cliente_id,
        "empresas": list(ctx.empresas),
        "d0": d0,
        "piso": piso,
        "corte": corte,
        "teto": teto,
        "de": de,
        "ate": ate,
        "dias": list(serie),
        "limites": list(serie.values()),
    }


def titulos_das_linhas(
    receber: Iterable[Mapping[str, Any]] | None, pagar: Iterable[Mapping[str, Any]] | None
) -> Titulos:
    a_receber: dict[date, Decimal] = {}
    serie: dict[date, tuple[Decimal, Decimal]] = {}
    for r in receber or []:
        if r["parte"] == "aberto":
            a_receber[r["dia"]] = a_receber.get(r["dia"], ZERO) + r["valor"]
        elif r["parte"] == "serie":
            serie[r["dia"]] = (r["valor"], r["aberto"])
    a_pagar: dict[tuple[date, str | None], Decimal] = {}
    pagos: dict[date, Decimal] = {}
    for r in pagar or []:
        if r["parte"] == "aberto":
            chave = (r["dia"], normalizar(r["conta"]))
            a_pagar[chave] = a_pagar.get(chave, ZERO) + r["valor"]
        elif r["parte"] == "pago":
            pagos[r["dia"]] = r["valor"]
    return Titulos(
        receber=a_receber,
        pagar=a_pagar,
        pagos=pagos,
        serie_receber=serie,
        tem_receber=receber is not None,
        tem_pagar=pagar is not None,
    )


def por_dia_das_linhas(linhas: Iterable[Mapping[str, Any]]) -> dict[int, dict[str, Decimal]]:
    saida: dict[int, dict[str, Decimal]] = {}
    for r in linhas:
        codigo = normalizar(r["conta"])
        if codigo is None:
            continue
        contas = saida.setdefault(r["dia"], {})
        contas[codigo] = contas.get(codigo, ZERO) + r["valor"]
    return saida


def _minimo(valor: Any) -> Decimal | None:
    if isinstance(valor, dict):
        valor = valor.get("valor")
    if valor is None or isinstance(valor, bool):
        return None
    try:
        d = Decimal(str(valor))
    except InvalidOperation:
        return None
    return d if d.is_finite() and d > 0 else None


def ler(
    ctx: ContextoConsolidado,
    tabelas_fluxo: Sequence[TabelaDoCliente],
    contas: Mapping[date, Mapping[str, Decimal]],
    meses_com_caixa: Iterable[date],
    d0: date,
    saldo: Decimal | None,
    plano: Plano,
    data_titulos: date | None,
) -> Entrada:
    """Lê os títulos e o perfil do dia do mês numa ida ao banco (o resto já veio da
    pré-leitura) e monta a entrada da projeção."""
    conn: Conexao = ctx.sessao.conn
    hoje = date.today()
    parametros_cliente = parametros_do_cliente(conn, ctx.cliente_id)
    escolhas = ler_escolhas(parametros_cliente, ctx.empresa_id, hoje)
    inicio = d0 + timedelta(days=1)
    fim = d0 + timedelta(days=DIAS)
    mes_aberto = inicio.replace(day=1)
    primeiro = mes_menos(mes_aberto, MESES_INADIMPLENCIA + 1)
    fer = frozenset(feriados(conn, ctx.cliente_id, primeiro, fim + timedelta(days=15)))

    consultas: dict[str, tuple[sql.Composed, dict[str, Any]]] = {}
    a_receber = tabelas_do_cliente(conn, ctx.cliente_id, "f_receitas_", COLUNAS_RECEBER)
    a_pagar = tabelas_do_cliente(conn, ctx.cliente_id, "f_despesas_", COLUNAS_PAGAR)
    if a_receber:
        fins = [
            ultimo_dia_do_mes(mes_menos(mes_aberto, n)) for n in range(MESES_INADIMPLENCIA, 0, -1)
        ]
        # vencido há mais que alguns dias já passou do D+2: fica fora
        corte = d0 - timedelta(days=15)
        consultas["receber"] = consulta_titulos(
            a_receber,
            ctx,
            com_conta=False,
            d0=d0,
            piso=corte,
            corte=corte,
            teto=fim,
            de=inicio,
            ate=inicio,
            serie={d: ultimo_vencimento_vencido(d, set(fer)) for d in fins},
        )
    if a_pagar:
        com_conta = existe(conn, "cfg.conta_dre") and all(
            t.colunas.get("conta") in t.existentes for t in a_pagar
        )
        # o vencido vem com o vencimento em D0 (vai para D0+1)
        consultas["pagar"] = consulta_titulos(
            a_pagar,
            ctx,
            com_conta=com_conta,
            d0=d0,
            piso=d0,
            corte=date(1901, 1, 1),
            teto=fim,
            de=mes_menos(mes_aberto, 12),
            ate=mes_aberto,
            serie={},
        )
    meses = [m for m in meses_antes(mes_aberto, escolhas.meses) if m in set(meses_com_caixa)]
    if tabelas_fluxo and len(meses) >= MESES_PERFIL:
        consultas["por_dia"] = consulta_por_dia(tabelas_fluxo, ctx, meses[0], mes_aberto)
    linhas = em_uma_ida(conn, consultas)

    minimo = _minimo(vigente(parametros_cliente, CHAVE_CAIXA_MINIMO, hoje))
    return Entrada(
        d0=d0,
        saldo=saldo,
        contas=contas,
        meses_com_caixa=frozenset(meses_com_caixa),
        plano=plano,
        titulos=titulos_das_linhas(linhas.get("receber"), linhas.get("pagar")),
        por_dia=por_dia_das_linhas(linhas.get("por_dia", [])),
        escolhas=escolhas,
        feriados=fer,
        farol_pagar=farol(conn, ctx.cliente_id, "pagar_lancado", hoje),
        minimo=minimo,
        data_titulos=data_titulos,
    )

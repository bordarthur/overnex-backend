"""Velocímetros que comparam o mês com uma referência. REGRA PROVISÓRIA (ver TODO.md).

A referência é a meta cadastrada (`referencia` em farol.<chave>, cfg.parametro_cliente)
ou, sem ela, a média dos até 6 meses anteriores com dado. O velocímetro e o farol usam o
valor em % da referência; o cartão mostra o valor na unidade do indicador.

O valor de um conjunto de meses é uma razão de totais do histórico mensal: sem
denominador, a média mensal do numerador (ex.: clientes por mês); com denominador, a
soma de um ÷ a soma do outro (ex.: faturado ÷ pedidos = ticket), sem média de médias.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.farol.parametro import Farol
from app.telas.visao_geral.contrato import Faixa as FaixaGauge
from app.telas.visao_geral.contrato import (
    Gauge,
    Indicador,
    IndicadorPeriodo,
    PeriodosIndicador,
    Tendencia,
    Unidade,
    Valor,
)

# mês → medida → total do mês (mart.resumo_mensal)
Mensal = dict[date, dict[str, Decimal]]

MESES_CURTOS = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")
MESES_REFERENCIA = 6
MESES_TENDENCIA = 6


@dataclass(frozen=True)
class Razao:
    numerador: str
    # None = média mensal do numerador
    denominador: str | None = None

    def tem(self, mensal: Mensal, mes: date) -> bool:
        medidas = mensal.get(mes, {})
        return self.numerador in medidas and (
            self.denominador is None or self.denominador in medidas
        )

    def valor(self, mensal: Mensal, meses: Sequence[date]) -> Decimal | None:
        com_dado = [m for m in meses if self.tem(mensal, m)]
        if not com_dado:
            return None
        num = sum((mensal[m][self.numerador] for m in com_dado), Decimal(0))
        if self.denominador is None:
            return num / len(com_dado)
        den = sum((mensal[m][self.denominador] for m in com_dado), Decimal(0))
        return num / den if den else None


@dataclass(frozen=True)
class Definicao:
    id: str
    nome: str
    # farol.<chave_farol> em cfg.parametro_cliente
    chave_farol: str
    razao: Razao
    unidade: Unidade
    # número dentro de uma frase, na unidade do indicador
    texto: Callable[[Decimal], str]
    casas: int | None = None
    compacto: bool | None = None
    sufixo: str | None = None
    # True: o delta sai em % da referência (ex.: ticket +2,1%); False: na unidade
    delta_percentual: bool = False


def _intervalo(meses: Sequence[date]) -> str:
    primeiro, ultimo = MESES_CURTOS[meses[0].month - 1], MESES_CURTOS[meses[-1].month - 1]
    return primeiro if len(meses) == 1 else f"{primeiro} a {ultimo}"


def indicador_contra_referencia(
    d: Definicao, ref: date, mensal: Mensal, f: Farol
) -> Indicador | None:
    """None sem dado no mês, sem referência ou com menos de 2 meses para a tendência."""
    meses = sorted(m for m in mensal if m <= ref and d.razao.tem(mensal, m))
    if not meses or meses[-1] != ref:
        return None
    tendencia = meses[-MESES_TENDENCIA:]
    if len(tendencia) < 2:
        return None

    anteriores = [m for m in meses if m < ref][-MESES_REFERENCIA:]
    if f.referencia is not None:
        referencia: Decimal | None = f.referencia
        contra = "meta"
    else:
        referencia = d.razao.valor(mensal, anteriores)
        contra = (
            "o mês anterior"
            if len(anteriores) == 1
            else f"a média de {len(anteriores)} meses ({_intervalo(anteriores)})"
        )
    if not referencia:
        return None

    def periodo(valor: Decimal, referencia_texto: str) -> IndicadorPeriodo:
        pct = valor / referencia * 100
        if d.delta_percentual:
            delta = Valor(valor=pct - 100, unidade="pct", casas=1)
        else:
            delta = Valor(
                valor=valor - referencia, unidade=d.unidade, casas=d.casas, sufixo=d.sufixo
            )
        pior = valor < referencia if f.faixa.maior_melhor else valor > referencia
        return IndicadorPeriodo(
            status=f.status(pct),
            valor=Valor(
                valor=valor, unidade=d.unidade, casas=d.casas, compacto=d.compacto, sufixo=d.sufixo
            ),
            ponteiro=f.ponteiro(pct),
            delta=delta,
            delta_ruim=pior,
            referencia=referencia_texto,
        )

    valor_mes = d.razao.valor(mensal, [ref])
    meses_ano = [m for m in meses if m.year == ref.year]
    valor_ano = d.razao.valor(mensal, meses_ano)
    if valor_mes is None or valor_ano is None:
        return None
    alvo = (
        f"meta de {d.texto(referencia)}" if contra == "meta" else f"{contra}: {d.texto(referencia)}"
    )

    return Indicador(
        id=d.id,
        nome=d.nome,
        origem="dw",
        gauge=Gauge(
            min=f.minimo,
            max=f.maximo,
            meta=f.meta,
            unidade="pct",
            faixas=[FaixaGauge(de=de, ate=ate, status=s) for de, ate, s in f.faixas()],
        ),
        periodos=PeriodosIndicador(
            mes=periodo(valor_mes, f"contra {alvo}"),
            ano=periodo(valor_ano, f"de {_intervalo(meses_ano)}, contra {alvo}"),
        ),
        tendencia=Tendencia(
            rotulos=[MESES_CURTOS[m.month - 1] for m in tendencia],
            real=[d.razao.valor(mensal, [m]) or Decimal(0) for m in tendencia],
            referencia=[referencia] * len(tendencia),
            unidade=d.unidade,
            casas=d.casas,
        ),
    )

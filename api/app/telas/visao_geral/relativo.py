"""Velocímetros na unidade do indicador (% da receita, dias) contra o orçado ou a média dos
meses anteriores, com farol RELATIVO: os limites ficam em p.p. ou dias de distância da
referência (deslocado(), em app/farol/parametro.py), como o CMV da seção Custos.

REGRA PROVISÓRIA (ver TODO.md):
- com orçado no mês de referência, a referência é o orçado: no mês, o do mês; no
  acumulado do ano, a razão dos totais orçados dos meses do ano que têm orçado;
- sem orçado, a média dos até 6 meses anteriores com dado (soma ÷ soma), a mesma nos dois
  períodos;
- o velocímetro é um só para os dois períodos, na escala do mês. No acumulado do ano com
  orçado, o ponteiro marca a distância do valor ao orçado do ano, levada para a escala do
  mês, para cair na faixa do status.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.farol.parametro import Farol, deslocado
from app.telas.visao_geral.contra_referencia import Mensal, Razao, intervalo, rotulo_eixo
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

MESES_REFERENCIA = 6
MESES_TENDENCIA = 6


@dataclass(frozen=True)
class Serie:
    """Valor de um conjunto de meses: razão dos totais do histórico, vezes a escala."""

    mensal: Mensal
    razao: Razao
    # 100 para percentual
    escala: Decimal = Decimal(1)

    def tem(self, mes: date) -> bool:
        return self.razao.tem(self.mensal, mes)

    def valor(self, meses: Sequence[date]) -> Decimal | None:
        v = self.razao.valor(self.mensal, meses)
        return v * self.escala if v is not None else None


@dataclass(frozen=True)
class Medidor:
    id: str
    nome: str
    unidade: Unidade
    # "pp" para percentual; nos outros, a própria unidade
    unidade_delta: Unidade
    # número dentro de uma frase: "20,4%", "28 dias"
    texto: Callable[[Decimal], str]
    casas: int | None = None
    # antes do "contra" no período mês, ex.: "da receita líquida"
    contexto: str | None = None


def indicador_relativo(
    m: Medidor, ref: date, real: Serie, orcado: Serie | None, base: Farol
) -> Indicador | None:
    """None sem dado no mês, sem referência ou com menos de 2 meses para a tendência."""
    meses = sorted(x for x in real.mensal if x <= ref and real.tem(x))
    if not meses or meses[-1] != ref:
        return None
    tendencia = meses[-MESES_TENDENCIA:]
    if len(tendencia) < 2:
        return None
    meses_ano = [x for x in meses if x.year == ref.year]

    if orcado is not None and orcado.tem(ref):
        meses_ano = [x for x in meses_ano if orcado.tem(x)]
        ref_mes, ref_ano = orcado.valor([ref]), orcado.valor(meses_ano)
        if ref_mes is None or ref_ano is None:
            return None
        alvo_mes, alvo_ano = f"{m.texto(ref_mes)} orçado", f"{m.texto(ref_ano)} orçado"
    else:
        anteriores = [x for x in meses if x < ref][-MESES_REFERENCIA:]
        media = real.valor(anteriores)
        if media is None:
            return None
        nome = (
            "o mês anterior"
            if len(anteriores) == 1
            else f"a média de {len(anteriores)} meses ({intervalo(anteriores)})"
        )
        ref_mes = ref_ano = media
        alvo_mes = alvo_ano = f"{nome}: {m.texto(media)}"

    valor_mes, valor_ano = real.valor([ref]), real.valor(meses_ano)
    if valor_mes is None or valor_ano is None:
        return None

    f = deslocado(base, ref_mes)
    maior_melhor = base.faixa.maior_melhor

    def periodo(valor: Decimal, referencia: Decimal, texto: str) -> IndicadorPeriodo:
        return IndicadorPeriodo(
            status=deslocado(base, referencia).status(valor),
            valor=Valor(valor=valor, unidade=m.unidade, casas=m.casas),
            # no mês, valor - ref_mes + ref_mes = o próprio valor
            ponteiro=f.ponteiro(valor - referencia + ref_mes),
            delta=Valor(valor=valor - referencia, unidade=m.unidade_delta, casas=m.casas),
            delta_ruim=valor < referencia if maior_melhor else valor > referencia,
            referencia=texto,
        )

    contra_mes = f"{m.contexto}, contra {alvo_mes}" if m.contexto else f"contra {alvo_mes}"
    return Indicador(
        id=m.id,
        nome=m.nome,
        origem="dw",
        gauge=Gauge(
            min=f.minimo,
            max=f.maximo,
            meta=f.meta,
            unidade=m.unidade,
            faixas=[FaixaGauge(de=de, ate=ate, status=s) for de, ate, s in f.faixas()],
        ),
        periodos=PeriodosIndicador(
            mes=periodo(valor_mes, ref_mes, contra_mes),
            ano=periodo(valor_ano, ref_ano, f"de {intervalo(meses_ano)}, contra {alvo_ano}"),
        ),
        tendencia=Tendencia(
            rotulos=[rotulo_eixo(x) for x in tendencia],
            real=[real.valor([x]) or Decimal(0) for x in tendencia],
            referencia=[ref_mes] * len(tendencia),
            # faixa saudável, do limite de atenção até a ponta boa da escala
            faixa=(f.faixa.atencao, f.maximo) if maior_melhor else (f.minimo, f.faixa.atencao),
            unidade=m.unidade,
            casas=m.casas,
        ),
    )

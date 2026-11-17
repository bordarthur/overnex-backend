"""Valores cheios na Visão geral (pedido da demonstração, 16/11/2026): os KPIs do topo de
cada seção e os números grandes dos cartões saem em reais sem abreviar (R$ 6.900.000, não
R$ 6,90 mi), com `compacto: false`.

Ficam abreviados, de propósito: os valores dentro das frases (contexto, resumo, notas) e o
que vai para gráfico (barras dos próximos dias e das contas lançadas, tendências), que não
cabem cheios.

Passa por cima das seções prontas (reais e do demo), sem mudar as funções de cada seção.
"""

from app.telas.visao_geral.contrato import (
    CartaoInadimplencia,
    CartaoProximosDias,
    CartaoSecao,
    Indicador,
    IndicadorPeriodo,
    Kpi,
    Secao,
    Valor,
)


def cheio(v: Valor) -> Valor:
    """Reais sem abreviar; as outras unidades (e o que já vem cheio) ficam como estão."""
    if v.unidade != "brl" or v.compacto is False:
        return v
    return v.model_copy(update={"compacto": False, "casas_mil": None})


def _kpi(k: Kpi) -> Kpi:
    return k.model_copy(update={"valor": cheio(k.valor)})


def _periodo(p: IndicadorPeriodo) -> IndicadorPeriodo:
    return p.model_copy(update={"valor": cheio(p.valor)})


def _indicador(i: Indicador) -> Indicador:
    periodos = i.periodos.model_copy(
        update={"mes": _periodo(i.periodos.mes), "ano": _periodo(i.periodos.ano)}
    )
    return i.model_copy(update={"periodos": periodos})


def _cartao(c: CartaoSecao) -> CartaoSecao:
    if isinstance(c, CartaoInadimplencia):
        # os quadros da carteira por idade
        return c.model_copy(
            update={
                "carteira": [i.model_copy(update={"valor": cheio(i.valor)}) for i in c.carteira]
            }
        )
    if isinstance(c, CartaoProximosDias):
        # o saldo de hoje e onde o caixa termina em cada janela; as barras ficam abreviadas
        janelas = [
            j.model_copy(
                update={
                    "fim": {
                        modo: f.model_copy(update={"valor": cheio(f.valor)})
                        for modo, f in j.fim.items()
                    }
                }
            )
            for j in c.janelas
        ]
        return c.model_copy(
            update={"saldo": cheio(c.saldo) if c.saldo is not None else None, "janelas": janelas}
        )
    return c


def secao_cheia(s: Secao) -> Secao:
    return s.model_copy(
        update={
            "kpis": [_kpi(k) for k in s.kpis],
            "cartoes": [_cartao(c) for c in s.cartoes],
            "indicadores": [_indicador(i) for i in s.indicadores],
        }
    )


def secoes_cheias(secoes: list[Secao]) -> list[Secao]:
    return [secao_cheia(s) for s in secoes]

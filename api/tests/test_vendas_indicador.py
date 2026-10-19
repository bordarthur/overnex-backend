"""Velocímetro "Vendas contra a meta" e faixas do farol — sem banco."""

from datetime import date
from decimal import Decimal

from app.farol.parametro import PADROES, Farol
from app.farol.status import Faixa
from app.telas.visao_geral.vendas import indicador_vendas_meta

PADRAO = PADROES["vendas_meta"]
D = Decimal


def _meses(*valores: int, ano: int = 2026, inicio: int = 4) -> dict[date, Decimal]:
    return {date(ano, inicio + i, 1): D(v) for i, v in enumerate(valores)}


def test_faixas_do_velocimetro_maior_melhor() -> None:
    assert PADRAO.faixas() == [
        (D(80), D(90), "critico"),
        (D(90), D(100), "atencao"),
        (D(100), D(110), "saudavel"),
    ]


def test_faixas_menor_melhor_e_recorte_na_escala() -> None:
    f = Farol(
        faixa=Faixa(atencao=D(30), critico=D(60), maior_melhor=False),
        minimo=D(40),
        maximo=D(90),
        meta=D(30),
    )
    # a faixa saudável (até 30) fica abaixo da escala e some
    assert f.faixas() == [(D(40), D(60), "atencao"), (D(60), D(90), "critico")]


def test_ponteiro_fica_no_limite_da_escala() -> None:
    assert PADRAO.ponteiro(D("56.2")) == D(80)
    assert PADRAO.ponteiro(D(95)) == D(95)
    assert PADRAO.ponteiro(D(130)) == D(110)


def test_indicador_mes_ano_e_tendencia() -> None:
    # abr a set: faturado e meta; o ano vai de abr (primeiro mês com os dois) a set
    faturado = _meses(90, 100, 110, 95, 100, 50)
    metas = _meses(100, 100, 100, 100, 100, 100)
    ref = date(2026, 9, 1)

    ind = indicador_vendas_meta(ref, faturado, metas, PADRAO)

    assert ind is not None
    mes, ano = ind.periodos.mes, ind.periodos.ano
    assert (mes.valor.valor, mes.status, mes.ponteiro) == (D(50), "critico", D(80))
    assert mes.delta is not None
    assert mes.delta.valor == D(-50)
    assert mes.delta_ruim
    assert mes.referencia == "R$ 50 contra meta de R$ 100"
    # 545 / 600
    assert ano.valor.valor.quantize(D("0.1")) == D("90.8")
    assert ano.status == "atencao"
    assert ano.referencia == "R$ 545 de abr a set contra meta de R$ 600"
    assert ind.tendencia.rotulos == ["abr", "mai", "jun", "jul", "ago", "set"]
    assert ind.tendencia.real == [D(90), D(100), D(110), D(95), D(100), D(50)]
    assert ind.gauge.meta == D(100)


def test_tendencia_so_com_os_ultimos_6_meses_e_ano_so_do_ano() -> None:
    faturado = _meses(*([100] * 10), ano=2025, inicio=3) | _meses(100, 100, inicio=1)
    metas = dict.fromkeys(faturado, D(100))

    ind = indicador_vendas_meta(date(2026, 2, 1), faturado, metas, PADRAO)

    assert ind is not None
    assert ind.tendencia.rotulos == ["set", "out", "nov", "dez", "jan", "fev"]
    assert ind.periodos.ano.referencia == "R$ 200 de jan a fev contra meta de R$ 200"


def test_sem_historico_ou_sem_meta_nao_ha_indicador() -> None:
    ref = date(2026, 9, 1)
    assert indicador_vendas_meta(ref, {ref: D(10)}, {ref: D(20)}, PADRAO) is None
    assert indicador_vendas_meta(ref, _meses(1, 2, 3, 4, 5, 6), {}, PADRAO) is None

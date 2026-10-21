"""Velocímetro "Vendas contra a meta" e faixas do farol — sem banco."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from app.farol.parametro import PADROES, Farol
from app.farol.status import Faixa
from app.telas.visao_geral.contra_referencia import indicador_contra_referencia
from app.telas.visao_geral.vendas import CLIENTES, PRODUTOS, TICKET, indicador_vendas_meta

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
    assert ind.tendencia.rotulos == ["abr-26", "mai-26", "jun-26", "jul-26", "ago-26", "set-26"]
    assert ind.tendencia.real == [D(90), D(100), D(110), D(95), D(100), D(50)]
    assert ind.gauge.meta == D(100)


def test_tendencia_so_com_os_ultimos_6_meses_e_ano_so_do_ano() -> None:
    faturado = _meses(*([100] * 10), ano=2025, inicio=3) | _meses(100, 100, inicio=1)
    metas = dict.fromkeys(faturado, D(100))

    ind = indicador_vendas_meta(date(2026, 2, 1), faturado, metas, PADRAO)

    assert ind is not None
    assert ind.tendencia.rotulos == ["set-25", "out-25", "nov-25", "dez-25", "jan-26", "fev-26"]
    assert ind.periodos.ano.referencia == "R$ 200 de jan a fev contra meta de R$ 200"


def test_sem_historico_ou_sem_meta_nao_ha_indicador() -> None:
    ref = date(2026, 9, 1)
    assert indicador_vendas_meta(ref, {ref: D(10)}, {ref: D(20)}, PADRAO) is None
    assert indicador_vendas_meta(ref, _meses(1, 2, 3, 4, 5, 6), {}, PADRAO) is None


def _mensal(**medidas: list[int]) -> dict[date, dict[str, Decimal]]:
    """Meses a partir de mar/2026, uma lista por medida."""
    n = len(next(iter(medidas.values())))
    return {date(2026, 3 + i, 1): {nome: D(v[i]) for nome, v in medidas.items()} for i in range(n)}


def test_clientes_contra_a_media_dos_6_meses_anteriores() -> None:
    # mar a ago: média 100; set: 85
    mensal = _mensal(clientes=[90, 110, 100, 100, 95, 105, 85])
    ref = date(2026, 9, 1)

    ind = indicador_contra_referencia(CLIENTES, ref, mensal, PADROES["clientes_compraram"])

    assert ind is not None
    mes = ind.periodos.mes
    assert mes.valor.valor == D(85)
    assert mes.status == "critico"  # 85% da média
    assert mes.ponteiro == D(85)
    assert mes.delta is not None
    assert mes.delta.valor == D(-15)
    assert mes.delta_ruim
    assert mes.referencia == "contra a média de 6 meses (mar a ago): 100 clientes"
    # média mensal de mar a set
    assert ind.periodos.ano.valor.valor.quantize(D("0.1")) == D("97.9")
    assert ind.periodos.ano.referencia.startswith("de mar a set, contra a média")
    assert ind.tendencia.rotulos == ["abr-26", "mai-26", "jun-26", "jul-26", "ago-26", "set-26"]
    assert ind.tendencia.referencia == [D(100)] * 6


def test_ticket_e_razao_de_totais_com_delta_em_percentual() -> None:
    # ago: 1000/10 = 100; set: 2100/20 = 105 (não é média de médias)
    mensal = {
        date(2026, 8, 1): {"faturado": D(1000), "pedidos": D(10)},
        date(2026, 9, 1): {"faturado": D(2100), "pedidos": D(20)},
    }
    ind = indicador_contra_referencia(TICKET, date(2026, 9, 1), mensal, PADROES["ticket_pedido"])

    assert ind is not None
    mes = ind.periodos.mes
    assert mes.valor.valor == D(105)
    assert mes.delta is not None
    assert (mes.delta.valor, mes.delta.unidade) == (D(5), "pct")
    assert not mes.delta_ruim
    assert mes.status == "saudavel"
    assert mes.referencia == "contra o mês anterior: R$ 100"
    # ano: 3100 / 30
    assert ind.periodos.ano.valor.valor.quantize(D("0.01")) == D("103.33")


def test_produtos_por_cliente_com_meta_cadastrada() -> None:
    mensal = _mensal(clientes_produtos=[26, 23], clientes=[10, 10])
    f = replace(PADROES["produtos_cliente"], referencia=D("2.6"))

    ind = indicador_contra_referencia(PRODUTOS, date(2026, 4, 1), mensal, f)

    assert ind is not None
    assert ind.periodos.mes.valor.valor == D("2.3")
    assert ind.periodos.mes.referencia == "contra meta de 2,6 produtos"
    assert ind.periodos.mes.status == "critico"  # 88,5% da meta, abaixo de 90%
    assert ind.tendencia.real == [D("2.6"), D("2.3")]


def test_sem_mes_anterior_nem_meta_nao_ha_indicador() -> None:
    mensal = _mensal(clientes=[100])
    assert (
        indicador_contra_referencia(
            CLIENTES, date(2026, 3, 1), mensal, PADROES["clientes_compraram"]
        )
        is None
    )

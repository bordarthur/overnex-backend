"""Seção Custos: CMV por ramo e cartões — sem banco."""

from datetime import date
from decimal import Decimal

from app.farol.parametro import PADROES, deslocado
from app.telas.visao_geral.contra_referencia import Mensal
from app.telas.visao_geral.custos import cmv, indicador_cmv, kpis_custos

D = Decimal
BASE = PADROES["cmv"]
SET = date(2026, 9, 1)


def _mensal(*cmvs: str, faturado: int = 1000, inicio: int = 3) -> Mensal:
    """Meses a partir de mar/2026 com o CMV (%) de cada um."""
    return {
        date(2026, inicio + i, 1): {"faturado": D(faturado), "custo": D(faturado) * D(c) / 100}
        for i, c in enumerate(cmvs)
    }


def test_farol_relativo_deslocado_para_a_referencia() -> None:
    f = deslocado(BASE, D(84))
    assert (f.minimo, f.maximo, f.meta) == (D(80), D(90), D(84))
    assert f.faixas() == [
        (D(80), D("84.3"), "saudavel"),
        (D("84.3"), D("85.5"), "atencao"),
        (D("85.5"), D(90), "critico"),
    ]


def test_cmv_e_soma_sobre_soma() -> None:
    mensal = {
        date(2026, 8, 1): {"faturado": D(100), "custo": D(90)},
        date(2026, 9, 1): {"faturado": D(300), "custo": D(240)},
    }
    # (90 + 240) / (100 + 300), não a média de 90% e 80%
    assert cmv(mensal, [date(2026, 8, 1), date(2026, 9, 1)]) == D("82.5")


def test_indicador_cmv_contra_a_media_dos_meses_anteriores() -> None:
    # mar a ago: 84%; set: 85,9%
    mensal = _mensal("84", "84", "84", "84", "84", "84", "85.9")

    ind = indicador_cmv("cmv-x", "CMV de x", SET, mensal, BASE)

    assert ind is not None
    mes = ind.periodos.mes
    assert mes.valor.valor == D("85.9")
    assert mes.status == "critico"  # +1,9 p.p.
    assert mes.delta is not None
    assert (mes.delta.valor, mes.delta.unidade) == (D("1.9"), "pp")
    assert mes.delta_ruim
    assert mes.referencia == "do faturado, contra a média de 6 meses (mar a ago): 84,0%"
    assert ind.gauge.meta == D(84)
    assert ind.tendencia.faixa == (D(80), D("84.3"))
    assert ind.tendencia.referencia == [D(84)] * 6
    assert ind.periodos.ano.referencia.startswith("de mar a set, contra")


def test_indicador_cmv_abaixo_da_referencia_e_saudavel() -> None:
    ind = indicador_cmv("cmv-x", "CMV de x", SET, _mensal("84", "83", inicio=8), BASE)
    assert ind is not None
    assert ind.periodos.mes.status == "saudavel"
    assert not ind.periodos.mes.delta_ruim
    assert ind.periodos.mes.referencia.endswith("contra o mês anterior: 84,0%")


def test_sem_mes_anterior_nao_ha_indicador() -> None:
    assert indicador_cmv("cmv-x", "CMV de x", SET, _mensal("84", inicio=9), BASE) is None


def test_kpis_com_referencia_e_efeito_no_lucro() -> None:
    mensal = _mensal("84", "84", "84", "84", "84", "84")  # mar a ago
    kpis = kpis_custos(SET, date(2026, 9, 19), D(1000), D(850), mensal, D(2000), BASE)

    rotulos = [k.rotulo for k in kpis]
    assert rotulos == [
        "CMV do mês",
        "Margem bruta",
        "Lucro bruto até 19/09",
        "Efeito no lucro do mês",
    ]
    cmv_mes, margem, lucro, efeito = kpis
    assert cmv_mes.valor.valor == D(85)
    assert cmv_mes.contexto == "+1,0 p.p. acima da média de 6 meses (84,0%)"
    assert cmv_mes.status == "atencao"
    assert margem.valor.valor == D(15)
    assert margem.contexto == "média de 6 meses: 16,0%"
    assert lucro.valor.valor == D(150)
    # 1 p.p. a mais de CMV sobre R$ 2.000 projetados
    assert efeito.valor.valor == D(-20)
    assert efeito.status == "atencao"


def test_kpis_sem_historico() -> None:
    kpis = kpis_custos(SET, date(2026, 9, 19), D(1000), D(850), {}, D(2000), BASE)
    assert [k.rotulo for k in kpis] == ["CMV do mês", "Margem bruta", "Lucro bruto até 19/09"]
    assert kpis[0].status is None
    assert kpis[0].contexto == "custo sobre o faturado"

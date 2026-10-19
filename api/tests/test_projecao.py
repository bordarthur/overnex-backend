"""Dias úteis, projeção de fechamento e KPIs de vendas — sem banco."""

from datetime import date
from decimal import Decimal

from app.farol.parametro import PADROES
from app.telas.calendario import dias_uteis, feriados_nacionais, ultimo_dia_do_mes
from app.telas.visao_geral.contrato import Kpi
from app.telas.visao_geral.projecao import projetar
from app.telas.visao_geral.vendas import kpis_vendas

D = Decimal
FERIADOS_2026 = feriados_nacionais(2026)
FAROL = PADROES["vendas_meta"]


def test_feriados_nacionais_de_2026() -> None:
    assert date(2026, 9, 7) in FERIADOS_2026  # Independência
    assert date(2026, 11, 20) in FERIADOS_2026  # Consciência Negra
    assert date(2026, 4, 3) in FERIADOS_2026  # Sexta-feira Santa
    assert date(2026, 2, 16) not in FERIADOS_2026  # Carnaval: ponto facultativo


def test_dias_uteis_sem_fim_de_semana_nem_feriado() -> None:
    # setembro/2026: 22 dias de semana, menos o 7/09
    assert dias_uteis(date(2026, 9, 1), date(2026, 9, 30), FERIADOS_2026) == 21
    # 19/09 é sábado: até ele, 13 dias úteis
    assert dias_uteis(date(2026, 9, 1), date(2026, 9, 19), FERIADOS_2026) == 13
    assert ultimo_dia_do_mes(date(2026, 2, 1)) == date(2026, 2, 28)
    assert ultimo_dia_do_mes(date(2026, 12, 1)) == date(2026, 12, 31)


def test_exemplo_da_fixture() -> None:
    # outubro/2026: 21 dias úteis (12/10 é feriado); até 12/10, 7
    p = projetar(D(2_180_000), D(6_900_000), date(2026, 10, 12), FERIADOS_2026)

    assert p is not None
    assert (p.du_mes, p.du_passados, p.du_restantes) == (21, 7, 14)
    assert p.ritmo.quantize(D(1)) == D(311_429)
    assert p.projecao.quantize(D(1)) == D(6_540_000)
    assert p.diferenca.quantize(D(1)) == D(-360_000)
    assert p.a_realizar is not None
    assert p.a_realizar.quantize(D(1)) == D(337_143)


def test_mes_fechado_projecao_e_o_faturado() -> None:
    p = projetar(D(1000), D(900), date(2026, 9, 30), FERIADOS_2026)

    assert p is not None
    assert p.du_restantes == 0
    assert p.projecao == D(1000)
    assert p.a_realizar is None


def test_sem_dia_util_ate_a_ultima_venda_nao_projeta() -> None:
    # 1º/11/2026 é domingo e 2/11 é Finados
    assert projetar(D(10), D(100), date(2026, 11, 2), FERIADOS_2026) is None


def _rotulos(kpis: list[Kpi]) -> list[str]:
    return [k.rotulo for k in kpis]


def test_kpis_com_mes_em_andamento() -> None:
    p = projetar(D(2_180_000), D(6_900_000), date(2026, 10, 12), FERIADOS_2026)
    kpis = kpis_vendas(date(2026, 10, 1), date(2026, 10, 12), D(2_180_000), D(6_900_000), p, FAROL)

    assert _rotulos(kpis) == [
        "Meta de outubro",
        "Faturado até 12/10",
        "Projeção de fechamento",
        "A realizar por dia útil",
    ]
    faturado, projecao, realizar = kpis[1], kpis[2], kpis[3]
    assert faturado.contexto == "meta até a data: R$ 2,30 mi"
    assert faturado.status == "atencao"  # 94,8% da meta até a data
    assert projecao.contexto == "R$ 360 mil abaixo da meta"
    assert projecao.status == "atencao"
    assert realizar.contexto == "hoje vende R$ 311 mil por dia"
    assert realizar.status == "atencao"  # 311 / 337 = 92%


def test_kpis_sem_meta_so_o_faturado() -> None:
    kpis = kpis_vendas(date(2026, 9, 1), date(2026, 9, 19), D(100), None, None, FAROL)
    assert _rotulos(kpis) == ["Faturado em setembro"]
    assert kpis[0].status is None


def test_kpis_mes_fechado_sem_a_realizar() -> None:
    p = projetar(D(1000), D(900), date(2026, 9, 30), FERIADOS_2026)
    kpis = kpis_vendas(date(2026, 9, 1), date(2026, 9, 30), D(1000), D(900), p, FAROL)

    assert _rotulos(kpis) == ["Meta de setembro", "Faturado até 30/09", "Projeção de fechamento"]
    assert kpis[2].valor.valor == D(1000)
    assert kpis[2].contexto == "R$ 100 acima da meta"


def test_kpis_meta_ja_batida() -> None:
    p = projetar(D(1000), D(900), date(2026, 9, 15), FERIADOS_2026)
    kpis = kpis_vendas(date(2026, 9, 1), date(2026, 9, 15), D(1000), D(900), p, FAROL)

    realizar = kpis[3]
    assert realizar.valor.valor == D(0)
    assert realizar.contexto == "meta atingida"
    assert realizar.status == "saudavel"

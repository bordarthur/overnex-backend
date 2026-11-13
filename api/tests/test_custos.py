"""Seção Custos: CMV por ramo e cartões — sem banco."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.farol.parametro import PADROES, deslocado
from app.telas.visao_geral import custos
from app.telas.visao_geral.contra_referencia import Mensal
from app.telas.visao_geral.contrato import Secao
from app.telas.visao_geral.custos import (
    SEM_RAMO,
    Abertura,
    cmv,
    indicador_cmv,
    kpis_custos,
    linhas_em_destaque,
    nome_outros,
    secao_custos,
)
from app.telas.visao_geral.vendas import BaseVendas, MesDeVendas

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


# --- seletor "Linhas de custo" ---------------------------------------------------------

AGO = date(2026, 8, 1)
# custo do mês por linha; o ramo vazio nunca tem velocímetro próprio
CUSTOS_DO_MES = {"A": 500, "B": 200, SEM_RAMO: 100, "C": 100, "D": 50, "E": 50}


def test_automatico_pega_as_linhas_que_somam_80_por_cento() -> None:
    custos = {r: D(c) for r, c in CUSTOS_DO_MES.items()}
    # 500 + 200 = 70%; com C, 80%: para aí
    assert linhas_em_destaque(custos, "auto") == ["A", "B", "C"]


def test_automatico_no_maximo_7_linhas() -> None:
    custos = {f"L{i:02d}": D(10) for i in range(10)}
    # 7 linhas somam só 70%, mas o máximo é 7
    assert len(linhas_em_destaque(custos, "auto")) == 7


def test_numero_fixo_de_linhas_por_custo() -> None:
    custos = {r: D(c) for r, c in CUSTOS_DO_MES.items()}
    assert linhas_em_destaque(custos, "1") == ["A"]
    # pede 7, só há 5 linhas com nome
    assert linhas_em_destaque(custos, "7") == ["A", "B", "C", "D", "E"]
    # sem custo positivo, a linha não ganha velocímetro
    assert linhas_em_destaque({"A": D(10), "B": D(0), "C": D(-5)}, "3") == ["A"]


def test_nome_de_outros_com_o_plural_da_abertura() -> None:
    assert nome_outros(11, Abertura(nome="linha", descricao="linha", plural="linhas")) == (
        "Outros (11 linhas)"
    )
    assert nome_outros(1, Abertura()) == "Outros (1 ramo)"


@pytest.fixture
def secao(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], Secao]:
    """secao_custos com o banco trocado: as linhas de CUSTOS_DO_MES em setembro, com
    agosto no histórico (CMV de 80%; o ramo vazio, 100%)."""

    def medidas(ramo: str, custo: int) -> dict[str, Decimal]:
        faturado = D(custo) if ramo == SEM_RAMO else D(custo) * D("1.25")
        return {"faturado": faturado, "custo": D(custo)}

    do_mes = {r: medidas(r, c) for r, c in CUSTOS_DO_MES.items()}
    monkeypatch.setattr(custos, "_ramos_do_mes", lambda *a: dict(do_mes))
    monkeypatch.setattr(
        custos, "_historico_por_ramo", lambda *a: {r: {AGO: m} for r, m in do_mes.items()}
    )
    monkeypatch.setattr(custos, "abertura", lambda *a: Abertura())
    monkeypatch.setattr(custos, "farol", lambda *a: BASE)

    def montar(escolha: str) -> Secao:
        monkeypatch.setattr(custos, "preferencia", lambda *a: escolha)
        faturado = sum((m["faturado"] for m in do_mes.values()), D(0))
        base = BaseVendas(
            tabelas=[],
            mes=MesDeVendas(SET, date(2026, 9, 30), faturado, D(1000), 0, 0, 0),
            mensal={},
            inicio=AGO,
            feriados=set(),
        )
        ctx = SimpleNamespace(sessao=SimpleNamespace(conn=None), cliente_id=None)
        s = secao_custos(ctx, base)  # type: ignore[arg-type]
        assert s is not None
        return s.secao

    return montar


def test_secao_no_automatico_com_outros(secao: Callable[[str], Secao]) -> None:
    s = secao("auto")

    assert [i.nome for i in s.indicadores] == [
        "CMV de a",
        "CMV de b",
        "CMV de c",
        "Outros (3 ramos)",
    ]
    outros = s.indicadores[-1]
    assert outros.id == "cmv-outros"
    # vazio 100/100, D e E 50/62,5 cada: 200 / 225
    assert outros.periodos.mes.valor.valor == D(200) / D(225) * 100
    assert s.seletor is not None
    assert s.seletor.id == "linhas-custo"
    assert s.seletor.valor == "auto"
    assert [o.id for o in s.seletor.opcoes] == ["auto", "1", "2", "3", "4", "5", "6", "7"]
    assert s.seletor.opcoes[0].rotulo == "Automático (80%)"


def test_secao_com_numero_fixo(secao: Callable[[str], Secao]) -> None:
    assert [i.nome for i in secao("1").indicadores] == ["CMV de a", "Outros (5 ramos)"]
    # todas as linhas com nome: em Outros, só o ramo vazio
    s = secao("7")
    assert [i.nome for i in s.indicadores][-1] == "Outros (1 ramo)"
    assert len(s.indicadores) == 6
    assert s.seletor is not None
    assert s.seletor.valor == "7"

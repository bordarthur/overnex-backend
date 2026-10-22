"""Seção Financeiro: inadimplência, cartões e prazos — sem banco."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.farol.parametro import PADROES
from app.telas.calendario import mais_dias_uteis
from app.telas.visao_geral.financeiro import (
    COLUNAS_RECEBER,
    PRAZO,
    PRAZO_RECEBIMENTO,
    Carteira,
    PontoSerie,
    _titulos,
    cartao_inadimplencia,
    kpis_financeiro,
    media_paga,
    ultimo_vencimento_vencido,
)
from app.telas.visao_geral.relativo import Serie, indicador_relativo
from app.telas.visao_geral.vendas import TabelaDoCliente

D = Decimal
HOJE = date(2026, 10, 21)


def _carteira(a_vencer: int = 9260, proximos: int = 4000, ate_30: int = 450) -> Carteira:
    # total de 10.000, 7,4% vencido
    return Carteira(
        a_vencer=D(a_vencer),
        proximos_dias=D(proximos),
        ate_30=D(ate_30),
        ate_60=D(140),
        ate_90=D(70),
        mais_90=D(80),
    )


def _serie(*pcts: str) -> list[PontoSerie]:
    """Fim de cada mês a partir de jan/2026, carteira de 1.000 e o % vencido."""
    return [
        PontoSerie(dia=date(2026, 1 + i, 28), vencido=D(p) * 10, aberto=D(1000))
        for i, p in enumerate(pcts)
    ]


def test_cartao_inadimplencia_contra_a_media_da_serie() -> None:
    cartao = cartao_inadimplencia(HOJE, _carteira(), _serie("5.6", "6.0"), PADROES["inadimplencia"])

    assert cartao is not None
    assert cartao.percentual.valor == D("7.4")
    # +1,6 p.p. sobre a média de 5,8%: atenção (crítico a partir de +2)
    assert cartao.status == "atencao"
    assert [t.texto for t in cartao.texto if t.destaque] == ["R$ 100", "R$ 7,40"]
    assert cartao.texto[-1].texto == " já venceram. A média de 2 meses é R$ 5,80."
    assert [(c.rotulo, c.status) for c in cartao.carteira] == [
        ("A vencer", "saudavel"),
        ("1 a 30 dias", "atencao"),
        ("31 a 60 dias", "critico"),
        ("61 a 90 dias", "critico"),
        ("Mais de 90", "critico"),
    ]
    assert cartao.carteira[0].percentual == D("92.6")
    assert cartao.serie.rotulos == ["jan-26", "fev-26"]
    assert cartao.serie.media == D("5.8")
    assert cartao.nota.startswith("Carteira em aberto em 21/10: R$ 10 mil.")


def test_sem_serie_ou_sem_carteira_nao_ha_cartao() -> None:
    base = PADROES["inadimplencia"]
    assert cartao_inadimplencia(HOJE, _carteira(), _serie("5"), base) is None
    vazia = Carteira(*(D(0),) * 6)
    assert cartao_inadimplencia(HOJE, vazia, _serie("5", "6"), base) is None


def test_media_paga_so_dos_meses_fechados() -> None:
    pagos = {date(2026, m, 1): {"valor": D(m * 100), "dias_valor": D(0)} for m in range(3, 11)}
    # out é o mês aberto: fica de fora; abr a set → (400 + … + 900) / 6
    assert media_paga(pagos, HOJE) == D(650)
    assert media_paga({}, HOJE) is None


def test_kpis_com_cobertura_do_a_pagar() -> None:
    kpis = kpis_financeiro(
        _carteira(), _carteira(proximos=580), D(1000), "atencao", PADROES["pagar_lancado"]
    )

    assert [k.rotulo for k in kpis] == [
        "A receber a vencer",
        "A receber vencido",
        "A pagar, 30 dias",
        "A pagar vencido",
    ]
    assert kpis[0].contexto == "carteira de R$ 10 mil"
    assert kpis[1].contexto == "7,4% da carteira"
    assert kpis[1].status == "atencao"
    assert kpis[2].contexto == "só o que está lançado: 58% da média paga"
    assert kpis[2].status == "atencao"  # entre 50% e 80%


def test_kpis_sem_tabela_a_pagar() -> None:
    kpis = kpis_financeiro(_carteira(), None, None, None, PADROES["pagar_lancado"])
    assert [k.rotulo for k in kpis] == ["A receber a vencer", "A receber vencido"]


def test_prazo_de_recebimento_contra_a_media() -> None:
    # abr a set: 28 dias; out: 31 dias (ponderado pelo valor)
    pagos = {date(2026, m, 1): {"valor": D(100), "dias_valor": D(2800)} for m in range(4, 10)}
    pagos[date(2026, 10, 1)] = {"valor": D(200), "dias_valor": D(6200)}

    ind = indicador_relativo(
        PRAZO_RECEBIMENTO,
        date(2026, 10, 1),
        Serie(pagos, PRAZO),
        None,
        PADROES["prazo_recebimento"],
    )

    assert ind is not None
    mes = ind.periodos.mes
    assert mes.valor.valor == D(31)
    assert mes.status == "atencao"  # +3 dias: até +5 é atenção
    assert mes.delta is not None
    assert (mes.delta.valor, mes.delta.unidade) == (D(3), "dias")
    assert mes.referencia == (
        "dos títulos recebidos no mês, contra a média de 6 meses (abr a set): 28 dias"
    )
    assert (ind.gauge.min, ind.gauge.max) == (D(15), D(45))


def test_d_mais_2_conta_dias_uteis() -> None:
    # sexta 16/10 → segunda 19 e terça 20
    assert mais_dias_uteis(date(2026, 10, 16), 2, set()) == date(2026, 10, 20)
    # sexta 30/10 com o feriado de segunda 2/11 → terça 3 e quarta 4
    assert mais_dias_uteis(date(2026, 10, 30), 2, {date(2026, 11, 2)}) == date(2026, 11, 4)


def test_vencido_no_proprio_d_mais_2() -> None:
    # quarta 21/10: o de segunda 19 tem D+2 hoje, já venceu (como no Power BI); o de
    # terça 20 só em 22/10
    assert ultimo_vencimento_vencido(HOJE, set()) == date(2026, 10, 19)
    # terça 3/11 com o feriado de 2/11: o de sexta 30/10 tem D+2 só em 4/11
    assert ultimo_vencimento_vencido(date(2026, 11, 3), {date(2026, 11, 2)}) == date(2026, 10, 29)
    assert ultimo_vencimento_vencido(date(2026, 11, 3), set()) == date(2026, 11, 1)


def test_compensando_fica_fora_dos_cartoes_e_dentro_da_carteira() -> None:
    c = Carteira(
        a_vencer=D(9000),
        proximos_dias=D(4000),
        ate_30=D(450),
        ate_60=D(140),
        ate_90=D(70),
        mais_90=D(80),
        compensando=D(260),
    )
    assert c.total == D(10000)

    kpis = kpis_financeiro(c, c, None, None, PADROES["pagar_lancado"])
    assert kpis[0].valor.valor == D(9000)
    assert kpis[0].contexto == "carteira de R$ 10 mil; R$ 260 em compensação (D+2)"
    assert kpis[3].contexto == "títulos em aberto já vencidos; R$ 260 em compensação (D+2)"

    cartao = cartao_inadimplencia(HOJE, c, _serie("5", "6"), PADROES["inadimplencia"])
    assert cartao is not None
    assert [(i.rotulo, i.status) for i in cartao.carteira][:2] == [
        ("A vencer", "saudavel"),
        ("Em compensação", "saudavel"),
    ]
    assert sum(i.percentual for i in cartao.carteira) == 100


def test_titulos_leem_coluna_date_sem_converter() -> None:
    ctx = SimpleNamespace(cliente_id=None, empresas=(), empresa_id=None)
    texto = TabelaDoCliente("f_receitas_pj", COLUNAS_RECEBER)
    ja_date = TabelaDoCliente(
        "f_receitas_pj", COLUNAS_RECEBER, frozenset({"en_dataemissao", "en_datavencimento"})
    )

    sql_texto = _titulos([texto], ctx).as_string(None)  # type: ignore[arg-type]
    sql_date = _titulos([ja_date], ctx).as_string(None)  # type: ignore[arg-type]

    assert sql_texto.count("to_date") == 9  # 3 formatos × 3 datas
    # só en_datapagamento continua em texto
    assert sql_date.count("to_date") == 3
    assert '"en_dataemissao" as "emissao"' in sql_date

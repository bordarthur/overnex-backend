"""Seção Financeiro: inadimplência, próximos dias, a pagar lançado e KPIs — sem banco."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.farol.parametro import PADROES
from app.telas.calendario import mais_dias_uteis
from app.telas.visao_geral.contra_referencia import Mensal
from app.telas.visao_geral.financeiro import (
    COLUNAS_RECEBER,
    Carteira,
    PontoSerie,
    _titulos,
    cartao_inadimplencia,
    cartao_pagar_lancado,
    cartao_proximos_dias,
    falta_lancar,
    kpis_financeiro,
    media_paga,
    ultimo_vencimento_vencido,
)
from app.telas.visao_geral.vendas import TabelaDoCliente

D = Decimal
HOJE = date(2026, 10, 21)


def _carteira(a_vencer: int = 9260, proximos: int = 4000, ate_30: int = 450) -> Carteira:
    # total de 10.000, 7,4% vencido
    return Carteira(
        a_vencer=D(a_vencer),
        proximos={7: D(1000), 15: D(2000), 30: D(proximos)},
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
    vazia = Carteira(D(0), {}, D(0), D(0), D(0), D(0))
    assert cartao_inadimplencia(HOJE, vazia, _serie("5", "6"), base) is None


def test_media_paga_so_dos_meses_fechados() -> None:
    pagos = {date(2026, m, 1): {"valor": D(m * 100)} for m in range(3, 11)}
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
        proximos={30: D(4000)},
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


def _pagos(*valores: int) -> Mensal:
    """Pago em cada mês a partir de abr/2026 (com HOJE em outubro, abr a set são fechados)."""
    return {date(2026, 4 + i, 1): {"valor": D(v)} for i, v in enumerate(valores)}


def _a_vencer(d7: int, d15: int, d30: int) -> Carteira:
    return Carteira(
        a_vencer=D(d30),
        proximos={7: D(d7), 15: D(d15), 30: D(d30)},
        ate_30=D(0),
        ate_60=D(0),
        ate_90=D(0),
        mais_90=D(0),
    )


def test_falta_lancar_proporcional_aos_dias_e_nunca_negativa() -> None:
    # média de 6.000 por mês: 7 dias → 1.400
    assert falta_lancar(D(6000), 7, D(400)) == D(1000)
    # já lançado mais do que a média proporcional: nada falta
    assert falta_lancar(D(6000), 7, D(2000)) == 0
    assert falta_lancar(D(6000), 30, D(6000)) == 0


def test_proximos_dias_com_e_sem_o_que_falta_lancar() -> None:
    receber = _a_vencer(1230, 2680, 5260)
    pagar = _a_vencer(530, 1010, 3660)

    cartao = cartao_proximos_dias(HOJE, receber, pagar, D(6000))

    assert cartao.tipo == "proximos-dias"
    assert [m.id for m in cartao.modos] == ["lancado", "com-falta"]
    assert [m.com_falta for m in cartao.modos] == [False, True]
    assert [j.titulo for j in cartao.janelas] == ["7 dias", "15 dias", "30 dias"]
    # hoje 21/10: 28/10, 05/11 e 20/11
    assert [j.ate for j in cartao.janelas] == ["até 28/10", "até 05/11", "até 20/11"]
    sete = cartao.janelas[0]
    assert (sete.receber.valor, sete.pagar.valor) == (D(1230), D(530))
    # 6.000 × 7/30 = 1.400; lançado 530 → falta 870
    assert sete.falta.valor == D(870)
    lancado, com_falta = sete.fim["lancado"], sete.fim["com-falta"]
    assert lancado.valor.valor == D(700)
    assert (lancado.status, lancado.texto) == ("saudavel", "entra mais do que sai")
    assert com_falta.valor.valor == D(-170)
    assert (com_falta.status, com_falta.texto) == ("atencao", "sai mais do que entra")
    # 30 dias: falta 6.000 − 3.660 = 2.340; fim 1.600 − 2.340
    trinta = cartao.janelas[2]
    assert trinta.falta.valor == D(2340)
    assert trinta.fim["com-falta"].valor.valor == D(-740)
    # status do modo: o pior das janelas
    assert [m.status for m in cartao.modos] == ["saudavel", "atencao"]
    assert cartao.nota.startswith("Só o lançado considera")
    assert "(R$ 6 mil, proporcional aos dias)" in cartao.nota
    assert cartao.saldo is None
    assert cartao.minimo is None
    assert cartao.legenda.saldo is None


def test_proximos_dias_janela_sem_titulo_a_pagar_e_falta_zero() -> None:
    receber = _a_vencer(500, 900, 1500)
    # nada a pagar em 7 dias; em 30, mais do que a média
    pagar = _a_vencer(0, 300, 7000)

    cartao = cartao_proximos_dias(HOJE, receber, pagar, D(6000))

    sete, _, trinta = cartao.janelas
    assert sete.pagar.valor == 0
    assert sete.falta.valor == D(1400)
    assert sete.fim["lancado"].valor.valor == D(500)
    assert sete.fim["com-falta"].valor.valor == D(-900)
    # lançado acima da média: falta 0, e os dois modos terminam igual
    assert trinta.falta.valor == 0
    assert trinta.fim["lancado"].valor.valor == D(-5500)
    assert trinta.fim["com-falta"].valor.valor == D(-5500)
    assert [m.status for m in cartao.modos] == ["atencao", "atencao"]


def test_proximos_dias_sem_media_paga_so_o_lancado() -> None:
    cartao = cartao_proximos_dias(HOJE, _a_vencer(1, 2, 3), _a_vencer(0, 0, 0), None)
    assert [m.id for m in cartao.modos] == ["lancado"]
    assert all(set(j.fim) == {"lancado"} and j.falta.valor == 0 for j in cartao.janelas)
    assert "não há média paga" in cartao.nota


def test_pagar_lancado_contra_a_media_paga() -> None:
    pagos = _pagos(6120, 6280, 6410, 6220, 6350, 6480)
    # outubro é o mês aberto: fica fora da média e das barras
    pagos[date(2026, 10, 1)] = {"valor": D(99999)}

    cartao = cartao_pagar_lancado(HOJE, _a_vencer(530, 1010, 3660), pagos, PADROES["pagar_lancado"])

    assert cartao is not None
    assert cartao.tipo == "pagar-lancado"
    media = D(37860) / 6  # 6.310
    assert cartao.media.valor.valor == media
    assert cartao.media.rotulo == "média paga R$ 6 mil"
    assert cartao.percentual.valor == D(3660) / media * 100  # ~58%
    assert (cartao.percentual.unidade, cartao.percentual.casas) == ("pct", 0)
    assert cartao.status == "atencao"  # entre 50% e 80%
    assert cartao.texto == "da média paga por mês já está lançada para os próximos 30 dias."
    assert cartao.nota == "De abril a setembro, a empresa pagou em média R$ 6 mil por mês."
    assert [p.rotulo for p in cartao.pagos] == [
        "abr-26",
        "mai-26",
        "jun-26",
        "jul-26",
        "ago-26",
        "set-26",
    ]
    assert cartao.proximos.rotulo == "próximos 30 dias"
    assert cartao.proximos.lancado.valor.valor == D(3660)
    assert cartao.proximos.falta.valor.valor == media - D(3660)
    assert cartao.explicacao is not None
    assert cartao.explicacao.startswith("As barras cinza")


def test_pagar_lancado_status_e_falta_zero() -> None:
    pagos = _pagos(1000)
    farol = PADROES["pagar_lancado"]
    critico = cartao_pagar_lancado(HOJE, _a_vencer(0, 0, 400), pagos, farol)
    assert critico is not None
    assert critico.status == "critico"  # 40%
    assert critico.nota == "Em abril, a empresa pagou R$ 1 mil."
    saudavel = cartao_pagar_lancado(HOJE, _a_vencer(0, 0, 1200), pagos, farol)
    assert saudavel is not None
    assert saudavel.status == "saudavel"  # 120%
    # lançado acima da média: nada falta
    assert saudavel.proximos.falta.valor.valor == 0


def test_pagar_lancado_sem_meses_fechados_nao_sai() -> None:
    farol = PADROES["pagar_lancado"]
    pagar = _a_vencer(0, 0, 100)
    assert cartao_pagar_lancado(HOJE, pagar, {}, farol) is None
    # só o mês aberto
    assert cartao_pagar_lancado(HOJE, pagar, {date(2026, 10, 1): {"valor": D(5)}}, farol) is None


def test_pagar_lancado_nota_com_anos_diferentes() -> None:
    pagos = {date(2025, 12, 1): {"valor": D(100)}, date(2026, 1, 1): {"valor": D(300)}}
    cartao = cartao_pagar_lancado(
        date(2026, 2, 10), _a_vencer(0, 0, 100), pagos, PADROES["pagar_lancado"]
    )
    assert cartao is not None
    assert cartao.nota == (
        "De dezembro de 2025 a janeiro de 2026, a empresa pagou em média R$ 200 por mês."
    )


def test_inadimplencia_com_titulo_e_subtitulo() -> None:
    cartao = cartao_inadimplencia(HOJE, _carteira(), _serie("5", "6"), PADROES["inadimplencia"])
    assert cartao is not None
    assert cartao.titulo == "Inadimplência corrente"
    assert cartao.subtitulo == "Quanto da carteira a receber já venceu, e há quanto tempo"
    assert cartao.explicacao is None

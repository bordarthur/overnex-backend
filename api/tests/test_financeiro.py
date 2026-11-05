"""Seção Financeiro: inadimplência, próximos dias, a pagar lançado e KPIs — sem banco."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.farol.parametro import PADROES
from app.telas.calendario import mais_dias_uteis
from app.telas.visao_geral.contra_referencia import Mensal
from app.telas.visao_geral.financeiro import (
    COLUNAS_PAGAR,
    COLUNAS_RECEBER,
    GRUPO_UNICO,
    Carteira,
    GrupoPagamento,
    PontoSerie,
    _ler,
    _titulos,
    cartao_cobertura,
    cartao_inadimplencia,
    cartao_pagar_lancado,
    cartao_proximos_dias,
    falta_lancar,
    grupos_da_leitura,
    kpis_financeiro,
    media_paga,
    resumo_financeiro,
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
        ("Mais de 90 dias", "critico"),
    ]
    assert cartao.carteira[0].percentual == D("92.6")
    # o a vencer fica só na barra
    assert [c.so_na_barra for c in cartao.carteira] == [True, None, None, None, None]
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


def test_kpis_sem_saldo_comecam_no_a_receber() -> None:
    kpis = kpis_financeiro(_carteira(), _carteira(proximos=580), D(1000), PADROES["pagar_lancado"])

    assert [k.rotulo for k in kpis] == ["A receber, 30 dias", "A pagar, 30 dias", "A pagar vencido"]
    assert kpis[0].valor.valor == D(4000)
    assert kpis[0].contexto == "a vencer no total: R$ 9 mil; carteira: R$ 10 mil"
    assert kpis[0].status is None
    assert kpis[1].contexto == "só o que está lançado: 58% da média paga"
    assert kpis[1].status == "atencao"  # entre 50% e 80%


def test_kpis_com_saldo_em_conta() -> None:
    kpis = kpis_financeiro(_carteira(), None, None, PADROES["pagar_lancado"], D(1420000))
    assert [k.rotulo for k in kpis] == ["Saldo em conta hoje", "A receber, 30 dias"]
    assert (kpis[0].valor.valor, kpis[0].contexto) == (D(1420000), "todas as contas")


def test_kpis_sem_tabela_a_pagar() -> None:
    kpis = kpis_financeiro(_carteira(), None, None, PADROES["pagar_lancado"])
    assert [k.rotulo for k in kpis] == ["A receber, 30 dias"]


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

    kpis = kpis_financeiro(c, c, None, PADROES["pagar_lancado"])
    assert kpis[0].contexto == "a vencer no total: R$ 9 mil; carteira: R$ 10 mil"
    assert kpis[2].contexto == "títulos em aberto já vencidos; R$ 260 em compensação (D+2)"

    cartao = cartao_inadimplencia(HOJE, c, _serie("5", "6"), PADROES["inadimplencia"])
    assert cartao is not None
    assert [(i.rotulo, i.status) for i in cartao.carteira][:2] == [
        ("A vencer", "saudavel"),
        ("Em compensação", "saudavel"),
    ]
    # a vencer e em compensação ficam só na barra; os quadros são das faixas de atraso
    assert [i.so_na_barra for i in cartao.carteira] == [True, True, None, None, None, None]
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


def test_proximos_dias_sem_saldo_com_a_projecao_so_do_a_pagar() -> None:
    receber = _a_vencer(1230, 2680, 5260)
    pagar = _a_vencer(530, 1010, 3660)

    cartao = cartao_proximos_dias(HOJE, receber, pagar, D(6000))

    assert cartao.tipo == "proximos-dias"
    assert [m.id for m in cartao.modos] == ["lancado", "projecao"]
    assert [(m.com_falta, m.com_novas) for m in cartao.modos] == [(False, False), (True, True)]
    assert [m.rotulo for m in cartao.modos] == ["Só o lançado", "Com a projeção"]
    assert [j.titulo for j in cartao.janelas] == ["7 dias", "15 dias", "30 dias"]
    # hoje 21/10: 28/10, 05/11 e 20/11
    assert [j.ate for j in cartao.janelas] == ["até 28/10", "até 05/11", "até 20/11"]
    sete = cartao.janelas[0]
    assert (sete.receber.valor, sete.pagar.valor) == (D(1230), D(530))
    # 6.000 × 7/30 = 1.400; lançado 530 → falta 870
    assert sete.falta.valor == D(870)
    # sem média recebida: nada de vendas novas
    assert sete.novas is not None
    assert sete.novas.valor == 0
    lancado, projecao = sete.fim["lancado"], sete.fim["projecao"]
    assert lancado.valor.valor == D(700)
    assert (lancado.status, lancado.texto) == ("saudavel", "entra mais do que sai")
    # sem saldo, a falta da janela é atenção, não crítico: o caixa pode cobrir
    assert projecao.valor.valor == D(-170)
    assert (projecao.status, projecao.texto) == ("atencao", "sai mais do que entra")
    # 30 dias: falta 6.000 − 3.660 = 2.340; fim 1.600 − 2.340
    trinta = cartao.janelas[2]
    assert trinta.falta.valor == D(2340)
    assert trinta.fim["projecao"].valor.valor == D(-740)
    # status do modo: o pior das janelas
    assert [m.status for m in cartao.modos] == ["saudavel", "atencao"]
    assert cartao.nota == (
        "Só o lançado usa o que já está no sistema; com a projeção, cada janela completa o a "
        "pagar até a média paga (R$ 6 mil por mês), proporcional aos dias, porque contas "
        "ainda não lançadas também passam pelo caixa. Sem recebimentos nos meses fechados, "
        "não há média recebida para projetar as vendas novas."
    )
    assert cartao.subtitulo.startswith("O que vence a receber, menos o que vence a pagar")
    assert cartao.explicacao is not None
    assert cartao.explicacao.endswith(
        "O saldo em conta ainda não entra: o sistema não tem essa fonte."
    )
    assert cartao.saldo is None
    assert cartao.minimo is None
    assert cartao.legenda.saldo is None
    assert cartao.legenda.novas == "Vendas novas, pela média recebida"
    assert cartao.legenda.falta == "Contas a lançar, pela média paga"


def test_proximos_dias_com_saldo_minimo_e_projecao_como_no_mockup() -> None:
    hoje = date(2026, 10, 12)
    receber = _a_vencer(1_230_000, 2_680_000, 5_260_000)
    pagar = _a_vencer(530_000, 1_010_000, 3_660_000)

    cartao = cartao_proximos_dias(
        hoje, receber, pagar, D(6_310_000), D(6_100_000), D(1_420_000), D(500_000)
    )

    sete, quinze, trinta = cartao.janelas
    assert [j.ate for j in cartao.janelas] == ["até 19/10", "até 27/10", "até 11/11"]
    # 7 dias: novas 6,10 mi × 7/30 − 1,23 mi; falta 6,31 mi × 7/30 − 530 mil
    assert sete.novas is not None
    assert round(sete.novas.valor, -3) == 193_000
    assert round(sete.falta.valor, -3) == 942_000
    assert sete.fim["lancado"].valor.valor == D(2_120_000)
    assert round(sete.fim["projecao"].valor.valor, -3) == 1_371_000
    assert quinze.novas is not None
    assert quinze.novas.valor == D(370_000)
    assert quinze.falta.valor == D(2_145_000)
    # 30 dias: 1,42 + 5,26 + 0,84 − 3,66 − 2,65
    assert trinta.novas is not None
    assert trinta.novas.valor == D(840_000)
    assert trinta.falta.valor == D(2_650_000)
    assert trinta.fim["lancado"].valor.valor == D(3_020_000)
    assert trinta.fim["projecao"].valor.valor == D(1_210_000)
    assert {(f.status, f.texto) for j in cartao.janelas for f in j.fim.values()} == {
        ("saudavel", "caixa no fim")
    }
    assert [m.status for m in cartao.modos] == ["saudavel", "saudavel"]
    assert cartao.saldo is not None
    assert cartao.saldo.valor == D(1_420_000)
    assert cartao.minimo is not None
    assert (cartao.minimo.valor, cartao.minimo.rotulo) == (D(500_000), "caixa mínimo R$ 500 mil")
    assert cartao.legenda.saldo == "Saldo hoje"
    assert cartao.subtitulo == (
        "O saldo de hoje, mais o que entra, menos o que sai: onde o caixa termina em cada janela"
    )
    assert cartao.explicacao == (
        "Cada linha começa no saldo de hoje e soma o que vence a receber na janela. Embaixo, o "
        "que vence a pagar volta do fim dessa barra. O traço marca onde o caixa termina; a "
        "linha tracejada é o caixa mínimo. Na projeção, o hachurado claro são vendas novas, "
        "pela média recebida, e o escuro, contas ainda não lançadas, pela média paga."
    )
    assert cartao.nota == (
        "Só o lançado usa o que já está no sistema; com a projeção, cada janela completa o a "
        "receber até a média recebida (R$ 6,10 mi por mês) e o a pagar até a média paga "
        "(R$ 6,31 mi por mês), proporcionais aos dias, porque vendas novas e contas ainda não "
        "lançadas também passam pelo caixa."
    )
    assert resumo_financeiro(hoje, cartao, D(6_310_000)) == (
        "Faltam lançar R$ 2,65 mi de contas a pagar; com a projeção, o caixa de 30 dias fecha "
        "em R$ 1,21 mi."
    )


def test_proximos_dias_com_saldo_abaixo_do_minimo_e_negativo() -> None:
    # saldo 100, mínimo 500; sem médias, só o lançado
    cartao = cartao_proximos_dias(
        HOJE, _a_vencer(0, 0, 0), _a_vencer(50, 200, 400), None, None, D(100), D(500)
    )

    fins = [j.fim["lancado"] for j in cartao.janelas]
    assert [(f.valor.valor, f.status, f.texto) for f in fins] == [
        (D(50), "atencao", "abaixo do mínimo"),
        (D(-100), "critico", "caixa negativo"),
        (D(-300), "critico", "caixa negativo"),
    ]
    assert [m.status for m in cartao.modos] == ["critico"]


def test_proximos_dias_caixa_minimo_so_vale_com_saldo() -> None:
    cartao = cartao_proximos_dias(
        HOJE, _a_vencer(100, 100, 100), _a_vencer(0, 0, 0), None, None, None, D(500)
    )
    assert cartao.minimo is None
    assert cartao.janelas[0].fim["lancado"].status == "saudavel"


def test_proximos_dias_janela_sem_titulo_a_pagar_e_falta_zero() -> None:
    receber = _a_vencer(500, 900, 1500)
    # nada a pagar em 7 dias; em 30, mais do que a média
    pagar = _a_vencer(0, 300, 7000)

    cartao = cartao_proximos_dias(HOJE, receber, pagar, D(6000))

    sete, _, trinta = cartao.janelas
    assert sete.pagar.valor == 0
    assert sete.falta.valor == D(1400)
    assert sete.fim["lancado"].valor.valor == D(500)
    assert sete.fim["projecao"].valor.valor == D(-900)
    # lançado acima da média: falta 0, e os dois modos terminam igual
    assert trinta.falta.valor == 0
    assert trinta.fim["lancado"].valor.valor == D(-5500)
    assert trinta.fim["projecao"].valor.valor == D(-5500)
    assert [m.status for m in cartao.modos] == ["atencao", "atencao"]


def test_proximos_dias_vendas_novas_nunca_negativas() -> None:
    # a receber de 30 dias acima da média recebida: nada de vendas novas
    cartao = cartao_proximos_dias(HOJE, _a_vencer(10, 20, 9000), _a_vencer(0, 0, 0), None, D(3000))
    novas = [j.novas.valor if j.novas else None for j in cartao.janelas]
    assert novas == [D(690), D(1480), 0]
    assert [j.falta.valor for j in cartao.janelas] == [0, 0, 0]
    assert "Sem pagamentos nos meses fechados" in cartao.nota


def test_proximos_dias_sem_media_so_o_lancado() -> None:
    cartao = cartao_proximos_dias(HOJE, _a_vencer(1, 2, 3), _a_vencer(0, 0, 0), None)
    assert [m.id for m in cartao.modos] == ["lancado"]
    assert all(set(j.fim) == {"lancado"} and j.falta.valor == 0 for j in cartao.janelas)
    assert all(j.novas is None for j in cartao.janelas)
    assert cartao.legenda.novas is None
    assert "não há média" in cartao.nota


def test_resumo_sem_saldo_e_sem_projecao() -> None:
    sobra = cartao_proximos_dias(HOJE, _a_vencer(0, 0, 9000), _a_vencer(0, 0, 3660), D(6000))
    assert resumo_financeiro(HOJE, sobra, D(6000)) == (
        "Faltam lançar R$ 2 mil de contas a pagar; com a projeção, entram R$ 3 mil a mais do "
        "que saem nos próximos 30 dias."
    )
    falta = cartao_proximos_dias(HOJE, _a_vencer(0, 0, 1000), _a_vencer(0, 0, 7000), D(6000))
    assert resumo_financeiro(HOJE, falta, D(6000)) == (
        "As contas a pagar dos próximos 30 dias já estão lançadas; com a projeção, saem "
        "R$ 6 mil a mais do que entram nos próximos 30 dias."
    )
    so_lancado = cartao_proximos_dias(HOJE, _a_vencer(1, 2, 3), _a_vencer(0, 0, 0), None)
    assert resumo_financeiro(HOJE, so_lancado, None) == (
        "Posição em 21/10/2026, com os títulos lançados no sistema."
    )
    assert resumo_financeiro(HOJE, None, D(6000)).startswith("Posição em")


def _grupo(lancado: int, *pagos: int, nome: str = GRUPO_UNICO) -> GrupoPagamento:
    return GrupoPagamento(nome, D(lancado), _pagos(*pagos))


def test_pagar_lancado_um_grupo_contra_a_media_paga() -> None:
    grupo = _grupo(3660, 6120, 6280, 6410, 6220, 6350, 6480)
    # outubro é o mês aberto: fica fora da média e das barras
    grupo.pagos[date(2026, 10, 1)] = {"valor": D(99999)}

    cartao = cartao_pagar_lancado(HOJE, [grupo], PADROES["pagar_lancado"])

    assert cartao is not None
    assert cartao.tipo == "pagar-lancado"
    media = D(37860) / 6  # 6.310
    assert cartao.percentual.valor == D(3660) / media * 100  # ~58%
    assert (cartao.percentual.unidade, cartao.percentual.casas) == ("pct", 0)
    assert cartao.status == "atencao"  # entre 50% e 80%
    assert cartao.texto == "da média paga por mês já está lançada para os próximos 30 dias."
    assert cartao.nota == (
        "Faltam R$ 3 mil para chegar à média paga por mês (R$ 6 mil, de abril a setembro)."
    )
    assert cartao.rotulo_grupos == "Pago por mês e lançado para 30 dias"
    assert cartao.explicacao is not None
    assert cartao.explicacao.endswith("O plano de contas ainda não separa os pagamentos por grupo.")
    (g,) = cartao.grupos
    assert g.nome == "Todas as contas a pagar"
    assert (g.status, g.percentual.valor) == ("atencao", cartao.percentual.valor)
    assert g.texto == "lançado; falta R$ 3 mil"
    assert [p.rotulo for p in g.pagos] == ["abr", "mai", "jun", "jul", "ago", "set"]
    assert (g.media.valor.valor, g.media.rotulo) == (media, "média R$ 6 mil")
    assert g.proximos.rotulo == "30 dias"
    assert g.proximos.lancado.valor.valor == D(3660)
    assert g.proximos.falta.valor.valor == media - D(3660)
    assert g.nota is None


def test_pagar_lancado_status_e_falta_zero() -> None:
    farol = PADROES["pagar_lancado"]
    critico = cartao_pagar_lancado(HOJE, [_grupo(400, 1000)], farol)
    assert critico is not None
    assert critico.status == "critico"  # 40%
    assert critico.nota == "Faltam R$ 600 para chegar à média paga por mês (R$ 1 mil, em abril)."
    saudavel = cartao_pagar_lancado(HOJE, [_grupo(1200, 1000)], farol)
    assert saudavel is not None
    assert saudavel.status == "saudavel"  # 120%
    assert saudavel.nota == "O lançado já cobre a média paga por mês (R$ 1 mil, em abril)."
    # lançado acima da média: nada falta
    assert saudavel.grupos[0].proximos.falta.valor.valor == 0
    assert saudavel.grupos[0].texto == "lançado; nada falta"


def test_pagar_lancado_sem_meses_fechados_nao_sai() -> None:
    farol = PADROES["pagar_lancado"]
    assert cartao_pagar_lancado(HOJE, [GrupoPagamento(GRUPO_UNICO, D(100))], farol) is None
    # só o mês aberto
    so_aberto = GrupoPagamento(GRUPO_UNICO, D(100), {date(2026, 10, 1): {"valor": D(5)}})
    assert cartao_pagar_lancado(HOJE, [so_aberto], farol) is None


def test_pagar_lancado_nota_com_anos_diferentes() -> None:
    pagos = {date(2025, 12, 1): {"valor": D(100)}, date(2026, 1, 1): {"valor": D(300)}}
    cartao = cartao_pagar_lancado(
        date(2026, 2, 10),
        [GrupoPagamento(GRUPO_UNICO, D(100), pagos)],
        PADROES["pagar_lancado"],
    )
    assert cartao is not None
    assert cartao.nota == (
        "Faltam R$ 100 para chegar à média paga por mês (R$ 200, de dezembro de 2025 a "
        "janeiro de 2026)."
    )
    assert [p.rotulo for p in cartao.grupos[0].pagos] == ["dez-25", "jan-26"]


def _mi(*valores: str) -> Mensal:
    """Pago em milhões em cada mês a partir de abr/2026."""
    return {date(2026, 4 + i, 1): {"valor": D(v) * 1_000_000} for i, v in enumerate(valores)}


def test_pagar_lancado_por_grupo_como_no_mockup() -> None:
    hoje = date(2026, 10, 12)
    grupos = [
        GrupoPagamento(
            "Fornecedores", D(2_860_000), _mi("3.86", "3.70", "4.12", "3.86", "4.07", "3.79")
        ),
        GrupoPagamento(
            "Custos fixos, despesas e impostos",
            D(610_000),
            _mi("1.97", "1.77", "1.93", "1.99", "2.01", "2.09"),
        ),
        GrupoPagamento("Dívida", D(190_000), _mi("0.19", "0.19", "0.31", "0.19", "0.19", "0.19")),
        GrupoPagamento(
            "Capex", D(0), _mi("0.10", "0.62", "0.05", "0.18", "0.08", "0.41"), sem_farol=True
        ),
    ]

    cartao = cartao_pagar_lancado(hoje, grupos, PADROES["pagar_lancado"])

    assert cartao is not None
    # 3,66 mi lançados contra 6,31 mi de média: 58%
    assert round(cartao.percentual.valor) == 58
    assert cartao.status == "atencao"
    assert cartao.nota == (
        "Faltam R$ 2,65 mi: R$ 1,35 mi em custos fixos, despesas e impostos, R$ 1,04 mi em "
        "fornecedores, R$ 240 mil em capex e R$ 20 mil em dívida."
    )
    assert cartao.rotulo_grupos == "Por grupo de pagamento"
    assert cartao.explicacao == (
        "Em cada grupo (fornecedores, custos fixos, despesas e impostos, dívida e capex), as "
        "barras cinza são o que a empresa pagou por mês. A barra da direita é o que já está "
        "lançado para vencer nos próximos 30 dias; o hachurado é o que falta para chegar à "
        "média do grupo. Capex oscila muito: confira se há investimento previsto antes de "
        "tratar como falta."
    )
    resumo = [
        (g.nome, g.status, round(g.percentual.valor), g.texto, g.media.rotulo)
        for g in cartao.grupos
    ]
    assert resumo == [
        ("Fornecedores", "atencao", 73, "lançado; falta R$ 1,04 mi", "média R$ 3,90 mi"),
        (
            "Custos fixos, despesas e impostos",
            "critico",
            31,
            "lançado; falta R$ 1,35 mi",
            "média R$ 1,96 mi",
        ),
        ("Dívida", "saudavel", 90, "lançado; falta R$ 20 mil", "média R$ 210 mil"),
        ("Capex", None, 0, "lançado; falta R$ 240 mil", "média R$ 240 mil"),
    ]
    assert [g.nota for g in cartao.grupos[:3]] == [None, None, None]
    assert cartao.grupos[3].nota == (
        "Oscila de R$ 50 mil a R$ 620 mil por mês. Confira se há investimento previsto antes "
        "de tratar como falta."
    )
    assert [p.rotulo for p in cartao.grupos[0].pagos] == ["abr", "mai", "jun", "jul", "ago", "set"]


def test_pagar_lancado_grupo_sem_pagamento_num_mes_conta_zero() -> None:
    farol = PADROES["pagar_lancado"]
    a = GrupoPagamento("Fornecedores", D(100), _pagos(300, 300))
    # só pagou em maio; sem nada pago, o grupo sai do cartão
    b = GrupoPagamento("Dívida", D(0), {date(2026, 5, 1): {"valor": D(100)}})
    c = GrupoPagamento("Capex", D(50), {}, sem_farol=True)

    cartao = cartao_pagar_lancado(HOJE, [a, b, c], farol)

    assert cartao is not None
    assert [g.nome for g in cartao.grupos] == ["Fornecedores", "Dívida"]
    divida = cartao.grupos[1]
    assert [p.valor.valor for p in divida.pagos] == [0, D(100)]
    assert divida.media.valor.valor == D(50)
    # total: 150 lançados contra (300 + 400) / 2
    assert cartao.percentual.valor == D(150) / D(350) * 100
    assert cartao.nota == "Faltam R$ 250: R$ 200 em fornecedores e R$ 50 em dívida."


def test_cobertura_como_no_mockup() -> None:
    hoje = date(2026, 10, 12)
    dias = [14, 13, 13, 12, 12, 13, 12, 11, 10, 9, 8]
    historico = {
        date(2025, 11 + i, 1) if i < 2 else date(2026, i - 1, 1): D(d) for i, d in enumerate(dias)
    }
    # um mês a mais no começo: só os 11 fechados mais recentes entram
    historico[date(2025, 10, 1)] = D(30)

    cartao = cartao_cobertura(hoje, D(1_420_000), D(6_310_000), historico)

    assert cartao is not None
    assert cartao.tipo == "cobertura"
    # 1,42 mi ÷ (6,31 mi ÷ 30) = 6,75 dias
    assert (cartao.dias.valor, cartao.dias.unidade) == (7, "dias")
    assert [(t.texto, t.destaque) for t in cartao.texto] == [
        ("Sem nada entrar, o saldo acaba em ", False),
        ("19/10", True),
        (".", False),
    ]
    # média dos 12 pontos (11 meses e hoje): 134 / 12 = 11,2
    assert cartao.nota == "Eram 8 dias em setembro. A média de 12 meses é 11."
    # 7 abaixo de 75% da média: crítico
    assert cartao.status == "critico"
    r = cartao.regua
    assert (r.total, r.cobertos, r.referencia) == (30, 7, 11)
    assert (r.rotulo_cobertos, r.rotulo_referencia) == ("hoje: 7 dias", "média: 11")
    assert (r.inicio, r.fim) == ("12/10", "11/11")
    s = cartao.serie
    assert s.titulo == "Últimos 12 meses, em dias de cobertura"
    assert s.rotulos[0] == "nov-25"
    assert s.rotulos[-2:] == ["set-26", "hoje"]
    assert s.valores == [*dias, 7]
    assert s.media.valor == D(134) / 12
    assert s.media.rotulo == "média 11"


def test_cobertura_status_e_saldo_negativo() -> None:
    historico = {date(2026, 9, 1): D(10)}
    # 10 dias de 10 na média: saudável
    saudavel = cartao_cobertura(HOJE, D(1000), D(3000), historico)
    assert saudavel is not None
    assert saudavel.status == "saudavel"
    # 8 dias: média (10 + 8) / 2 = 9; 8 ≥ 75% de 9
    atencao = cartao_cobertura(HOJE, D(800), D(3000), historico)
    assert atencao is not None
    assert atencao.status == "atencao"
    negativo = cartao_cobertura(HOJE, D(-500), D(3000), historico)
    assert negativo is not None
    assert negativo.dias.valor == 0
    assert [t.texto for t in negativo.texto] == ["O saldo de hoje não paga nem um dia das contas."]
    # mais de 30 dias: a régua para no fim
    folgado = cartao_cobertura(HOJE, D(5000), D(3000), {date(2026, 9, 1): D(45)})
    assert folgado is not None
    assert (folgado.regua.cobertos, folgado.regua.referencia) == (30, 30)
    assert folgado.regua.rotulo_cobertos == "hoje: 50 dias"


def test_cobertura_sem_saldo_sem_media_ou_sem_historico_nao_sai() -> None:
    historico = {date(2026, 9, 1): D(10)}
    assert cartao_cobertura(HOJE, None, D(3000), historico) is None
    assert cartao_cobertura(HOJE, D(1000), None, historico) is None
    assert cartao_cobertura(HOJE, D(1000), D(3000), {}) is None
    # só o mês aberto no histórico
    assert cartao_cobertura(HOJE, D(1000), D(3000), {date(2026, 10, 1): D(10)}) is None


def test_inadimplencia_com_titulo_e_subtitulo() -> None:
    cartao = cartao_inadimplencia(HOJE, _carteira(), _serie("5", "6"), PADROES["inadimplencia"])
    assert cartao is not None
    assert cartao.titulo == "Inadimplência corrente"
    assert cartao.subtitulo == "Quanto da carteira a receber já venceu, e há quanto tempo"
    assert cartao.explicacao is None


# ---------- grupos de pagamento (cfg.conta_dre.grupo_pagamento) ----------


def test_sem_classificacao_fica_o_grupo_unico() -> None:
    # só contas sem grupo (ou nada lido): o cliente ainda não tem a classificação
    assert grupos_da_leitura({None: D(300)}, {None: _pagos(600)}) is None
    assert grupos_da_leitura({}, {}) is None


def test_grupos_na_ordem_do_cartao_com_capex_sem_farol() -> None:
    lido = grupos_da_leitura(
        {"capex": D(10), "fornecedores": D(100), None: D(7)},
        {"fornecedores": _pagos(200, 200), "capex": _pagos(0, 50), None: _pagos(30, 30)},
    )

    assert lido is not None
    grupos, sem_grupo = lido
    assert [(g.nome, g.lancado, g.sem_farol) for g in grupos] == [
        ("Fornecedores", D(100), False),
        ("Custos fixos, despesas e impostos", D(0), False),
        ("Dívida", D(0), False),
        ("Capex", D(10), True),
    ]
    assert grupos[0].pagos == _pagos(200, 200)
    assert grupos[1].pagos == {}
    # as contas sem grupo voltam à parte: ficam fora do cartão
    assert sem_grupo == _pagos(30, 30)


def test_pagar_lancado_por_grupo_soma_dos_grupos_e_o_total_do_topo() -> None:
    lido = grupos_da_leitura(
        {"fornecedores": D(300), "custos": D(150), "divida": D(40), "capex": D(0), None: D(999)},
        {
            "fornecedores": _pagos(500, 700),
            "custos": _pagos(200, 200),
            "divida": _pagos(50, 50),
            "capex": _pagos(0, 120),
            None: _pagos(1000, 1000),
        },
    )
    assert lido is not None
    grupos, sem_grupo = lido

    cartao = cartao_pagar_lancado(HOJE, grupos, PADROES["pagar_lancado"], sem_grupo)

    assert cartao is not None
    assert cartao.rotulo_grupos == "Por grupo de pagamento"
    assert [g.nome for g in cartao.grupos] == [
        "Fornecedores",
        "Custos fixos, despesas e impostos",
        "Dívida",
        "Capex",
    ]
    # o topo é a soma dos grupos: 490 lançados contra (600 + 200 + 50 + 60) de média
    lancado = sum((g.proximos.lancado.valor.valor for g in cartao.grupos), D(0))
    media = sum((g.media.valor.valor for g in cartao.grupos), D(0))
    assert (lancado, media) == (D(490), D(910))
    assert cartao.percentual.valor == lancado / media * 100
    assert cartao.status == "atencao"  # 54%
    # capex sem farol e com a nota da oscilação
    capex = cartao.grupos[3]
    assert capex.status is None
    assert capex.nota == (
        "Oscila de R$ 0 a R$ 120 por mês. Confira se há investimento previsto antes de tratar "
        "como falta."
    )
    assert cartao.nota == (
        "Faltam R$ 420: R$ 300 em fornecedores, R$ 60 em capex, R$ 50 em custos fixos, "
        "despesas e impostos e R$ 10 em dívida."
    )
    # o que ficou sem grupo só aparece na explicação
    assert cartao.explicacao is not None
    assert cartao.explicacao.endswith(
        "Ficam fora dos grupos R$ 2 mil pagos de abril a maio em contas sem grupo de pagamento "
        "no cadastro."
    )


def test_pagar_lancado_grupo_sem_pagamento_fica_de_fora_e_sem_nota_do_que_falta_fora() -> None:
    lido = grupos_da_leitura({"fornecedores": D(100)}, {"fornecedores": _pagos(200)})
    assert lido is not None
    grupos, sem_grupo = lido

    cartao = cartao_pagar_lancado(HOJE, grupos, PADROES["pagar_lancado"], sem_grupo)

    assert cartao is not None
    # custos, dívida e capex sem nada pago nos meses: fora do cartão
    assert [g.nome for g in cartao.grupos] == ["Fornecedores"]
    assert cartao.nota == "Faltam R$ 100, tudo em fornecedores."
    assert cartao.explicacao is not None
    assert "Ficam fora" not in cartao.explicacao


def test_titulos_por_grupo_juntam_o_de_para() -> None:
    ctx = SimpleNamespace(cliente_id=None, empresas=(), empresa_id=None)
    tabela = TabelaDoCliente("f_despesas_pj", dict(COLUNAS_PAGAR))

    sem = _titulos([tabela], ctx).as_string(None)  # type: ignore[arg-type]
    com = _titulos([tabela], ctx, por_grupo=True).as_string(None)  # type: ignore[arg-type]

    assert "null::text as grupo" in sem
    assert "conta_dre" not in sem
    assert '"en_codigoplanocliente"::text as "conta"' in com
    assert "g.grupo_pagamento as grupo" in com
    assert "left join cfg.conta_dre g on g.cliente_id = %(cliente)s and g.conta = t.conta" in com


def test_leitura_por_grupo_separa_lancado_e_pago() -> None:
    class Conn:
        def execute(self, consulta: object, parametros: object) -> object:
            linhas = [
                {
                    "parte": "carteira",
                    "dia": None,
                    "a_vencer": D(500),
                    "proximos": [D(0), D(0), D(400)],
                    "compensando": D(0),
                    "ate_30": D(0),
                    "ate_60": D(0),
                    "ate_90": D(0),
                    "mais_90": D(0),
                    "grupo": None,
                },
                {"parte": "pagos", "dia": date(2026, 9, 1), "pago": D(900), "grupo": None},
                {
                    "parte": "grupo-lancado",
                    "dia": None,
                    "proximos": [D(0), D(0), D(300)],
                    "grupo": "fornecedores",
                },
                {
                    "parte": "grupo-lancado",
                    "dia": None,
                    "proximos": [D(0), D(0), D(100)],
                    "grupo": None,
                },
                {
                    "parte": "grupo-pago",
                    "dia": date(2026, 9, 1),
                    "pago": D(800),
                    "grupo": "fornecedores",
                },
                {"parte": "grupo-pago", "dia": date(2026, 9, 1), "pago": D(100), "grupo": None},
            ]
            return SimpleNamespace(fetchall=lambda: linhas)

    ctx = SimpleNamespace(cliente_id=None, empresas=(), empresa_id=None)
    tabela = TabelaDoCliente("f_despesas_pj", dict(COLUNAS_PAGAR))
    leitura = _ler(Conn(), ctx, [tabela], HOJE, HOJE, {}, date(2026, 1, 1), por_grupo=True)  # type: ignore[arg-type]

    assert leitura.carteira.proximos_dias == D(400)
    assert leitura.pagos == {date(2026, 9, 1): {"valor": D(900)}}
    assert leitura.lancado_grupo == {"fornecedores": D(300), None: D(100)}
    assert leitura.pagos_grupo == {
        "fornecedores": {date(2026, 9, 1): {"valor": D(800)}},
        None: {date(2026, 9, 1): {"valor": D(100)}},
    }
    # o lançado dos grupos (com o sem grupo) fecha com a carteira
    assert sum(leitura.lancado_grupo.values()) == leitura.carteira.proximos_dias

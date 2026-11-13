"""Fluxo de caixa (FIN-04), sem banco: classificação pelo plano, saldo, período e cartões."""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi import HTTPException

from app.telas.paineis import fluxo_caixa
from app.telas.paineis.contrato import CartaoArvoreMensal, CartaoGraficos, GraficoCascata
from app.telas.paineis.fluxo_caixa import Fluxo, classificar, conteudo, fluxo_das_linhas
from app.telas.plano import Plano, plano_das_linhas

D = Decimal


@pytest.fixture(autouse=True)
def _filtro_competencia(monkeypatch: pytest.MonkeyPatch) -> None:
    """Os testes valem para a regra com o filtro de competência (CA e CO/CA), desligado só
    para um teste do usuário (FILTRO_COMPETENCIA); test_sem_filtro_de_competencia cobre o
    desligado."""
    monkeypatch.setattr(fluxo_caixa, "FILTRO_COMPETENCIA", True)


JUN, JUL, AGO, SET = (date(2026, m, 1) for m in (6, 7, 8, 9))


def _conta(
    codigo: str, nome: str, grupo: tuple[str, str], fator: int, competencia: str, caixa: str
) -> dict[str, Any]:
    return {
        "id_descritivo": codigo,
        "descritivo": nome,
        "id_nivel_1": grupo[0],
        "nivel_1": grupo[1],
        "id_nivel_2": codigo.rsplit(".", 1)[0],
        "nivel_2": f"Subgrupo {codigo.rsplit('.', 1)[0]}",
        "fator": fator,
        "caixa": caixa,
        "competencia": competencia,
    }


PLANO = plano_das_linhas(
    [
        _conta("100.01.01", "Aves", ("100.00", "(+) RECEITA BRUTA"), 1, "CO/CA", "FCO"),
        _conta("140.01.01", "Salários", ("140.00", "(-) DESPESAS FIXAS"), -1, "CO/CA", "FCO"),
        _conta("150.01.01", "Depreciação", ("150.00", "(-) D&A"), -1, "CO", "FCO"),
        _conta("410.01.02", "Empréstimos", ("410.00", "(+) EMPRÉSTIMOS"), 1, "CA", "FCF"),
        _conta("420.02.02", "Veículos", ("420.00", "(-) INVESTIMENTOS"), -1, "CA", "FCI"),
    ]
)


def _l(trecho: date, conta: str | None, valor: str, cod: str | None = None) -> dict[str, Any]:
    return {"trecho": trecho, "conta": conta, "cod": cod, "valor": D(valor), "ultimo": trecho}


LINHAS = [
    _l(JUN, "100.01.01", "100"),
    _l(JUL, "100.01.01", "500"),
    _l(JUL, "140.01.01", "-300"),
    _l(JUL, None, "-10", cod="2300"),
    _l(JUL, "150.01.01", "-50"),
    _l(AGO, "100.01.01", "400"),
    _l(AGO, "140.01.01", "-450"),
    _l(AGO, "410.01.02", "200"),
    _l(AGO, "420.02.02", "-100"),
    {**_l(SET, "100.01.01", "600"), "ultimo": date(2026, 9, 15)},
    _l(SET, "999.99.99", "5"),
]
SALDOS = [(JUL, D(1000))]


def _fluxo(saldos: list[tuple[date, Decimal]] = SALDOS, plano: Plano = PLANO) -> Fluxo:
    return fluxo_das_linhas(LINHAS, saldos, plano)


def _cartoes(c: Any) -> dict[str, Any]:
    return {x.id: x for x in c.cartoes}


def _arvore(c: Any) -> dict[str, list[float | None]]:
    cartao = _cartoes(c)["fluxo-realizado"]
    assert isinstance(cartao, CartaoArvoreMensal)
    return {x.id: [None if v is None else float(v) for v in x.valores] for x in cartao.linhas}


@pytest.mark.parametrize(
    ("conta", "cod", "esperado"),
    [
        ("100.01.01", None, ("conta", "100.01.01")),
        ("1000101", None, ("conta", "100.01.01")),
        ("150.01.01", None, ("fora", "150.01.01")),
        ("999.99.99", None, ("sem", "999.99.99, fora do plano")),
        (None, "2300", ("sem", "conta 2300 do ERP")),
        (None, None, ("sem", "sem conta no ERP")),
    ],
)
def test_classificar_pelo_plano(conta: str | None, cod: str | None, esperado: Any) -> None:
    assert classificar(conta, cod, PLANO) == esperado


def test_saldo_parte_do_cadastro_para_frente_e_para_tras() -> None:
    f = _fluxo()
    # junho é antes do saldo: volta o caixa de junho
    assert f.saldo_em(JUN) == D(900)
    assert f.saldo_em(JUL) == D(1000)
    # julho: 500 − 300 − 10 (sem classificação entra; a depreciação, CO, não)
    assert f.saldo_em(AGO) == D(1190)
    assert f.fechamento(SET) == D(1845)
    assert f.fora == {JUL: {"150.01.01": D(-50)}}


def test_saldo_no_meio_do_mes() -> None:
    # o saldo de 15/07 parte julho em dois trechos (a consulta separa)
    meio = date(2026, 7, 15)
    f = fluxo_das_linhas(
        [_l(JUL, "100.01.01", "30"), _l(meio, "100.01.01", "70")], [(meio, D(1000))], PLANO
    )
    assert f.saldo_em(JUL) == D(970)
    assert f.saldo_em(AGO) == D(1070)
    assert f.contas == {JUL: {"100.01.01": D(100)}}


def test_visao_mes_padrao_e_o_ultimo_mes_com_caixa() -> None:
    c = conteudo(_fluxo(), PLANO, None, {})

    assert [o.rotulo for o in c.filtros[0].opcoes] == ["Setembro", "Acumulado de 2026"]
    saldo, fco, variacao = c.destaques
    assert (saldo.rotulo, float(saldo.valor.valor)) == ("Saldo final de setembro", 1845)  # type: ignore[union-attr]
    assert saldo.contexto == "começou o mês em R$ 1 mil"
    assert (float(fco.valor.valor), fco.contexto, fco.status) == (
        600,
        "-R$ 50 em agosto",
        "saudavel",
    )  # type: ignore[union-attr]
    assert (float(variacao.valor.valor), variacao.contexto) == (605, "R$ 50 em agosto")  # type: ignore[union-attr]
    assert [(a.rotulo, float(a.valor.valor)) for a in c.apoios] == [  # type: ignore[union-attr]
        ("Entradas operacionais", 600),
        ("Saídas operacionais", 0),
        ("Financiamentos (FCF)", 0),
        ("Investimentos (FCI)", 0),
        ("Sem classificação", 5),
    ]
    assert c.aviso is not None
    assert c.aviso.tom == "info"
    assert any(t.destaque and t.texto == "R$ 5 de setembro" for t in c.aviso.texto)


def test_arvore_mes_a_mes() -> None:
    c = conteudo(_fluxo(), PLANO, None, {})
    cartao = _cartoes(c)["fluxo-realizado"]
    assert isinstance(cartao, CartaoArvoreMensal)
    # só os meses com caixa entre os 6 até setembro (o caixa começa em junho)
    assert [x.rotulo for x in cartao.colunas] == ["jun-26", "jul-26", "ago-26", "set-26"]
    assert (cartao.nivel_inicial, cartao.niveis) == (2, [1, 2, 3, 4])
    v = _arvore(c)
    assert v["saldo-inicial"] == [900, 1000, 1190, 1240]
    assert v["saldo-final"] == [1000, 1190, 1240, 1845]
    assert v["fco"] == [100, 200, -50, 600]
    assert v["fcf-ent"] == [None, None, 200, None]
    assert v["fcf"] == [None, None, 200, None]
    assert v["sem-classificacao"] == [None, -10, None, 5]
    assert v["variacao"] == [100, 190, 50, 605]
    # entradas e saídas abertas pelos níveis do plano; a depreciação (CO) não aparece
    assert v["fco-ent-100.00-100.01-100.01.01"] == [100, 500, 400, 600]
    assert not any("150" in id_ for id_ in v)
    linhas = {x.id: x for x in cartao.linhas}
    assert linhas["fco-ent-100.00"].nome == "Receita bruta"
    assert linhas["fco-ent-100.00"].pai == "fco-ent"
    assert linhas["fco"].fundos == ["saudavel", "saudavel", "critico", "saudavel"]
    assert linhas["saldo-final"].final
    # ordem da fixture do front
    ordem = [x.id for x in cartao.linhas if x.nivel <= 1]
    assert ordem == [
        "saldo-inicial",
        "fco-ent",
        "fco-sai",
        "fco",
        "fcf-ent",
        "fcf",
        "fci-sai",
        "fci",
        "sem-classificacao",
        "variacao",
        "saldo-final",
    ]
    assert cartao.rodape is not None
    assert "conta 2300 do ERP (-R$ 10)" in cartao.rodape[0]
    assert "999.99.99, fora do plano (R$ 5)" in cartao.rodape[0]
    assert "150.01.01 Depreciação (-R$ 50)" in cartao.rodape[1]


def test_visao_ano_com_coluna_do_acumulado() -> None:
    c = conteudo(_fluxo(), PLANO, None, {"visao": "ano"})
    cartao = _cartoes(c)["fluxo-realizado"]
    assert isinstance(cartao, CartaoArvoreMensal)
    assert cartao.colunas[-1].rotulo == "Acumulado 2026"
    assert cartao.colunas[-1].total
    v = _arvore(c)
    # o acumulado abre no saldo do primeiro mês e fecha no do último
    assert v["saldo-inicial"][-1] == 900
    assert v["saldo-final"][-1] == 1845
    assert v["fco"][-1] == 850
    saldo, fco, _ = c.destaques
    assert saldo.contexto == "começou junho em R$ 900"
    # 2025 sem caixa: sem comparação
    assert fco.contexto is None


def test_cascata_do_saldo_inicial_ao_final() -> None:
    c = conteudo(_fluxo(), PLANO, None, {})
    cartao = _cartoes(c)["saldo-cascata"]
    assert isinstance(cartao, CartaoGraficos)
    # setembro termina depois do último dia com caixa
    assert cartao.subtitulo is not None
    assert cartao.subtitulo.startswith("Setembro, até 15/09.")
    g = cartao.graficos[0].grafico
    assert isinstance(g, GraficoCascata)
    # passos sem valor (financiamento e investimento em setembro) não saem
    assert [(p.rotulo, float(p.de), float(p.ate)) for p in g.passos] == [
        ("Saldo inicial", 0, 1240),
        ("Operação", 1240, 1840),
        ("Sem classificação", 1840, 1845),
        ("Saldo final", 0, 1845),
    ]


def test_geracao_por_mes() -> None:
    c = conteudo(_fluxo(), PLANO, None, {})
    cartao = _cartoes(c)["geracao-caixa"]
    assert isinstance(cartao, CartaoGraficos)
    assert cartao.subtitulo == "Operação, financiamentos e investimentos nos últimos 4 meses"
    grupos = cartao.graficos[0].grafico.grupos  # type: ignore[union-attr]
    assert [g.rotulo for g in grupos] == ["jun", "jul", "ago", "set"]
    assert [(b.nome, float(b.valor), b.estilo) for b in grupos[2].barras] == [
        ("Operação (FCO)", -50, "serie"),
        ("Financiamentos (FCF)", 200, "serie-azul"),
        ("Investimentos (FCI)", -100, "serie-clara"),
    ]


def test_sem_saldo_cadastrado_so_o_movimento() -> None:
    c = conteudo(_fluxo(saldos=[]), PLANO, None, {})
    assert "saldo-cascata" not in _cartoes(c)
    assert [d.rotulo for d in c.destaques] == ["Geração operacional (FCO)", "Variação do caixa"]
    assert c.aviso is not None
    assert c.aviso.tom == "atencao"
    assert _arvore(c)["saldo-final"] == [None, None, None, None]


def test_sem_plano_tudo_sem_classificacao() -> None:
    c = conteudo(_fluxo(plano=Plano()), Plano(), None, {})
    cartao = _cartoes(c)["fluxo-realizado"]
    assert isinstance(cartao, CartaoArvoreMensal)
    assert cartao.niveis == [1]
    v = _arvore(c)
    # sem plano, nem a depreciação é reconhecida: tudo fica sem classificação
    assert v["sem-classificacao"] == [100, 140, 50, 605]
    assert v["fco"] == [None, None, None, None]


def test_mes_escolhido_sem_caixa_sai_vazio_nas_colunas() -> None:
    c = conteudo(_fluxo(), PLANO, date(2026, 10, 1), {})
    v = _arvore(c)
    assert v["saldo-final"][-1] is None
    assert v["variacao"][-1] is None


def test_filtro_invalido_422() -> None:
    with pytest.raises(HTTPException) as erro:
        conteudo(_fluxo(), PLANO, None, {"visao": "projecao"})
    assert erro.value.status_code == 422


def test_sem_caixa_vazio_e_nenhum_filtro_vale() -> None:
    vazio = fluxo_das_linhas([], SALDOS, PLANO)
    assert conteudo(vazio, PLANO, None, {}).cartoes == []
    with pytest.raises(HTTPException):
        conteudo(vazio, PLANO, None, {"visao": "mes"})


def test_sem_filtro_de_competencia(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fluxo_caixa, "FILTRO_COMPETENCIA", False)
    # a depreciação (CO, classe FCO) entra nas saídas operacionais e no saldo
    assert classificar("150.01.01", None, PLANO) == ("conta", "150.01.01")
    f = _fluxo()
    assert f.fora == {}
    assert f.saldo_em(AGO) == D(1140)
    c = conteudo(f, PLANO, None, {})
    v = _arvore(c)
    assert v["fco-sai-150.00-150.01-150.01.01"] == [None, -50, None, None]
    assert v["saldo-final"] == [1000, 1140, 1190, 1795]
    cartao = _cartoes(c)["fluxo-realizado"]
    assert isinstance(cartao, CartaoArvoreMensal)
    # sem a frase das contas fora do caixa, no rodapé e no aviso
    assert cartao.rodape is not None
    assert len(cartao.rodape) == 1
    assert c.aviso is not None
    assert not any("Depreciação" in t.texto for t in c.aviso.texto)
    assert not c.aviso.texto[-1].texto.endswith(" ")


def test_visao_desde_o_primeiro_mes_com_caixa() -> None:
    # caixa desde novembro de 2025: "Desde novembro de 2025" pega os dois anos
    nov25, jan26 = date(2025, 11, 1), date(2026, 1, 1)
    linhas = [
        _l(nov25, "100.01.01", "100"),
        _l(jan26, "140.01.01", "-30"),
        *LINHAS,
    ]
    f = fluxo_das_linhas(linhas, [(nov25, D(1000))], PLANO)

    padrao = conteudo(f, PLANO, None, {})
    opcoes = [(o.id, o.rotulo) for o in padrao.filtros[0].opcoes]
    assert opcoes[-1] == ("historico", "Desde novembro de 2025")

    c = conteudo(f, PLANO, None, {"visao": "historico"})
    cartao = _cartoes(c)["fluxo-realizado"]
    assert isinstance(cartao, CartaoArvoreMensal)
    rotulos = [x.rotulo for x in cartao.colunas]
    # todos os meses de nov/25 a set/26 (dez/25 sem caixa vem vazio) e o acumulado de tudo
    assert rotulos[0] == "nov-25"
    assert rotulos[-2:] == ["set-26", "Acumulado desde nov-25"]
    assert len(rotulos) == 12
    v = _arvore(c)
    assert v["saldo-inicial"][0] == 1000
    assert v["saldo-inicial"][1] is None
    assert v["saldo-inicial"][-1] == 1000
    # 1.000 + 100 − 30 + 100 + 190 + 50 + 605
    assert v["saldo-final"][-1] == 2015
    saldo, fco, _ = c.destaques
    assert saldo.contexto == "começou novembro de 2025 em R$ 1 mil"
    assert fco.contexto is None
    geracao = _cartoes(c)["geracao-caixa"]
    assert isinstance(geracao, CartaoGraficos)
    # só meses com caixa, no máximo 12
    grupos = geracao.graficos[0].grafico.grupos  # type: ignore[union-attr]
    assert [g.rotulo for g in grupos] == ["nov", "jan", "jun", "jul", "ago", "set"]


def test_sem_caixa_antes_do_ano_nao_tem_a_visao_desde() -> None:
    c = conteudo(_fluxo(), PLANO, None, {})
    assert [o.id for o in c.filtros[0].opcoes] == ["mes", "ano"]
    with pytest.raises(HTTPException):
        conteudo(_fluxo(), PLANO, None, {"visao": "historico"})

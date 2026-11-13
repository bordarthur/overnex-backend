"""Projeção de caixa de 60 dias do Fluxo de caixa (FIN-04), sem banco.

Cenário: D0 = 12/10/2026 (segunda); a projeção vai de 13/10 (terça) a 11/12 (sexta). J1 =
13/10 a 11/11, J2 = 12/11 a 11/12. Realizado de abril a setembro, igual todo mês: receita
1.000, fornecedores −600, folha −100, aluguel −50, amortização −30 e veículos −20. Sem
feriados (a lista vazia), salvo onde o teste diz.
"""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi import HTTPException

from app.farol.parametro import PADROES
from app.telas.paineis.contrato import (
    CartaoBarrasHorizontais,
    CartaoGraficos,
    CartaoOrigemProjecao,
    CartaoProvisoes,
    CartaoTabela,
    GraficoBarras,
    GraficoLinhas,
)
from app.telas.paineis.fluxo_caixa import conteudo, fluxo_das_linhas
from app.telas.paineis.projecao import (
    Entrada,
    Projecao,
    Titulos,
    cartoes,
    distribuir,
    perfil_por_grupo,
    realizado_por_grupo,
    semanas,
)
from app.telas.paineis.provisoes import EscolhaGrupo, Escolhas, grupo_da_conta
from app.telas.plano import plano_das_linhas

D = Decimal
D0 = date(2026, 10, 12)
MESES = [date(2026, m, 1) for m in range(4, 10)]


def _conta(codigo: str, caixa: str, fator: int) -> dict[str, Any]:
    return {
        "id_descritivo": codigo,
        "descritivo": f"Conta {codigo}",
        "id_nivel_1": codigo[:3] + ".00",
        "nivel_1": f"Grupo {codigo[:3]}",
        "id_nivel_2": codigo.rsplit(".", 1)[0],
        "nivel_2": f"Subgrupo {codigo.rsplit('.', 1)[0]}",
        "fator": fator,
        "caixa": caixa,
        "competencia": "CO/CA",
    }


PLANO = plano_das_linhas(
    [
        _conta("100.01.01", "FCO", 1),
        _conta("120.01.01", "FCO", -1),
        _conta("140.01.01", "FCO", -1),
        _conta("140.07.01", "FCO", -1),
        _conta("110.03.01", "FCO", -1),
        _conta("200.01.01", "FCO", -1),
        _conta("130.01.01", "FCO", -1),
        _conta("170.01.05", "FCF", -1),
        _conta("400.02.01", "FCF", -1),
        _conta("420.02.02", "FCI", -1),
        _conta("210.01.01", "FCO", -1),
        _conta("160.01.01", "FCF", 1),
    ]
)
MES_TIPICO = {
    "100.01.01": D(1000),
    "120.01.01": D(-600),
    "140.01.01": D(-100),
    "140.07.01": D(-50),
    "400.02.01": D(-30),
    "420.02.02": D(-20),
}
CONTAS = {m: dict(MES_TIPICO) for m in MESES}
TITULOS = Titulos(
    receber={
        # a vencer: D+2 → segunda 19/10
        date(2026, 10, 15): D(300),
        # vencido na sexta, ainda no D+2 (terça 13/10): em compensação → D0+1
        date(2026, 10, 9): D(50),
        # vencido além do D+2: fora
        date(2026, 10, 1): D(70),
        # depois do horizonte: fora
        date(2026, 12, 20): D(999),
    },
    pagar={
        # sábado → segunda 19/10
        (date(2026, 10, 17), "120.01.01"): D(200),
        # vencido (vem com o vencimento em D0) → D0+1
        (D0, "140.01.01"): D(40),
        # sem grupo de provisão
        (date(2026, 10, 20), None): D(5),
        # na J2
        (date(2026, 11, 20), "400.02.01"): D(10),
    },
    pagos={m: D(400) for m in MESES},
    serie_receber={date(2026, 9, 30): (D(10), D(100))},
    tem_receber=True,
    tem_pagar=True,
)


def _entrada(**muda: Any) -> Entrada:
    base: dict[str, Any] = {
        "d0": D0,
        "saldo": D(1000),
        "contas": CONTAS,
        "meses_com_caixa": frozenset([*MESES, date(2026, 10, 1)]),
        "plano": PLANO,
        "titulos": TITULOS,
        "por_dia": {},
        "escolhas": Escolhas(),
        "feriados": frozenset(),
        "farol_pagar": PADROES["pagar_lancado"],
        "minimo": None,
        "data_titulos": D0,
    }
    base.update(muda)
    return Entrada(**base)


def _por_id(lista: list[Any]) -> dict[str, Any]:
    return {c.id: c for c in lista}


# ---------- grupos ----------


@pytest.mark.parametrize(
    ("codigo", "entrada", "grupo"),
    [
        ("100.01.01", True, "recebimentos"),
        ("120.01.01", False, "fornecedores"),
        ("140.01.01", False, "folha"),
        ("140.07.01", False, "despesas-fixas"),
        ("130.01.01", False, "despesas-variaveis"),
        ("110.03.01", False, "impostos"),
        ("200.01.01", False, "impostos"),
        ("170.01.05", False, "emprestimos"),
        ("400.02.01", False, "emprestimos"),
        ("420.02.02", False, "investimentos"),
        # sem grupo: participação dos sócios e receita financeira (FCF)
        ("210.01.01", False, None),
        ("160.01.01", True, None),
    ],
)
def test_grupo_da_conta(codigo: str, entrada: bool, grupo: str | None) -> None:
    assert grupo_da_conta(codigo, PLANO.conta(codigo), entrada) == grupo


def test_realizado_por_grupo_tem_o_sinal_do_caixa() -> None:
    r = realizado_por_grupo(CONTAS, PLANO)
    assert r[MESES[0]] == {
        "recebimentos": D(1000),
        "fornecedores": D(-600),
        "folha": D(-100),
        "despesas-fixas": D(-50),
        "emprestimos": D(-30),
        "investimentos": D(-20),
    }


def test_perfil_so_conta_o_lado_do_grupo() -> None:
    # no dia 10 a folha teve estorno maior que o pago: o dia fica fora do perfil
    por_dia = {5: {"140.01.01": D(-80)}, 10: {"140.01.01": D(30)}, 20: {"100.01.01": D(500)}}
    assert perfil_por_grupo(por_dia, PLANO) == {"folha": {5: D(80)}, "recebimentos": {20: D(500)}}


# ---------- lançado ----------


def test_receber_no_d_mais_2_e_compensacao_em_d0_mais_1() -> None:
    p = Projecao(_entrada())
    assert p.receber == {date(2026, 10, 19): D(300), date(2026, 10, 13): D(50)}


def test_receber_d_mais_2_pula_feriado() -> None:
    p = Projecao(_entrada(feriados=frozenset({date(2026, 10, 16)})))
    # 15/10 + 2 dias úteis, sem a sexta 16/10: terça 20/10
    assert p.receber[date(2026, 10, 20)] == D(300)


def test_pagar_no_proximo_dia_util_e_vencido_em_d0_mais_1() -> None:
    p = Projecao(_entrada())
    assert p.pagar == {
        (date(2026, 10, 19), "fornecedores"): D(200),
        (date(2026, 10, 13), "folha"): D(40),
        (date(2026, 10, 20), None): D(5),
        (date(2026, 11, 20), "emprestimos"): D(10),
    }
    assert (p.pagar1, p.pagar2) == (D(245), D(10))


# ---------- provisões ----------


def _grupos(p: Projecao) -> dict[str, tuple[Decimal, Decimal, Decimal, Decimal]]:
    return {g.id: (g.media, g.lancado1, g.provisao1, g.provisao2) for g in p.grupos}


def test_provisao_e_a_media_menos_o_lancado_em_cada_janela() -> None:
    g = _grupos(Projecao(_entrada()))
    assert g["recebimentos"] == (D(1000), D(350), D(650), D(1000))
    assert g["fornecedores"] == (D(600), D(200), D(400), D(600))
    assert g["folha"] == (D(100), D(40), D(60), D(100))
    assert g["despesas-fixas"] == (D(50), D(0), D(50), D(50))
    assert g["emprestimos"] == (D(30), D(0), D(30), D(20))
    # investimentos começam desligados: média, mas nenhuma provisão
    assert g["investimentos"] == (D(20), D(0), D(0), D(0))
    assert g["impostos"] == (D(0), D(0), D(0), D(0))


def test_periodo_menor_usa_menos_meses() -> None:
    contas = {**CONTAS, MESES[-1]: {**MES_TIPICO, "120.01.01": D(-900)}}
    p = Projecao(_entrada(contas=contas, escolhas=Escolhas(periodo="ultimo-mes")))
    assert p.meses_media == [MESES[-1]]
    assert _grupos(p)["fornecedores"][0] == D(900)


def test_valor_personalizado_e_desligado() -> None:
    escolhas = Escolhas(
        grupos={
            "fornecedores": EscolhaGrupo(
                ligado=True, valor=D(500), por="Ana", em=date(2026, 11, 5)
            ),
            "folha": EscolhaGrupo(ligado=False),
        }
    )
    p = Projecao(_entrada(escolhas=escolhas))
    g = _grupos(p)
    # J1 = V; J2 = máx(0, V + lançado em J1 − lançado em J2) = 500 + 200 − 0
    assert g["fornecedores"][2:] == (D(500), D(700))
    assert g["folha"][2:] == (D(0), D(0))
    cartao = _por_id(cartoes(p, "provisoes"))["provisoes"]
    assert isinstance(cartao, CartaoProvisoes)
    linhas = {g.id: g for g in cartao.grupos}
    assert linhas["fornecedores"].usado == D(500)
    assert linhas["fornecedores"].sugerida == D(400)
    assert linhas["fornecedores"].personalizado == "personalizado por Ana em 05/11"
    assert linhas["folha"].ligado is False
    assert linhas["recebimentos"].personalizado is None


def test_distribuir_pelo_perfil_e_no_proximo_dia_util() -> None:
    p = Projecao(_entrada())
    fim = p.dias[-1]
    # 31/10 é sábado: vai para segunda 02/11
    assert distribuir(D(100), p.janela1, {31: D(1), 5: D(1)}, frozenset(), fim) == {
        date(2026, 11, 2): D(50),
        date(2026, 11, 5): D(50),
    }
    # com o feriado de 02/11, para 03/11
    feriado = frozenset({date(2026, 11, 2)})
    assert distribuir(D(100), p.janela1, {31: D(1)}, feriado, fim) == {date(2026, 11, 3): D(100)}
    # novembro não tem dia 31: cai no último dia do mês (segunda 30/11)
    assert distribuir(D(100), p.janela2, {31: D(1)}, frozenset(), fim) == {
        date(2026, 11, 30): D(100)
    }


def test_distribuir_sem_perfil_divide_nos_dias_uteis() -> None:
    p = Projecao(_entrada())
    partes = distribuir(D(220), p.janela1, None, frozenset(), p.dias[-1])
    # 13/10 a 11/11: 22 dias úteis
    assert len(partes) == 22
    assert all(d.weekday() < 5 for d in partes)
    assert sum(partes.values()) == D(220)


def test_perfil_so_com_3_meses_de_historico() -> None:
    por_dia = {5: {"140.01.01": D(-100)}}
    com = Projecao(_entrada(por_dia=por_dia))
    assert com.perfis == {"folha": {5: D(100)}}
    sem = Projecao(_entrada(por_dia=por_dia, meses_com_caixa=frozenset(MESES[-2:])))
    assert sem.perfis == {}


# ---------- saldo ----------


def test_saldo_so_lancados_e_com_provisoes() -> None:
    p = Projecao(_entrada())
    lancados = p.saldos("lancados")
    assert lancados[0] == D(1010)  # 13/10: +50 − 40
    assert lancados[6] == D(1110)  # 19/10: +300 − 200
    assert lancados[-1] == D(1095)
    # com provisões: + (650 + 1000) − (400 + 600 + 60 + 100 + 50 + 50 + 30 + 20)
    assert round(p.saldos("provisoes")[-1], 6) == D(1435)


def test_semanas_de_segunda_a_domingo() -> None:
    p = Projecao(_entrada())
    s = semanas(p.dias)
    assert [(p.dias[i[0]], p.dias[i[-1]]) for i in (s[0], s[1], s[-1])] == [
        (date(2026, 10, 13), date(2026, 10, 18)),
        (date(2026, 10, 19), date(2026, 10, 25)),
        (date(2026, 12, 7), date(2026, 12, 11)),
    ]
    assert len(s) == 9


# ---------- cartões ----------


def test_ordem_e_larguras_dos_cartoes() -> None:
    c = cartoes(Projecao(_entrada()), "lancados")
    assert [(x.id, x.largura) for x in c] == [
        ("saldo-projetado", 12),
        ("origem-projecao", 5),
        ("semana-a-semana", 7),
        ("provisoes", 12),
        ("pago-lancado", 6),
        ("para-onde-vai", 6),
    ]


def test_saldo_projetado_com_alerta_minimo_e_nota() -> None:
    e = _entrada(minimo=D(1100), data_titulos=date(2026, 10, 16))
    c = _por_id(cartoes(Projecao(e), "lancados"))["saldo-projetado"]
    assert isinstance(c, CartaoGraficos)
    g = c.graficos[0]
    assert g.alto is True
    assert isinstance(g.grafico, GraficoLinhas)
    assert len(g.grafico.rotulos) == 60
    assert g.grafico.rotulos[0] == "13/10"
    assert g.grafico.titulos is not None
    assert g.grafico.titulos[0] == "13/10 · ter"
    assert g.grafico.referencia is not None
    assert g.grafico.referencia.estilo == "minimo"
    assert g.grafico.referencia.rotulo == "Caixa mínimo R$ 1 mil"
    assert [m.rotulo for m in g.grafico.marcadores or []] == ["R$ 1 mil em 13/10"]
    # 245 de 400 de média paga: 61%, atenção
    assert c.chamada is not None
    assert c.chamada.status == "atencao"
    assert "só 61% da média paga" in c.chamada.texto[1].texto
    assert c.subtitulo is not None
    assert "Como faltam saídas" in c.subtitulo
    # títulos de 16/10, mais de 3 dias úteis depois de D0 (15/10)
    assert c.nota == "O caixa realizado vai até 12/10; os títulos, até 16/10."


def test_saldo_projetado_com_provisoes_marca_o_menor_saldo() -> None:
    # sem a provisão dos recebimentos, o saldo cai até o fim: o menor é o último dia
    escolhas = Escolhas(grupos={"recebimentos": EscolhaGrupo(ligado=False)})
    p = Projecao(_entrada(escolhas=escolhas))
    c = _por_id(cartoes(p, "provisoes"))["saldo-projetado"]
    assert isinstance(c, CartaoGraficos)
    grafico = c.graficos[0].grafico
    assert isinstance(grafico, GraficoLinhas)
    marcadores = grafico.marcadores or []
    assert [m.i for m in marcadores] == [0, 59]
    assert marcadores[1].rotulo.startswith("Menor saldo ")
    assert marcadores[1].rotulo.endswith(" em 11/12")
    assert grafico.referencia is None
    assert c.subtitulo is not None
    assert "pela média de 6 meses" in c.subtitulo
    # o saldo só sobe: o menor é o primeiro dia, sem marcador repetido
    sobe = _por_id(cartoes(Projecao(_entrada()), "provisoes"))["saldo-projetado"]
    assert isinstance(sobe.graficos[0].grafico, GraficoLinhas)
    assert len(sobe.graficos[0].grafico.marcadores or []) == 1


def test_sem_saldo_parte_de_zero() -> None:
    c = _por_id(cartoes(Projecao(_entrada(saldo=None)), "lancados"))["saldo-projetado"]
    assert c.nota == "Sem saldo inicial cadastrado: a linha parte de zero."


def test_origem_da_projecao() -> None:
    c = _por_id(cartoes(Projecao(_entrada()), "lancados"))["origem-projecao"]
    assert isinstance(c, CartaoOrigemProjecao)
    entradas, saidas = c.grupos
    assert [
        (i.nome, float(i.valor.valor), i.etiqueta.estilo, i.riscado) for i in entradas.itens
    ] == [
        ("Títulos a receber já emitidos", 350, "lancado", None),
        # 10% de inadimplência média × 350
        ("Inadimplência esperada, pelo histórico (10,0%)", -35, "futuro", True),
        ("Vendas ainda não faturadas, pela média recebida", 1650, "nao-lancado", True),
    ]
    assert [(i.nome, float(i.valor.valor), i.riscado) for i in saidas.itens] == [
        ("Títulos a pagar e compras lançados", -245, None),
        ("Parcelas de empréstimos", -10, None),
        ("Fornecedores ainda não lançados, pela média", -1000, True),
        ("Folha e encargos, pela média", -160, True),
        ("Despesas fixas, pela média", -100, True),
        ("Empréstimos e juros ainda não lançados", -50, True),
    ]
    # lançado 605 de 3.565 em volume bruto
    assert [float(b.proporcao) for b in c.barra] == [0.17, 0.83]
    assert c.texto is not None
    assert c.texto[1].texto == "R$ 340"
    com = _por_id(cartoes(Projecao(_entrada()), "provisoes"))["origem-projecao"]
    assert isinstance(com, CartaoOrigemProjecao)
    # com provisões, só a inadimplência fica riscada
    riscados = [i.nome for g in com.grupos for i in g.itens if i.riscado]
    assert riscados == ["Inadimplência esperada, pelo histórico (10,0%)"]


def test_semana_a_semana() -> None:
    c = _por_id(cartoes(Projecao(_entrada(minimo=D(1100))), "lancados"))["semana-a-semana"]
    assert isinstance(c, CartaoTabela)
    assert c.variante == "listrada"
    primeira, segunda, terceira = c.linhas[:3]
    assert primeira.celulas[0].valor == "13/10 a 18/10"
    assert (primeira.celulas[0].marca, primeira.separar) == ("out", None)
    assert segunda.celulas[0].marca is None
    # 26/10 a 01/11 termina em novembro
    assert (terceira.celulas[0].marca, terceira.separar) == ("nov", True)
    # 1.010 no fim da primeira semana: abaixo do mínimo de 1.100
    assert float(primeira.celulas[3].valor.valor) == 1010  # type: ignore[union-attr]
    assert primeira.celulas[4].fundo == "atencao"
    assert float(primeira.celulas[2].valor.valor) == -40  # type: ignore[union-attr]
    assert segunda.celulas[4].fundo == "saudavel"


def test_pago_contra_lancado() -> None:
    c = _por_id(cartoes(Projecao(_entrada()), "lancados"))["pago-lancado"]
    assert isinstance(c, CartaoGraficos)
    assert c.status == "atencao"
    grafico = c.graficos[0].grafico
    assert isinstance(grafico, GraficoBarras)
    assert [g.rotulo for g in grafico.grupos] == [
        "abr",
        "mai",
        "jun",
        "jul",
        "ago",
        "set",
        "30 d",
        "60 d",
    ]
    assert [b.estilo for b in grafico.grupos[-2].barras + grafico.grupos[-1].barras] == [
        "atencao",
        "critico",
    ]
    assert grafico.referencias is not None
    assert float(grafico.referencias[0].valor) == 400
    assert c.quadros is not None
    assert [(q.titulo, q.status) for q in c.quadros] == [
        ("61% da média paga", "atencao"),
        ("3% da média paga", None),
    ]
    assert c.quadros[0].texto.startswith("Atenção: abaixo de 80%")


def test_sem_pagamento_nao_ha_pago_contra_lancado_nem_alerta() -> None:
    titulos = Titulos(receber=TITULOS.receber, pagar=TITULOS.pagar, tem_pagar=True)
    c = _por_id(cartoes(Projecao(_entrada(titulos=titulos)), "lancados"))
    assert "pago-lancado" not in c
    assert c["saldo-projetado"].chamada is None


def test_para_onde_vai() -> None:
    c = _por_id(cartoes(Projecao(_entrada()), "lancados"))["para-onde-vai"]
    assert isinstance(c, CartaoBarrasHorizontais)
    assert [(i.rotulo, float(i.valor)) for i in c.itens] == [
        ("Fornecedores", 600),
        ("Folha e encargos", 100),
        ("Despesas fixas", 50),
        ("Empréstimos e juros", 30),
    ]
    assert c.subtitulo == "Saídas previstas de 13/10 a 11/11, lançadas e provisionadas"


# ---------- na tela ----------


def _fluxo() -> Any:
    linhas = [
        {"trecho": m, "conta": c, "cod": None, "valor": v, "ultimo": m}
        for m in MESES
        for c, v in MES_TIPICO.items()
    ]
    linhas.append(
        {
            "trecho": date(2026, 10, 1),
            "conta": "100.01.01",
            "cod": None,
            "valor": D(10),
            "ultimo": D0,
        }
    )
    return fluxo_das_linhas(linhas, [(MESES[0], D(500))], PLANO)


def test_na_tela_a_projecao_vem_antes_do_realizado() -> None:
    c = conteudo(_fluxo(), PLANO, None, {}, _entrada())
    assert [f.id for f in c.filtros] == ["visao", "projecao"]
    projecao = c.filtros[1]
    assert projecao.cartao == "saldo-projetado"
    assert projecao.padrao == "lancados"
    assert [x.id for x in c.cartoes] == [
        "saldo-projetado",
        "origem-projecao",
        "semana-a-semana",
        "provisoes",
        "pago-lancado",
        "para-onde-vai",
        "saldo-cascata",
        "geracao-caixa",
        "fluxo-realizado",
    ]


def test_filtro_projecao_muda_so_os_tres_cartoes() -> None:
    so = _por_id(conteudo(_fluxo(), PLANO, None, {}, _entrada()).cartoes)
    com = _por_id(conteudo(_fluxo(), PLANO, None, {"projecao": "provisoes"}, _entrada()).cartoes)
    mudam = {i for i in so if so[i] != com[i]}
    assert mudam == {"saldo-projetado", "origem-projecao", "semana-a-semana"}


def test_filtro_projecao_invalido_422() -> None:
    with pytest.raises(HTTPException) as erro:
        conteudo(_fluxo(), PLANO, None, {"projecao": "tudo"}, _entrada())
    assert erro.value.status_code == 422
    # sem projeção (sem caixa para partir), o filtro não existe
    with pytest.raises(HTTPException):
        conteudo(_fluxo(), PLANO, None, {"projecao": "lancados"})

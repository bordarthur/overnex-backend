"""FIN-02 — Resultado: regras — sem banco.

As leituras (uma ida ao banco) são trocadas por linhas fictícias; plano de contas, DRE,
farol, textos, cartões e o contrato rodam de verdade.
"""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.core.db import Sessao
from app.farol.parametro import PADROES
from app.seguranca.acesso import ContextoConsolidado
from app.seguranca.auth import UsuarioToken
from app.telas.calendario import feriados_nacionais
from app.telas.moldura import Cliente, UltimaCarga
from app.telas.paineis import resultado
from app.telas.paineis.base import Conteudo
from app.telas.paineis.contrato import (
    CartaoBarraPartes,
    CartaoDre,
    CartaoGraficos,
    CartaoTabela,
    GraficoCascata,
    PainelPayload,
)
from app.telas.paineis.receber_pagar import IndicadoresMensais
from app.telas.plano import Plano, plano_das_linhas

D = Decimal
CARGA = UltimaCarga(data_snapshot=date(2026, 10, 13), fim=None)
SETEMBRO = date(2026, 9, 1)
OUTUBRO = date(2026, 10, 1)


def _ctx() -> ContextoConsolidado:
    sessao = Sessao(conn=object(), usuario=UsuarioToken(id_autenticacao=UUID(int=1)))  # type: ignore[arg-type]
    return ContextoConsolidado(
        sessao=sessao,
        usuario_id=UUID(int=2),
        cliente_id=UUID(int=3),
        empresa_id=None,
        empresas=(UUID(int=4),),
        papel="gestor",
        codigo_tela="FIN-02",
        ve_dado_nominal=True,
    )


def _meses() -> list[date]:
    return [date(a, m, 1) for a in (2025, 2026) for m in range(1, 13) if date(a, m, 1) <= OUTUBRO]


def _linhas(*, cenarios: tuple[str, ...] = ("base",), orcado: bool = True) -> dict[str, Any]:
    """Todo mês: faturado 1.000, custo 600, devolução 20, comissão 30, folha 100, outras
    fixas 50, receita financeira 10 e IR 20. Em setembro/26 as outras fixas vão a 80.
    Outubro é o mês em aberto (parcial: 40%). Orçado de 2026: receita 1.050, CMV 600,
    comissão 30, folha 100 e outras fixas 50 (o cenário "meta" com 10% a mais de receita)."""
    vendas, devolucoes, lancamentos, despesas, orcamento = [], [], [], [], []
    for m in _meses():
        parte = D("0.4") if m == OUTUBRO else D(1)
        vendas += [
            {"mes": m, "medida": "faturado", "valor": D(1000) * parte},
            {"mes": m, "medida": "custo", "valor": D(600) * parte},
            # outras medidas do resumo não entram
            {"mes": m, "medida": "pedidos", "valor": D(80)},
        ]
        devolucoes.append({"mes": m, "valor": D(20) * parte})
        outras = D(80) if m == SETEMBRO else D(50)
        lancamentos += [
            {"mes": m, "conta": "140.01.01", "valor": D(100) * parte},
            {"mes": m, "conta": "140.02.04", "valor": outras * parte},
            # entrada (Tipo E) vem negativa: receita financeira
            {"mes": m, "conta": "160.01.03", "valor": D(-10) * parte},
            {"mes": m, "conta": "200.01.01", "valor": D(20) * parte},
            # conta de caixa: fora da DRE
            {"mes": m, "conta": "400.01.01", "valor": D(999)},
        ]
        despesas.append({"mes": m, "conta": "130.01.01", "valor": D(30) * parte})
        if orcado and m.year == 2026:
            for cenario in cenarios:
                fator = D("1.1") if cenario == "meta" else D(1)
                orcamento += [
                    # o DW grava o orçado com qualquer sinal: entra o valor absoluto
                    {"mes": m, "conta": "100.01.21", "cenario": cenario, "valor": D(-1050) * fator},
                    {"mes": m, "conta": "120.01.07", "cenario": cenario, "valor": D(600)},
                    {"mes": m, "conta": "130.01.01", "cenario": cenario, "valor": D(30)},
                    {"mes": m, "conta": "140.01.01", "cenario": cenario, "valor": D(100)},
                    {"mes": m, "conta": "140.02.04", "cenario": cenario, "valor": D(-50)},
                ]
    return {
        "vendas": vendas,
        "devolucoes": devolucoes,
        "lancamentos": lancamentos,
        "despesas": despesas,
        "orcamento": orcamento,
    }


def _conta(codigo: str, nome: str, nivel_2: str, fator: int, competencia: str) -> dict[str, Any]:
    return {
        "id_descritivo": codigo,
        "descritivo": nome,
        "id_nivel_2": codigo.rsplit(".", 1)[0],
        "nivel_2": nivel_2,
        "fator": fator,
        "caixa": "FCO",
        "competencia": competencia,
    }


# as contas do teste como estão no plano padrão (supabase/producao/plano_contas_padrao.sql)
PLANO = plano_das_linhas(
    [
        _conta("100.01.21", "Bovinos", "Venda de Produtos", 1, "CO/CA"),
        _conta("100.03.06", "Recebimento Indevido", "Outras Receitas", 1, "CA"),
        _conta("110.01.02", "Provisao para Devedores Duvidosos", "Descontos", -1, "CO"),
        _conta("120.01.07", "Custo Bovinos", "CMV", -1, "CO/CA"),
        _conta("120.01.103", "Descontos de Mercadoria Comprada", "CMV", 1, "CO/CA"),
        _conta("130.01.01", "Comissoes Vendedores", "Despesa com Vendas", -1, "CO/CA"),
        _conta("140.01.01", "Salarios ( D )", "Despesas com Pessoal", -1, "CO/CA"),
        _conta("140.02.04", "Telefone/Internet", "Despesas Administrativas", -1, "CO/CA"),
        _conta("160.01.03", "Rendimento de Aplicação", "Receitas Financeiras", 1, "CO/CA"),
        _conta("200.01.01", "IRPJ/CSLL", "IRPJ e CSLL", -1, "CO/CA"),
    ]
)


def _feriados(_conn: Any, _cliente: Any, inicio: date, fim: date) -> set[date]:
    todos = set().union(*(feriados_nacionais(a) for a in range(inicio.year, fim.year + 1)))
    return {d for d in todos if inicio <= d <= fim}


@pytest.fixture
def leituras(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    estado: dict[str, Any] = {
        "linhas": _linhas(),
        "lancamentos": True,
        "titulos": None,
        "plano": PLANO,
    }
    monkeypatch.setattr(
        resultado, "tabelas_lancamentos", lambda c, x: [1] if estado["lancamentos"] else []
    )
    monkeypatch.setattr(resultado, "tabelas_agrupados", lambda c, x: [])
    monkeypatch.setattr(resultado, "consultas", lambda *a, **k: {})
    monkeypatch.setattr(resultado, "em_uma_ida", lambda conn, c: estado["linhas"])
    monkeypatch.setattr(resultado, "feriados", _feriados)
    monkeypatch.setattr(resultado, "farol", lambda conn, c, indicador, ref: PADROES[indicador])
    monkeypatch.setattr(resultado, "cenario_escolhido", lambda conn, ctx, d: None)
    monkeypatch.setattr(resultado, "ler_plano", lambda conn: estado["plano"])
    monkeypatch.setattr(resultado, "indicadores_mensais", lambda conn, ctx, ate: estado["titulos"])
    return estado


def _montar(
    pedidos: dict[str, str | None] | None = None,
    escolhido: date | None = None,
    a_decidir: bool = False,
) -> Conteudo:
    return resultado.montar(_ctx(), escolhido, CARGA, pedidos or {}, a_decidir)


def _payload(c: Conteudo) -> dict[str, Any]:
    """O contrato inteiro, como a rota devolve."""
    p = PainelPayload(
        tela="resultado",
        versao_contrato="1.0",
        cliente=Cliente(nome="X", sigla="X", logo_url=None),
        dados_ate=CARGA.data_snapshot,
        atualizado_em=None,
        filtros=c.filtros,
        aviso=c.aviso,
        destaques=c.destaques,
        kpis=c.kpis,
        apoios=c.apoios,
        cartoes=c.cartoes,
    )
    return p.model_dump(mode="json", by_alias=True, exclude_defaults=True)


def _cartao(c: Conteudo, id_: str) -> Any:
    return next(x for x in c.cartoes if x.id == id_)


# ---------- plano de contas ----------


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("140.01.01", "140.01.01"),
        ("1400101", "140.01.01"),
        ("140 01", "140.01"),
        ("100", "100"),
        ("400.01.01", None),
        ("", None),
        (None, None),
        ("abc", None),
    ],
)
def test_codigo_conta(texto: str | None, esperado: str | None) -> None:
    assert resultado.codigo_conta(texto) == esperado


def test_dados_com_o_sinal_da_dre_e_orcado_em_valor_absoluto() -> None:
    dados = resultado.dados_das_linhas(_linhas(), PLANO)
    setembro = dados.real[SETEMBRO]

    assert setembro[resultado.FATURADO] == 1000
    assert setembro[resultado.CUSTO_VENDAS] == 600
    assert setembro[resultado.DEVOLUCOES] == 20
    # entrada num grupo de receita vira receita positiva
    assert setembro["160.01.03"] == 10
    assert "400.01.01" not in setembro
    assert dados.orcamento["base"][SETEMBRO]["100.01.21"] == 1050
    assert dados.orcamento["base"][SETEMBRO]["140.02.04"] == 50
    assert dados.fora_do_plano == {}


def test_totais_da_dre() -> None:
    t = resultado.totais(resultado.dados_das_linhas(_linhas()).real[SETEMBRO])

    assert t.receita_bruta == 1000
    assert t.receita_liquida == 980
    assert t.lucro_bruto == 380
    assert t.margem_contribuicao == 350
    assert t.despesas_fixas == 180
    assert t.folha == 100
    assert t.devolucoes == 20
    assert t.ebitda == 170
    assert t.lucro_liquido == 160


def test_projecao_no_ritmo_e_fixas_pela_media() -> None:
    parcial = {"100.01.vendas": D(400), "140.01.01": D(30)}
    anteriores = [{"140.01.01": D(100), "150.01.01": D(9)}, {"140.01.01": D(110)}]
    p = resultado.projetar(parcial, anteriores, D("2.5"))

    assert p["100.01.vendas"] == 1000
    # fixa: o maior entre o lançado e a média dos anteriores
    assert p["140.01.01"] == 105
    assert p["150.01.01"] == D("4.5")


@pytest.mark.parametrize(
    ("real", "comp", "esperado"),
    [(100, 100, "saudavel"), (98, 100, "atencao"), (96, 100, "critico"), (5, 0, "saudavel")],
)
def test_status_receita(real: int, comp: int, esperado: str) -> None:
    assert resultado.status_receita(D(real), D(comp)) == esperado


@pytest.mark.parametrize(
    ("real", "comp", "esperado"),
    [
        (101, 100, "saudavel"),
        (104, 100, "atencao"),
        (106, 100, "critico"),
        (5, 0, "atencao"),
        (0, 0, "saudavel"),
    ],
)
def test_status_custo_pelo_peso_na_receita(real: int, comp: int, esperado: str) -> None:
    assert resultado.status_custo(D(real), D(comp), D(1000), D(1000)) == esperado


def test_status_subtotal_pela_margem() -> None:
    assert resultado.status_subtotal(D(170), D(172), D(1000), D(1000)) == "saudavel"
    assert resultado.status_subtotal(D(160), D(170), D(1000), D(1000)) == "atencao"
    assert resultado.status_subtotal(D(150), D(170), D(1000), D(1000)) == "critico"


def test_piso_da_escala() -> None:
    # como a ponte do mockup: de 591 mil a 711 mil
    assert resultado.piso_da_escala([D(666000), D(711000), D(591200), D(613000)]) == 530000
    assert resultado.piso_da_escala([D(-5), D(10)]) is None


# ---------- a tela ----------


def test_mes_fechado_contra_o_orcado(leituras: dict[str, Any]) -> None:
    c = _montar()
    corpo = _payload(c)

    rl, ebitda, ll = c.destaques
    assert (rl.rotulo, rl.valor.valor) == ("Receita líquida", 980)  # type: ignore[union-attr]
    assert rl.contexto == "6,7% abaixo do orçado"
    assert rl.status == "critico"
    assert ebitda.contexto == "17,0% da receita bruta; orçado 25,7%"
    assert ebitda.status == "critico"
    assert ll.valor.valor == 160  # type: ignore[union-attr]
    assert "aviso" not in corpo

    apoios = {a.rotulo: a for a in c.apoios}
    assert apoios["Margem bruta"].valor.valor == D("38.78")  # type: ignore[union-attr]
    assert apoios["Ponto de equilíbrio"].valor.valor == 504  # type: ignore[union-attr]
    # outras fixas 80 contra 50 orçado; receita 1.000 contra 1.050
    assert apoios["Maior ofensor"].valor == "Venda de Produtos -R$ 50"
    assert apoios["Maior ofensor"].status == "critico"
    assert "Maior favorável" in apoios

    assert [x.id for x in c.cartoes] == [
        "resultado-12-meses",
        "dre-cascata",
        "ponto-equilibrio",
        "dre-gerencial",
    ]
    # filtros: visão (com a projeção: sem `mes`, o mês em aberto existe) e sem cenário
    assert [f["id"] for f in corpo["filtros"]] == ["visao"]
    assert [o["id"] for o in corpo["filtros"][0]["opcoes"]] == ["mes", "ano", "projecao"]
    assert corpo["filtros"][0]["opcoes"][0]["rotulo"] == "Setembro (fechado)"


def test_dre_gerencial(leituras: dict[str, Any]) -> None:
    dre = _cartao(_montar(), "dre-gerencial")
    assert isinstance(dre, CartaoDre)
    por_id = {x.id: x for x in dre.linhas}

    assert dre.rotulo_comparacao == "Orçado"
    assert dre.niveis == [1, 2]
    assert [x.id for x in dre.linhas if x.nivel == 0] == [
        "sub-receita-liquida",
        "sub-lucro-bruto",
        "sub-margem-contribuicao",
        "sub-ebitda",
        "sub-resultado-antes-ir",
        "sub-lucro-liquido",
    ]
    # o último subtotal em destaque
    assert por_id["sub-lucro-liquido"].final
    assert not any(x.nivel == 3 for x in dre.linhas)
    # receita positiva, custo negativo, em R$ mil
    assert por_id["100"].realizado == 1
    assert por_id["100"].codigo == "100.00"
    assert por_id["120"].realizado == D("-0.6")
    # CMV sobre a receita bruta menos as devoluções
    assert por_id["120.01"].percentual_receita_bruta == D("61.2")
    assert por_id["120"].percentual_receita_bruta == 60
    assert por_id["140.02"].pai == "140"
    assert por_id["140.02"].nome == "Despesas Administrativas"
    assert por_id["140.02"].status == "critico"
    # 140 tem uma conta em desvio abaixo
    assert por_id["140"].com_desvio
    # a folha igual ao orçado pesa mais numa receita menor
    assert por_id["140.01"].status == "critico"
    # receita sem orçado: saudável
    assert por_id["160"].status == "saudavel"
    assert not por_id["160"].com_desvio
    # devolução sem orçado: diferença sem percentual
    assert por_id["110"].diferenca_percentual is None
    assert len(por_id["100"].tendencia.realizado) == 6
    assert dre.rodape == [
        "Sem movimento no período, por isso fora da tabela: 150 Depreciação e amortização, "
        "170 Despesas financeiras, 180 Despesas não operacionais, 190 Receitas não "
        "operacionais, 210 Participações dos sócios.",
        "Comparação: orçado (filtro “Comparar com”).",
    ]


def test_cascata_soma_ate_o_lucro(leituras: dict[str, Any]) -> None:
    cartao = _cartao(_montar(), "dre-cascata")
    assert isinstance(cartao, CartaoGraficos)
    g = cartao.graficos[0].grafico
    assert isinstance(g, GraficoCascata)
    passos = [(p.rotulo, p.de, p.ate, p.estilo) for p in g.passos]

    assert passos[0] == ("Receita", 0, 1000, "total")
    assert passos[1] == ("Deduções", 1000, 980, "negativo")
    assert ("EBITDA", 0, 170, "total") in passos
    assert ("Financeiro", 170, 180, "positivo") in passos
    assert passos[-1] == ("Lucro", 0, 160, "total")
    assert cartao.subtitulo == "Setembro. Para onde vai cada real vendido"


def test_ponto_de_equilibrio(leituras: dict[str, Any]) -> None:
    cartao = _cartao(_montar(), "ponto-equilibrio")
    assert isinstance(cartao, CartaoBarraPartes)

    assert [p.rotulo for p in cartao.partes] == ["Equilíbrio R$ 504", "Folga 49%"]
    assert sum(p.proporcao for p in cartao.partes) == 1
    assert cartao.partes[1].estilo == "saudavel"
    assert cartao.escala.fim == "Receita líquida de setembro: R$ 980"
    assert cartao.texto is not None
    assert cartao.texto[-1].texto.startswith(" Em abril")


def test_doze_meses_contra_o_orcado(leituras: dict[str, Any]) -> None:
    cartao = _cartao(_montar(), "resultado-12-meses")
    assert isinstance(cartao, CartaoGraficos)
    receita, _, fixas, *_ = cartao.graficos
    pontos = receita.grafico.pontos  # type: ignore[union-attr]

    assert len(pontos) == 12
    # 2025 sem orçado: sem traço
    assert pontos[0].referencia is None
    # receita 1.000 contra 1.050: mais de 2% abaixo
    assert pontos[-1].status == "critico"
    # despesas fixas: setembro acima do orçado
    assert fixas.grafico.pontos[-1].status == "critico"  # type: ignore[union-attr]
    assert cartao.chamada is not None
    assert cartao.chamada.texto[1].texto.startswith(
        "a receita bruta veio acima do orçado em 0 dos 9 meses"
    )


def test_sem_orcado_compara_com_o_mes_anterior_e_avisa(leituras: dict[str, Any]) -> None:
    leituras["linhas"] = _linhas(orcado=False)
    c = _montar()

    assert c.aviso is not None
    assert c.aviso.texto[0].texto == "Sem orçado para setembro: a comparação é com agosto."
    assert _cartao(c, "dre-gerencial").rotulo_comparacao == "Agosto"
    assert c.destaques[0].contexto == "0,0% acima de agosto"


def test_comparar_com_o_mes_anterior(leituras: dict[str, Any]) -> None:
    c = _montar({"comparar": "mes-anterior"})
    assert c.aviso is None
    assert _cartao(c, "dre-gerencial").rotulo_comparacao == "Agosto"
    # o acumulado do ano compara com o mesmo período do ano anterior
    ano = _montar({"comparar": "mes-anterior", "visao": "ano"})
    assert _cartao(ano, "dre-gerencial").rotulo_comparacao == "2025"
    assert _cartao(ano, "dre-gerencial").titulo == (
        "DRE gerencial de jan a set de 2026, pelo plano de contas"
    )


def test_acumulado_do_ano(leituras: dict[str, Any]) -> None:
    c = _montar({"visao": "ano"})
    assert c.destaques[0].valor.valor == 980 * 9  # type: ignore[union-attr]
    # sem a folga de meses antes, só no mês
    pe = _cartao(c, "ponto-equilibrio")
    assert not pe.texto[-1].texto.startswith(" Em")


def test_projecao_do_mes_em_aberto(leituras: dict[str, Any]) -> None:
    c = _montar({"visao": "projecao"})
    dre = _cartao(c, "dre-gerencial")
    receita = next(x for x in dre.linhas if x.id == "100")

    assert dre.titulo == "DRE gerencial de outubro (projeção), pelo plano de contas"
    # parcial de 400 no ritmo dos dias úteis: bem acima do parcial
    assert receita.realizado > D("0.4")
    pontos = _cartao(c, "resultado-12-meses").graficos[0].grafico.pontos
    assert pontos[-1].titulo == "out-26 (projeção)"


def test_mes_fechado_escolhido_sem_projecao(leituras: dict[str, Any]) -> None:
    c = _montar(escolhido=date(2026, 8, 1))
    assert [o.id for o in c.filtros[0].opcoes] == ["mes", "ano"]
    assert _cartao(c, "dre-gerencial").titulo.startswith("DRE gerencial de agosto")
    with pytest.raises(HTTPException) as erro:
        _montar({"visao": "projecao"}, escolhido=date(2026, 8, 1))
    assert erro.value.status_code == 422


def test_cenario_so_com_mais_de_um(leituras: dict[str, Any]) -> None:
    leituras["linhas"] = _linhas(cenarios=("base", "meta"))
    c = _montar()
    cenario = next(f for f in c.filtros if f.id == "cenario")
    assert cenario.padrao == "base"
    assert [o.rotulo for o in cenario.opcoes] == ["Base", "Meta"]

    meta = _montar({"cenario": "meta"})
    assert meta.destaques[0].contexto == "15,2% abaixo do orçado"
    with pytest.raises(HTTPException):
        _montar({"cenario": "pessimista"})


def test_filtro_desconhecido_422(leituras: dict[str, Any]) -> None:
    for pedidos in ({"visao": "trimestre"}, {"comparar": "ano-anterior"}):
        with pytest.raises(HTTPException) as erro:
            _montar(pedidos)
        assert erro.value.detail == "Filtro inválido."


def test_sem_lancamentos_vem_vazio(leituras: dict[str, Any]) -> None:
    leituras["lancamentos"] = False
    c = _montar({"comparar": "mes-anterior"})
    assert (c.filtros, c.destaques, c.kpis, c.apoios, c.cartoes) == ([], [], [], [], [])
    with pytest.raises(HTTPException):
        _montar({"visao": "ano"})


def test_a_decidir(leituras: dict[str, Any]) -> None:
    leituras["titulos"] = IndicadoresMensais(
        recebimento={SETEMBRO: D(30), date(2026, 8, 1): D(28)},
        pagamento={},
        inadimplencia={SETEMBRO: D(5)},
    )
    c = _montar(a_decidir=True)
    corpo = _payload(c)

    assert [x.id for x in c.cartoes] == [
        "resultado-12-meses",
        "dre-cascata",
        "ponto-equilibrio",
        "ponte-ebitda",
        "maiores-desvios",
        "dre-gerencial",
        "tabela-financeira",
    ]
    dre = _cartao(c, "dre-gerencial")
    assert dre.niveis == [1, 2, 3]
    nomes = {x.id: x.nome for x in dre.linhas if x.nivel == 3}
    assert nomes["140.02.04"] == "Telefone/Internet"
    assert nomes["140.01.01"] == "Salarios ( D )"
    assert nomes[resultado.FATURADO] == "Faturado das vendas"

    ponte = _cartao(c, "ponte-ebitda").graficos[0].grafico
    assert ponte.passos[0].rotulo == "Orçado"
    assert ponte.passos[-1].ate == 170
    assert ponte.piso is not None

    desvios = _cartao(c, "maiores-desvios")
    # no nível 3 o orçado da receita (100.01.21) não casa com o faturado das vendas
    assert desvios.grupos[0].itens[0].nome == "Bovinos"
    assert desvios.grupos[1].itens[0].nome == "Faturado das vendas"

    tabela = _cartao(c, "tabela-financeira")
    assert isinstance(tabela, CartaoTabela)
    assert [x.celulas[0].valor for x in tabela.linhas][-2:] == [
        "Prazo médio de recebimento",
        "Inadimplência",
    ]
    assert corpo["cartoes"][3]["graficos"][0]["grafico"]["tipo"] == "cascata"


def test_ponte_so_contra_o_orcado(leituras: dict[str, Any]) -> None:
    c = _montar({"comparar": "mes-anterior"}, a_decidir=True)
    assert "ponte-ebitda" not in [x.id for x in c.cartoes]
    # o par dos desvios ocupa a linha inteira
    assert _cartao(c, "maiores-desvios").largura == 12


# ---------- plano de contas ----------


def test_plano_competencia_e_fator() -> None:
    linhas = {
        "lancamentos": [
            {"mes": SETEMBRO, "conta": "140.01.01", "valor": D(100)},
            # só caixa (CA): fora da DRE
            {"mes": SETEMBRO, "conta": "100.03.06", "valor": D(-500)},
            # só competência (CO): entra
            {"mes": SETEMBRO, "conta": "110.01.02", "valor": D(30)},
            # fora do plano: fora da DRE, citada no rodapé
            {"mes": SETEMBRO, "conta": "140.09.99", "valor": D(7)},
        ],
        "orcamento": [
            {"mes": SETEMBRO, "conta": "120.01.07", "cenario": "", "valor": D(600)},
            # redutora de custo (fator +1): abate o CMV, com qualquer sinal no DW
            {"mes": SETEMBRO, "conta": "120.01.103", "cenario": "", "valor": D(-100)},
            {"mes": SETEMBRO, "conta": "100.03.06", "cenario": "", "valor": D(50)},
        ],
    }
    dados = resultado.dados_das_linhas(linhas, PLANO)

    assert dados.real[SETEMBRO] == {"140.01.01": D(100), "110.01.02": D(30)}
    assert dados.fora_do_plano == {SETEMBRO: {"140.09.99"}}
    orcado = dados.orcamento[""][SETEMBRO]
    assert orcado == {"120.01.07": D(600), "120.01.103": D(-100)}
    assert resultado.totais(orcado).g("120") == 500

    # sem plano cadastrado: a regra antiga (todas as contas, valor absoluto no orçado)
    antigo = resultado.dados_das_linhas(linhas, Plano())
    assert "100.03.06" in antigo.real[SETEMBRO]
    assert antigo.orcamento[""][SETEMBRO]["120.01.103"] == 100


def test_conta_fora_do_plano_no_rodape(leituras: dict[str, Any]) -> None:
    linhas = _linhas()
    linhas["lancamentos"].append({"mes": SETEMBRO, "conta": "140.09.99", "valor": D(7)})
    leituras["linhas"] = linhas
    dre = _cartao(_montar(), "dre-gerencial")
    assert dre.rodape[-2] == "Lançado em conta fora do plano de contas, fora da DRE: 140.09.99."
    assert "140.09" not in {x.id for x in dre.linhas}


# ---------- lançamentos agrupados (a view da DRE do cliente) ----------


def test_agrupados_vem_no_sinal_do_resultado() -> None:
    linhas = {
        "agrupados": [
            {"mes": SETEMBRO, "conta": "100.01.21", "valor": D(1000)},
            {"mes": SETEMBRO, "conta": "120.01.07", "valor": D(-600)},
            {"mes": SETEMBRO, "conta": "120.01.103", "valor": D(50)},
            {"mes": SETEMBRO, "conta": "140.01.01", "valor": D(-100)},
            # só caixa: fora, como nas outras fontes
            {"mes": SETEMBRO, "conta": "100.03.06", "valor": D(70)},
        ]
    }
    t = resultado.totais(resultado.dados_das_linhas(linhas, PLANO).real[SETEMBRO])

    assert t.receita_bruta == 1000
    # a redutora (+50) abate o CMV
    assert t.g("120") == 550
    assert t.ebitda == 350


def test_com_agrupados_a_tela_abre_sem_lancamentos(
    leituras: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    leituras["lancamentos"] = False
    monkeypatch.setattr(resultado, "tabelas_agrupados", lambda c, x: [1])
    leituras["linhas"] = {
        "agrupados": [
            {"mes": m, "conta": conta, "valor": D(v)}
            for m in (date(2026, 8, 1), SETEMBRO)
            for conta, v in (("100.01.21", 1000), ("120.01.07", -600), ("140.01.01", -100))
        ]
    }
    c = _montar({"comparar": "mes-anterior"})
    assert c.destaques[0].valor.valor == 1000  # type: ignore[union-attr]
    assert c.destaques[1].valor.valor == 300  # type: ignore[union-attr]


def test_agrupados_vazio_cai_na_regra_antiga(
    leituras: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    pedidas: list[bool] = []

    def consultas(*a: Any, com_agrupados: bool = True, **k: Any) -> dict[str, Any]:
        pedidas.append(com_agrupados)
        return {}

    idas = iter([{"agrupados": [], "orcamento": []}, _linhas()])
    monkeypatch.setattr(resultado, "consultas", consultas)
    monkeypatch.setattr(resultado, "em_uma_ida", lambda conn, c: next(idas))
    c = _montar()

    assert pedidas == [True, False]
    assert c.destaques[0].valor.valor == 980  # type: ignore[union-attr]

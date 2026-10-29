"""FIN-03 — Receber e pagar: regras — sem banco.

As leituras (fontes e as duas idas ao banco) são trocadas por linhas fictícias; contas,
textos, cartões e o contrato rodam de verdade.
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
from app.telas.calendario import feriados_nacionais, ultimo_dia_do_mes
from app.telas.moldura import Cliente, UltimaCarga
from app.telas.paineis import receber_pagar as rp
from app.telas.paineis.base import Conteudo
from app.telas.paineis.contrato import (
    CartaoBarrasHorizontais,
    CartaoDoisLados,
    CartaoJanelas,
    CartaoTabela,
    PainelPayload,
    PillComRotulo,
)
from app.telas.visao_geral.vendas import TabelaDoCliente, mes_menos

D = Decimal
CARGA = UltimaCarga(data_snapshot=date(2026, 10, 13), fim=None)
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
        codigo_tela="FIN-03",
        ve_dado_nominal=True,
    )


def _tabela(nome: str, papeis: dict[str, str]) -> TabelaDoCliente:
    return TabelaDoCliente(nome, papeis, frozenset(), frozenset(papeis.values()))


def _fontes(*, vendedor: bool = True, limite: bool = True, pagar: bool = True) -> rp.Fontes:
    receber = dict(rp.COLUNAS_RECEBER_PAINEL)
    if not vendedor:
        receber.pop("vendedor")
    clientes = dict(rp.COLUNAS_CLIENTE)
    if not limite:
        clientes.pop("limite")
    return rp.Fontes(
        receber=[_tabela("f_receitas_pj", receber)],
        pagar=[_tabela("f_despesas_pj", dict(rp.COLUNAS_PAGAR_PAINEL))] if pagar else [],
        clientes=[_tabela("d_cliente_pj", clientes)],
        fornecedores=[_tabela("d_fornecedor_pj", dict(rp.COLUNAS_FORNECEDOR))],
        vendedores=[_tabela("d_vendedor_pj", dict(rp.COLUNAS_VENDEDOR))],
    )


def _carteira(**valores: int) -> list[Decimal]:
    base = dict.fromkeys(rp.CARTEIRA, 0)
    base.update(valores)
    return [D(base[k]) for k in rp.CARTEIRA]


def _lado(carteira: list[Decimal], prazo: int, vencido_pct: int = 5) -> list[dict[str, Any]]:
    """Carteira de hoje, 12 fins de mês com 1.000 em aberto e `vencido_pct`% vencido (o
    último 8%), pagos de 1.000 por mês com o `prazo`, e 6 safras."""
    linhas: list[dict[str, Any]] = [{"parte": "carteira", "dia": None, "v": carteira}]
    for n in range(12, 0, -1):
        m = mes_menos(OUTUBRO, n)
        pct = 8 if n == 1 else vencido_pct
        faixas = [D(pct) * 10 * f for f in (D("0.4"), D("0.3"), D("0.2"), D("0.1"), D(0))]
        linhas.append({"parte": "serie", "dia": ultimo_dia_do_mes(m), "v": [*faixas, D(1000)]})
        linhas.append({"parte": "pagos", "dia": m, "v": [D(1000), D(1000 * prazo)]})
    for n in range(6, 0, -1):
        m = mes_menos(OUTUBRO, n)
        linhas.append({"parte": "safra", "dia": m, "v": [D(1000), D(100 + n), D(50), D(30), D(20)]})
    return linhas


def _pessoa(codigo: str, nome: str | None, **campos: Any) -> dict[str, Any]:
    p: dict[str, Any] = {
        "pessoa": codigo,
        "nome": nome,
        "aberto": D(0),
        "vencido": D(0),
        "atraso": None,
        "aberto_total": D(0),
        "vencido_total": False,
        "vendedor": None,
        "historico": None,
        "historico_pago": None,
        "ultima": date(2026, 10, 1),
        "limite": None,
        "ordem_cobrar": None,
        "ordem_aberto": None,
        "ordem_limite": None,
        "tem_limite": True,
    }
    p.update(campos)
    return p


CLIENTES = [
    _pessoa(
        "10",
        "MERCANTIL AURORA",
        aberto=D(142000),
        vencido=D(58200),
        atraso=12,
        aberto_total=D(142000),
        vencido_total=True,
        vendedor="1",
        historico=D(1000),
        historico_pago=D(800),
        ultima=date(2026, 7, 1),
        limite=D(60000),
        ordem_cobrar=1,
        ordem_aberto=1,
        ordem_limite=1,
    ),
    _pessoa(
        "11",
        None,
        aberto=D(30000),
        vencido=D(9800),
        atraso=97,
        aberto_total=D(30000),
        vencido_total=True,
        vendedor="2",
        ordem_cobrar=2,
        ordem_aberto=2,
    ),
]
FORNECEDORES = [_pessoa("90", "Frigorífico Boi Bom", aberto=D(1060000), ordem_aberto=1)]


def _feriados(_conn: Any, _cliente: Any, inicio: date, fim: date) -> set[date]:
    todos = set().union(*(feriados_nacionais(a) for a in range(inicio.year, fim.year + 1)))
    return {d for d in todos if inicio <= d <= fim}


@pytest.fixture
def leituras(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    estado: dict[str, Any] = {
        "fontes": _fontes(),
        "receber": _lado(
            _carteira(
                a_vencer=6602,
                compensando=0,
                ate_30=320,
                ate_60=98,
                ate_90=52,
                mais_90=58,
                prox_7=1200,
                prox_15=2600,
                prox_30=5200,
                pessoas_atraso=64,
                pessoas=1400,
            ),
            prazo=30,
        ),
        # a pagar: 3.000 lançados em 30 dias contra 6.000 pagos por mês
        "pagar": _lado(
            _carteira(
                a_vencer=4874,
                ate_30=40,
                prox_7=500,
                prox_15=1000,
                prox_30=3000,
                semana_1=500,
                semana_2=400,
                semana_3=560,
                semana_4=2010,
            ),
            prazo=24,
        ),
        "chamadas": [],
    }
    for linha in estado["pagar"]:
        if linha["parte"] == "pagos":
            linha["v"] = [D(6000), D(6000 * 24)]

    def em_uma_ida(conn: Any, consultas: dict[str, Any]) -> dict[str, Any]:
        estado["chamadas"].append(sorted(consultas))
        if "codigos" in consultas:
            return {
                "codigos": [{"vendedor": "1"}, {"vendedor": "2"}],
                "cadastro": [{"codigo": "1", "nome": "DIEGO ALVES"}, {"codigo": "2", "nome": None}],
            }
        linhas = {"receber": estado["receber"], "clientes": CLIENTES}
        if "pagar" in consultas:
            linhas |= {"pagar": estado["pagar"], "fornecedores": FORNECEDORES}
        return linhas

    monkeypatch.setattr(rp, "fontes", lambda conn, ctx: estado["fontes"])
    monkeypatch.setattr(rp, "em_uma_ida", em_uma_ida)
    monkeypatch.setattr(rp, "feriados", _feriados)
    monkeypatch.setattr(rp, "farol", lambda conn, c, indicador, ref: PADROES[indicador])
    return estado


def _montar(
    pedidos: dict[str, str | None] | None = None, escolhido: date | None = None
) -> Conteudo:
    return rp.montar(_ctx(), escolhido, CARGA, pedidos or {})


def _cartao(c: Conteudo, id_: str) -> Any:
    return next(x for x in c.cartoes if x.id == id_)


def _payload(c: Conteudo) -> dict[str, Any]:
    return PainelPayload(
        tela="receber-pagar",
        versao_contrato="1.0",
        cliente=Cliente(nome="X", sigla="X", logo_url=None),
        dados_ate=CARGA.data_snapshot,
        atualizado_em=None,
        filtros=c.filtros,
        destaques=c.destaques,
        kpis=c.kpis,
        apoios=c.apoios,
        cartoes=c.cartoes,
    ).model_dump(mode="json", by_alias=True, exclude_defaults=True)


# ---------- regras ----------


def test_posicao_d2_e_fins_de_mes(leituras: dict[str, Any]) -> None:
    pos = rp.posicao(object(), _ctx(), date(2026, 10, 13))  # type: ignore[arg-type]
    # 12/10 é feriado: o D+2 de 08/10 cai em 13/10
    assert pos.limite == date(2026, 10, 8)
    assert pos.fins[0] == date(2025, 10, 31)
    assert pos.fins[-1] == date(2026, 9, 30)
    assert len(pos.limites) == 12


def test_chance_de_receber() -> None:
    def chance(pago: int | None) -> Any:
        historico = None if pago is None else D(100)
        p = rp.pessoas_das_linhas(
            [_pessoa("1", "X", historico=historico, historico_pago=D(pago or 0))]
        )[0][0]
        return rp.chance(p)

    assert chance(95).pill == PillComRotulo(status="saudavel", rotulo="Alta")
    assert chance(75).pill == PillComRotulo(status="atencao", rotulo="Média")
    assert chance(10).pill == PillComRotulo(status="critico", rotulo="Baixa")
    assert chance(None).valor == "sem histórico"


def test_opcoes_de_vendedor_pelo_nome_do_cadastro() -> None:
    o = rp.opcoes_vendedor(["2", "1", "3"], {"1": "DIEGO ALVES", "3": "Diego Alves"})
    assert o is not None
    assert [(x.id, x.rotulo) for x in o.filtro.opcoes] == [
        ("todos", "Todos"),
        ("diego-alves", "Diego Alves"),
        ("diego-alves-3", "Diego Alves"),
        ("vendedor-2", "Vendedor 2"),
    ]
    assert o.valores["diego-alves-3"] == ("3",)
    assert rp.opcoes_vendedor([], {}) is None


def test_status_da_semana_e_da_safra() -> None:
    assert rp.status_semana(D(100), D(100)) == "saudavel"
    assert rp.status_semana(D(120), D(100)) == "atencao"
    assert rp.status_semana(D(140), D(100)) == "critico"
    assert rp.status_semana(D(140), None) is None
    assert rp.status_safra(D(10), D(10)) == "saudavel"
    assert rp.status_safra(D(11), D(10)) == "atencao"
    assert rp.status_safra(D(12), D(10)) == "critico"


# ---------- a tela ----------


def test_tela_completa(leituras: dict[str, Any]) -> None:
    c = _montar()
    corpo = _payload(c)

    assert [f["id"] for f in corpo["filtros"]] == ["vendedor", "atraso"]
    assert [k.rotulo for k in c.kpis] == [
        "A receber em 30 dias",
        "A pagar em 30 dias",
        "Inadimplência",
    ]
    pagar = c.kpis[1]
    assert pagar.contexto == "só o que está lançado: 50% da média paga"
    assert pagar.status == "atencao"
    inad = c.kpis[2]
    # 528 de 7.130 em aberto
    assert inad.valor.valor == D("7.41")
    assert inad.contexto == "média de 12 meses: 5,3%"
    assert [x.id for x in c.cartoes] == [
        "proximos-dias",
        "contas-receber-pagar",
        "quem-cobrar",
        "pagar-semana",
        "inadimplencia-safra",
        "inadimplencia-12-meses",
        "idade-vencidos",
        "prazos",
        "limite-credito",
    ]
    # só as duas idas: vendedores e a leitura principal
    assert leituras["chamadas"] == [
        ["cadastro", "codigos"],
        ["clientes", "fornecedores", "pagar", "receber"],
    ]


def test_janelas_com_o_alerta_de_dado_incompleto(leituras: dict[str, Any]) -> None:
    janelas = _cartao(_montar(), "proximos-dias")
    assert isinstance(janelas, CartaoJanelas)
    trinta = janelas.janelas[2]

    assert trinta.titulo == "Próximos 30 dias"
    assert [(x.rotulo, x.valor.valor, x.tipo) for x in trinta.linhas] == [
        ("A receber", 5200, None),
        ("A pagar", -3000, None),
        ("Sobra no período", 2200, "total"),
    ]
    assert janelas.alerta is not None
    assert janelas.alerta.texto[1].texto == "50% da média paga"


def test_dois_lados(leituras: dict[str, Any]) -> None:
    cartao = _cartao(_montar(), "contas-receber-pagar")
    assert isinstance(cartao, CartaoDoisLados)
    receber, pagar = cartao.lados

    assert receber.total == "em aberto: R$ 7 mil"
    assert [k.rotulo for k in receber.kpis] == ["Vencido", "Prazo médio", "Clientes em atraso"]
    assert receber.kpis[1].contexto == "média de 12 meses: 30"
    assert receber.kpis[2].contexto == "4,6% dos 1400 clientes com título em aberto"
    assert [(i.rotulo, i.status) for i in receber.idade] == [
        ("A vencer", "saudavel"),
        ("1 a 30 dias", "atencao"),
        ("31 a 60", "critico"),
        ("61 a 90", "critico"),
        ("Mais de 90", "critico"),
    ]
    assert receber.maiores[0].nome == "Mercantil Aurora"
    assert receber.maiores[0].etiqueta == "vencido"
    assert receber.maiores[1].nome == "Cliente 11"
    assert receber.observacao is None
    assert pagar.alerta == "lançado: 50% da média paga"
    assert pagar.maiores[0].nome == "Frigorífico Boi Bom"
    # a pagar sem vencido: a faixa vazia fica sem status
    assert pagar.idade[2].status is None


def test_quem_cobrar(leituras: dict[str, Any]) -> None:
    tabela = _cartao(_montar(), "quem-cobrar")
    assert isinstance(tabela, CartaoTabela)
    aurora, sem_nome = tabela.linhas

    assert [c.rotulo for c in tabela.colunas][-1] == "Vendedor"
    assert aurora.celulas[0].etiqueta == "parou de comprar"
    assert aurora.celulas[2].fundo == "saudavel"
    assert aurora.celulas[3].pill == PillComRotulo(status="atencao", rotulo="Média")
    assert aurora.celulas[4].valor == "Diego"
    assert sem_nome.celulas[0].etiqueta is None
    assert sem_nome.celulas[2].fundo == "critico"
    assert sem_nome.celulas[4].valor == "Vendedor 2"


def test_pagar_por_semana(leituras: dict[str, Any]) -> None:
    cartao = _cartao(_montar(), "pagar-semana")
    assert isinstance(cartao, CartaoBarrasHorizontais)

    assert [i.sub for i in cartao.itens][:2] == ["13/10 a 19/10", "20/10 a 26/10"]
    # média paga por semana: 6.000 × 12 ÷ 52 ≈ 1.385
    assert [i.status for i in cartao.itens] == ["saudavel", "saudavel", "saudavel", "critico"]
    assert cartao.chamada is not None
    assert cartao.chamada.texto[0].texto == "Semana 4 (03/11 a 09/11): R$ 2 mil a pagar"


def test_safra_ainda_nao(leituras: dict[str, Any]) -> None:
    tabela = _cartao(_montar(), "inadimplencia-safra")
    agosto, setembro = tabela.linhas[-2:]

    assert agosto.celulas[0].valor == "ago-26"
    assert agosto.celulas[1].valor.valor == D("10.20")
    assert agosto.celulas[3].valor == "ainda não"
    assert all(c.valor == "ainda não" for c in setembro.celulas[1:])


def test_series_de_12_meses(leituras: dict[str, Any]) -> None:
    c = _montar()
    linhas = _cartao(c, "inadimplencia-12-meses").graficos[0].grafico
    assert linhas.marcadores[0].rotulo == "8,0%"
    assert linhas.referencia.rotulo == "média de 12 meses 5,3%"
    idade = _cartao(c, "idade-vencidos").graficos[0].grafico
    assert idade.paleta == "atraso"
    assert [s.nome for s in idade.series] == list(rp.FAIXAS_SERIE)
    prazos = _cartao(c, "prazos").graficos[0].grafico
    assert [s.rotulo_fim for s in prazos.series] == ["Pagamento, 24 d", "Recebimento, 30 d"]


def test_limite_de_credito(leituras: dict[str, Any]) -> None:
    tabela = _cartao(_montar(), "limite-credito")
    (linha,) = tabela.linhas
    assert linha.celulas[0].etiqueta == "título vencido"
    assert linha.celulas[3].valor.valor == 82000

    leituras["fontes"] = _fontes(limite=False)
    assert "limite-credito" not in [x.id for x in _montar().cartoes]


def test_filtro_de_vendedor_tira_o_a_pagar_das_janelas(leituras: dict[str, Any]) -> None:
    c = _montar({"vendedor": "diego-alves", "atraso": "ate-30"})
    janelas = _cartao(c, "proximos-dias")

    assert [x.rotulo for x in janelas.janelas[0].linhas] == ["A receber"]
    assert janelas.alerta is None
    assert c.kpis[0].contexto == "títulos do vendedor"
    assert _cartao(c, "contas-receber-pagar").lados[0].titulo == "Contas a receber do vendedor"


def test_sem_vendedor_nos_titulos(leituras: dict[str, Any]) -> None:
    leituras["fontes"] = _fontes(vendedor=False)
    c = _montar()
    assert [f.id for f in c.filtros] == ["atraso"]
    assert [x.rotulo for x in _cartao(c, "quem-cobrar").colunas][-1] == "Chance"
    with pytest.raises(HTTPException):
        _montar({"vendedor": "diego-alves"})


def test_so_a_receber(leituras: dict[str, Any]) -> None:
    leituras["fontes"] = _fontes(pagar=False)
    c = _montar()
    assert [k.rotulo for k in c.kpis] == ["A receber em 30 dias", "Inadimplência"]
    assert "pagar-semana" not in [x.id for x in c.cartoes]
    # sem o par (a pagar por semana), "Quem cobrar" ocupa a linha inteira
    assert _cartao(c, "quem-cobrar").largura == 12
    assert _cartao(c, "inadimplencia-safra").largura == 7
    assert _cartao(c, "inadimplencia-12-meses").largura == 5


def test_filtro_desconhecido_422(leituras: dict[str, Any]) -> None:
    for pedidos in ({"atraso": "mais-120"}, {"vendedor": "ninguem"}):
        with pytest.raises(HTTPException) as erro:
            _montar(pedidos)
        assert erro.value.detail == "Filtro inválido."


def test_sem_titulos_vem_vazio(leituras: dict[str, Any]) -> None:
    leituras["fontes"] = rp.Fontes([], [], [], [], [])
    c = _montar()
    assert (c.filtros, c.kpis, c.cartoes) == ([], [], [])
    with pytest.raises(HTTPException):
        _montar({"atraso": "ate-30"})


def test_mes_fechado_usa_o_fim_do_mes(
    leituras: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    posicoes: list[date] = []
    original = rp.posicao

    def posicao(conn: Any, ctx: Any, hoje: date) -> rp.Posicao:
        posicoes.append(hoje)
        return original(conn, ctx, hoje)

    monkeypatch.setattr(rp, "posicao", posicao)
    _montar(escolhido=date(2026, 8, 1))
    assert posicoes == [date(2026, 8, 31)]

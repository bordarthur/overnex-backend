"""Regras dos painéis comerciais — sem banco.

As leituras (fontes, vendas, metas, vendedores…) são trocadas por dados fictícios; o resto
(contas, textos, cartões e o contrato) roda de verdade.
"""

from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.core.db import Sessao
from app.farol.parametro import PADROES
from app.seguranca.acesso import ContextoConsolidado
from app.seguranca.auth import UsuarioToken
from app.telas.calendario import dias_uteis, feriados_nacionais
from app.telas.moldura import UltimaCarga
from app.telas.paineis import base, clientes, equipe, vendas
from app.telas.paineis.contrato import PainelPayload
from app.telas.visao_geral.vendas import TabelaDoCliente

D = Decimal
CARGA = UltimaCarga(data_snapshot=date(2026, 10, 13), fim=None)
OUTUBRO = date(2026, 10, 1)
ULTIMA_VENDA = date(2026, 10, 9)


def _ctx() -> ContextoConsolidado:
    sessao = Sessao(conn=object(), usuario=UsuarioToken(id_autenticacao=UUID(int=1)))  # type: ignore[arg-type]
    return ContextoConsolidado(
        sessao=sessao,
        usuario_id=UUID(int=2),
        cliente_id=UUID(int=3),
        empresa_id=None,
        empresas=(UUID(int=4),),
        papel="gestor",
        codigo_tela="COM-01",
        ve_dado_nominal=True,
    )


def _tabela(nome: str, papeis: dict[str, str]) -> TabelaDoCliente:
    return TabelaDoCliente(nome, papeis, frozenset(), frozenset(papeis.values()))


FONTES = base.Fontes(
    vendas=[
        _tabela(
            "f_vendas_pj",
            {
                "data": "en_dataemissao",
                "valor": "en_valortotal",
                "custo": "en_custototal",
                "pedido": "en_numpedido",
                "cliente": "en_codigocliente",
                "produto": "en_codigoproduto",
                "vendedor": "en_codigovendedor",
                "linha": "it_grupo",
                "quantidade": "en_quantidade",
            },
        )
    ],
    metas=[_tabela("f_metas_comercial_pj", dict(base.COLUNAS_METAS_PAINEL))],
    vendedores=[_tabela("d_vendedor_pj", dict(base.COLUNAS_VENDEDOR))],
    produtos=[_tabela("d_bitem_pj", dict(base.COLUNAS_PRODUTO))],
    receber=[_tabela("f_receitas_pj", dict(base.COLUNAS_RECEBER_PAINEL))],
)

VENDEDORES = {
    "1": base.Vendedor("1", "ANA RIBEIRO", "ATACADO", "Paula"),
    "2": base.Vendedor("2", "BRUNO LIMA", "Varejo", "Marcos"),
    "3": base.Vendedor("3", "ELISA ROCHA", "Varejo", "Marcos"),
    "4": base.Vendedor("4", "DIEGO ALVES", "Varejo", "Marcos"),
}


def _meses(n: int) -> list[date]:
    return base.meses_antes(OUTUBRO, n)


def _metas() -> base.Metas:
    valor: dict[tuple[date, str | None], Decimal] = {}
    clientes_: dict[tuple[date, str | None], Decimal] = {}
    for m in [*_meses(24), OUTUBRO]:
        for codigo, meta in (("1", 1000), ("2", 800), ("3", 600), ("4", 700)):
            valor[(m, codigo)] = D(meta)
            clientes_[(m, codigo)] = D(10)
    return base.Metas(valor=valor, clientes=clientes_)


def _feriados(_conn: Any, _cliente: Any, inicio: date, fim: date) -> set[date]:
    todos = set().union(*(feriados_nacionais(a) for a in range(inicio.year, fim.year + 1)))
    return {d for d in todos if inicio <= d <= fim}


@pytest.fixture
def leituras(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """Troca as leituras do banco nos três módulos; guarda os argumentos de ler_dados."""
    # resumo por vendedor que a leitura inicial devolve (os testes da Equipe trocam)
    chamadas: dict[str, Any] = {"resumo": {}}
    panorama = base.Panorama(mes=OUTUBRO, ultimo_dia=ULTIMA_VENDA, linhas=["BOVINOS", "Aves"])

    def ler_inicio(conn: Any, ctx: Any, f: Any, mes: Any, **_: Any) -> base.InicioPainel:
        return base.InicioPainel(panorama, VENDEDORES, _metas(), chamadas["resumo"])

    for modulo in (vendas, equipe, clientes):
        monkeypatch.setattr(modulo, "fontes", lambda conn, ctx: FONTES)
        monkeypatch.setattr(modulo, "ler_inicio", ler_inicio)
        monkeypatch.setattr(modulo, "feriados", _feriados)
    for modulo in (vendas, equipe):
        monkeypatch.setattr(modulo, "farol", lambda conn, c, indicador, ref: PADROES[indicador])
    monkeypatch.setattr(base, "feriados", _feriados)
    monkeypatch.setattr(clientes, "_parametro", lambda conn, ctx, chave, mes: None)
    return chamadas


def _payload(tela: Any, conteudo: base.Conteudo) -> dict[str, Any]:
    """Monta o payload como a rota (o contrato valida) e devolve o JSON."""
    p = PainelPayload(
        tela=tela,
        versao_contrato="1.0",
        cliente={"nome": "X", "sigla": "X", "logo_url": None},  # type: ignore[arg-type]
        dados_ate=CARGA.data_snapshot,
        atualizado_em=None,
        filtros=conteudo.filtros,
        destaques=conteudo.destaques,
        kpis=conteudo.kpis,
        apoios=conteudo.apoios,
        cartoes=conteudo.cartoes,
    )
    return PainelPayload.model_validate(p.model_dump()).model_dump(
        mode="json", by_alias=True, exclude_defaults=True
    )


# ---------- filtros ----------


def test_opcoes_juntam_grafias_e_tem_o_padrao() -> None:
    o = base.opcoes("canal", "Canal", ["ATACADO", "Atacado ", "Televendas", None, ""])
    assert o.filtro.padrao == "todos"
    assert [(x.id, x.rotulo) for x in o.filtro.opcoes] == [
        ("todos", "Todos"),
        ("atacado", "Atacado"),
        ("televendas", "Televendas"),
    ]
    assert o.valores["atacado"] == ("ATACADO", "Atacado ")
    assert base.slug("Suínos e Aves") == "suinos-e-aves"


def test_escolher_padrao_valido_e_invalido() -> None:
    o = base.opcoes("linha", "Linha", ["Bovinos"], todas=True)
    assert base.escolher(o, None) is None
    assert base.escolher(o, "todas") is None
    assert base.escolher(o, "bovinos") == ("Bovinos",)
    for invalido in ("suinos", "todos"):
        with pytest.raises(HTTPException) as erro:
            base.escolher(o, invalido)
        assert erro.value.status_code == 422
        assert erro.value.detail == "Filtro inválido."
    # filtro que a tela não tem
    with pytest.raises(HTTPException):
        base.escolher(None, "x")


# ---------- regras puras ----------


def test_em_queda_conta_os_meses_seguidos() -> None:
    assert equipe.em_queda([D(96), D(92), D(88), D(85), D(81), D(78)]) == 5
    assert equipe.em_queda([D(90), D(95), D(93)]) == 1
    assert equipe.em_queda([D(90), None, D(80)]) == 0
    assert equipe.em_queda([D(80), D(90)]) == 0


def test_historico_usa_a_tabela_nos_meses_dela_e_o_resumo_antes() -> None:
    tabela = {(date(2026, 9, 1), "1"): D(10)}
    resumo = {(date(2026, 9, 1), "1"): D(99), (date(2026, 8, 1), "1"): D(7)}
    assert equipe.historico(tabela, resumo) == {
        (date(2026, 9, 1), "1"): D(10),
        (date(2026, 8, 1), "1"): D(7),
    }


def test_curva_de_rampa_media_dos_melhores_nos_primeiros_meses() -> None:
    inicio = date(2024, 1, 1)
    primeiros = {
        "a": date(2025, 1, 1),
        "b": date(2025, 3, 1),
        "velho": inicio,
        "novo": date(2026, 8, 1),
    }
    # "a" rende 50, 60… nos primeiros meses; "b", 70, 80…; os dois com 100 nos últimos 6
    rampa = {"a": [50, 60, 70, 80, 90, 100], "b": [70, 80, 90, 100, 110, 120]}

    def atingimento(m: date, v: str) -> Decimal | None:
        if v in rampa:
            k = equipe.meses_de_casa(primeiros[v], m) - 1
            if 0 <= k < 6:
                return D(rampa[v][k])
            return D(100)
        return D(100)

    curva = equipe.curva_de_rampa(
        ["a", "b", "velho", "novo"], OUTUBRO, primeiros, inicio, atingimento
    )
    # "velho" já vendia no início dos dados; "novo" ainda está na rampa
    assert curva == [D(60), D(70), D(80), D(90), D(100), D(110)]
    assert equipe.curva_de_rampa(["a"], OUTUBRO, primeiros, inicio, atingimento) is None


def _produto(codigo: str, valor: int, custo: int) -> clientes.Produto:
    return clientes.Produto(codigo, codigo, "Bovinos", D(valor), D(custo), D(valor), D(valor))


def test_curva_abc_e_cortes_da_margem() -> None:
    produtos = [
        _produto("p1", 700, 600),
        _produto("p2", 150, 90),
        _produto("p3", 100, 70),
        _produto("p4", 30, 10),
        _produto("p5", 20, 5),
    ]
    curva = clientes.curva_abc(produtos)
    assert curva is not None
    # p1 começa em 0%; p2 em 70%: ainda A; p3 em 85%: B; p4 em 95%: C
    assert [i.classe for i in curva] == ["A", "A", "B", "C", "C"]
    assert curva[-1].acumulado == 100

    tercos = clientes.cortes_margem(curva, None)
    assert tercos.tercos
    assert [tercos.classe(i.p.margem) for i in curva] == ["C", "B", "B", "A", "A"]
    fixos = clientes.cortes_margem(curva, {"a": 33, "b": 27})
    assert (fixos.a, fixos.b, fixos.tercos) == (D(33), D(27), False)
    assert fixos.classe(D("30")) == "B"


def test_faixas_de_valor() -> None:
    padrao = list(clientes.FAIXAS_PADRAO)
    assert clientes.rotulos_faixas(padrao) == [
        "Até R$ 500",
        "R$ 500 a 2 mil",
        "R$ 2 a 5 mil",
        "R$ 5 a 10 mil",
        "Acima de R$ 10 mil",
    ]
    assert clientes.limites_faixas({"limites": [100, 1000]}) == [D(100), D(1000)]
    # fora de ordem ou inválido: o padrão
    assert clientes.limites_faixas({"limites": [1000, 100]}) == padrao
    assert clientes.limites_faixas("x") == padrao


# ---------- Vendas ----------


def _dias_uteis_ate(dia: date) -> list[date]:
    fer = _feriados(None, None, OUTUBRO, dia)
    d, dias = OUTUBRO, []
    while d <= dia:
        if d.weekday() < 5 and d not in fer:
            dias.append(d)
        d += timedelta(days=1)
    return dias


def _dados_vendas() -> vendas.DadosVendas:
    dados = vendas.DadosVendas()
    for i, dia in enumerate(_dias_uteis_ate(ULTIMA_VENDA)):
        dados.dias[dia] = (D(100 + 10 * i), D(5))
    total = sum((v for v, _ in dados.dias.values()), D(0))
    dados.meses[OUTUBRO] = vendas.MesVendas(total, total * D("0.7"), D(30), 40, 20)
    for k, m in enumerate(_meses(12)):
        dados.meses[m] = vendas.MesVendas(D(3000 + k), D(2000), D(90), 100, 30)
        dados.linhas[(m, "BOVINOS")] = (D(2000), D(1500))
        dados.linhas[(m, "Aves")] = (D(1000), D(600))
    dados.linhas[(OUTUBRO, "BOVINOS")] = (total * D("0.6"), total * D("0.5"))
    dados.linhas[(OUTUBRO, "Aves")] = (total * D("0.4"), total * D("0.2"))
    dados.por_vendedor = {"1": total * D("0.5"), "2": total * D("0.3"), "3": total * D("0.2")}
    # mês anterior: 30 clientes, metade até o 6º dia útil
    dados.novos_anterior = {date(2026, 9, 2): 15, date(2026, 9, 25): 15}
    return dados


def test_vendas_monta_o_painel_e_passa_os_filtros(
    leituras: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    dados = _dados_vendas()

    def ler_dados(conn: Any, ctx: Any, f: Any, mes: date, vend: Any, linhas: Any) -> Any:
        leituras["vendas"] = (mes, vend, linhas)
        return dados

    monkeypatch.setattr(vendas, "ler_dados", ler_dados)
    conteudo = vendas.montar(_ctx(), None, CARGA, {"canal": "varejo"})
    corpo = _payload("vendas", conteudo)

    # canal varejo → os vendedores do varejo; sem linha
    mes, vend, linhas = leituras["vendas"]
    assert mes == OUTUBRO
    assert sorted(vend) == ["2", "3", "4"]
    assert linhas is None
    assert [f["id"] for f in corpo["filtros"]] == ["canal", "linha"]
    assert [o["id"] for o in corpo["filtros"][1]["opcoes"]] == ["todas", "aves", "bovinos"]

    total = dados.meses[OUTUBRO].valor
    du_mes = dias_uteis(
        OUTUBRO, date(2026, 10, 31), _feriados(None, None, OUTUBRO, date(2026, 10, 31))
    )
    du = len(_dias_uteis_ate(ULTIMA_VENDA))
    vendido, projecao, necessario = corpo["destaques"]
    assert vendido["rotulo"] == "Vendido até 09/10"
    assert vendido["valor"]["valor"] == float(total)
    assert projecao["valor"]["valor"] == pytest.approx(float(total / du * du_mes), abs=0.01)
    # meta do varejo: 800 + 600 + 700
    meta = D(2100)
    assert necessario["valor"]["valor"] == pytest.approx(
        float((meta - total) / (du_mes - du)), abs=0.01
    )
    ids = [c["id"] for c in corpo["cartoes"]]
    assert ids == [
        "andamento-mes",
        "fatores-meta",
        "ritmo-dia",
        "linhas-produto",
        "canais",
        "vendas-12-meses",
        "participacao-linhas",
    ]
    andamento = corpo["cartoes"][0]
    assert [v["id"] for v in andamento["visoes"]] == ["valor", "volume"]
    # sem supermeta
    assert {s["estilo"] for s in andamento["visoes"][0]["grafico"]["series"]} == {
        "meta",
        "projecao",
        "realizado",
    }
    canais = corpo["cartoes"][4]
    assert [i["nome"] for i in canais["itens"]] == ["Varejo"]


def test_vendas_com_filtro_de_linha_tira_a_meta(
    leituras: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(vendas, "ler_dados", lambda *a: _dados_vendas())
    corpo = _payload("vendas", vendas.montar(_ctx(), None, CARGA, {"linha": "bovinos"}))

    ids = [c["id"] for c in corpo["cartoes"]]
    assert "fatores-meta" not in ids
    assert "linhas-produto" not in ids
    assert "participacao-linhas" not in ids
    assert "da meta" not in corpo["destaques"][0]["contexto"]
    # o ritmo fica sozinho na fileira
    ritmo = next(c for c in corpo["cartoes"] if c["id"] == "ritmo-dia")
    assert ritmo["largura"] == 12


def test_vendas_filtro_invalido_422(leituras: dict[str, Any]) -> None:
    with pytest.raises(HTTPException) as erro:
        vendas.montar(_ctx(), None, CARGA, {"linha": "suinos"})
    assert erro.value.status_code == 422


def test_vendas_mes_fechado_sem_projecao(
    leituras: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(vendas, "ler_dados", lambda *a: _dados_vendas())
    novembro = UltimaCarga(data_snapshot=date(2026, 11, 20), fim=None)
    corpo = _payload("vendas", vendas.montar(_ctx(), OUTUBRO, novembro, {}))

    rotulos = [d["rotulo"] for d in corpo["destaques"]]
    assert rotulos == ["Vendido em outubro", "Meta de outubro"]
    andamento = corpo["cartoes"][0]
    assert "projecao" not in {t["estilo"] for t in andamento["regua"]["trechos"]}


# ---------- Equipe ----------


def _dados_equipe() -> equipe.DadosEquipe:
    d = equipe.DadosEquipe()
    for codigo, valor, clientes_ in (("1", 500, 8), ("2", 250, 6), ("3", 300, 9), ("4", 200, 4)):
        d.mes[codigo] = equipe.DoMes(D(valor), D(valor) * D("0.7"), clientes_)
        d.mensal[(OUTUBRO, codigo)] = D(valor)
        for k, m in enumerate(_meses(12)):
            d.mensal[(m, codigo)] = D(valor * 2 - 10 * k * (codigo == "4"))
    d.mes["99"] = equipe.DoMes(D(50), D(30), 1)
    d.equipe = equipe.DoMes(D(1250), D(875), 25)
    d.novos_anterior = {date(2026, 9, 2): 10, date(2026, 9, 28): 10}
    return d


def test_equipe_monta_o_painel(leituras: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    def ler_dados(conn: Any, ctx: Any, f: Any, mes: date, vend: Any, time: Any) -> Any:
        leituras["equipe"] = (vend, time)
        return _dados_equipe()

    # "3" começou em maio de 2026 (novo, 6º mês); os outros vendem desde o início dos dados
    resumo = {(m, c): D(1500) for m in _meses(24) for c in ("1", "2", "4")}
    resumo |= {(m, "3"): D(400) for m in _meses(5)}
    monkeypatch.setattr(equipe, "ler_dados", ler_dados)
    leituras["resumo"] = resumo
    monkeypatch.setattr(
        equipe,
        "ler_carteiras",
        lambda *a: {"1": equipe.Carteira(D(1000), D(28)), "4": equipe.Carteira(D(5000), None)},
    )
    corpo = _payload(
        "equipe-comercial", equipe.montar(_ctx(), None, CARGA, {"supervisor": "marcos"})
    )

    vend, time = leituras["equipe"]
    assert sorted(vend) == ["2", "3", "4"]
    assert time == ["2", "3", "4"]
    assert [f["id"] for f in corpo["filtros"]] == ["canal", "supervisor"]
    assert corpo["destaques"][0]["rotulo"] == "Vendedores no ritmo da meta"
    ids = [c["id"] for c in corpo["cartoes"]]
    assert ids[0] == "destaques-mes"
    assert {"ranking", "mapa-produtividade", "atingimento-6-meses", "report-equipe"} <= set(ids)
    ranking = next(c for c in corpo["cartoes"] if c["id"] == "ranking")
    assert next(i["nome"] for i in ranking["itens"]) == "Elisa Rocha"
    elisa = ranking["itens"][0]
    assert elisa["detalhe"] == "Varejo · novo, 6º mês"
    report = next(c for c in corpo["cartoes"] if c["id"] == "report-equipe")
    rotulos = [c["rotulo"] for c in report["colunas"]]
    assert "Mix meta" not in rotulos
    assert "Vencido na carteira" in rotulos
    # com filtro, sem a nota dos códigos fora da tabela
    assert "nota" not in report


def test_equipe_sem_filtro_lista_os_codigos_fora(
    leituras: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(equipe, "ler_dados", lambda *a: _dados_equipe())
    monkeypatch.setattr(equipe, "ler_carteiras", lambda *a: {})
    corpo = _payload("equipe-comercial", equipe.montar(_ctx(), None, CARGA, {}))

    report = next(c for c in corpo["cartoes"] if c["id"] == "report-equipe")
    assert report["nota"] == (
        "Ficam fora da tabela os códigos de vendedor com venda no mês e sem meta: 99."
    )
    # sem o resumo por vendedor, não há rampa nem "novo"
    ids = [c["id"] for c in corpo["cartoes"]]
    assert "rampa" not in ids
    assert len(corpo["destaques"]) == 2


# ---------- Clientes ----------


def _dados_clientes() -> clientes.DadosClientes:
    d = clientes.DadosClientes()
    d.meses[OUTUBRO] = clientes.MesAtendimentos(D(30000), 10, 45)
    for k, m in enumerate(_meses(12)):
        d.meses[m] = clientes.MesAtendimentos(D(100000 + 1000 * k), 40, 200)
    dia = base.meses_antes(OUTUBRO, 6)[0]
    while dia <= ULTIMA_VENDA:
        if dia.weekday() < 6:
            d.dias[dia] = (D(3000), 2 if dia.weekday() == 0 else 1)
        dia += timedelta(days=1)
    d.faixas = {0: (20, D(5000)), 1: (30, D(30000)), 4: (5, D(60000))}
    d.produtos = [
        _produto("p1", 700, 600),
        _produto("p2", 150, 90),
        _produto("p3", 100, 70),
        _produto("p4", 30, 10),
    ]
    return d


def test_clientes_monta_o_painel(leituras: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clientes, "ler_dados", lambda *a: _dados_clientes())
    corpo = _payload("clientes", clientes.montar(_ctx(), None, CARGA, {}))

    assert corpo["filtros"] == []
    assert "aviso" not in corpo
    assert [k["rotulo"] for k in corpo["kpis"]] == [
        "Atendimentos no mês",
        "Ticket médio por atendimento",
        "Itens por atendimento",
        "Atendimentos por dia útil",
    ]
    assert corpo["kpis"][3]["contexto"].endswith("são os dias mais fortes")
    ids = [c["id"] for c in corpo["cartoes"]]
    assert ids == [
        "atendimentos-ticket",
        "dia-semana",
        "faixas-valor",
        "curva-abc",
        "cruzamento-abc",
        "itens-mais-vendem",
        "itens-atendimento",
    ]
    faixas = corpo["cartoes"][2]
    assert next(linha["valores"] for linha in faixas["linhas"]) == [36.4, 5.3]
    # 13 barras: 12 meses e outubro em projeção
    barras = corpo["cartoes"][0]["graficos"][0]["grafico"]["grupos"]
    assert len(barras) == 13
    assert barras[-1]["barras"][0]["estilo"] == "projecao"


def test_clientes_nao_tem_filtro(leituras: dict[str, Any]) -> None:
    with pytest.raises(HTTPException) as erro:
        clientes.montar(_ctx(), None, CARGA, {"canal": "varejo"})
    assert erro.value.status_code == 422

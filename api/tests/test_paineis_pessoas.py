"""PES-01 — Pessoas: regras — sem banco.

A leitura (uma ida ao banco) é trocada por linhas fictícias já no formato da consulta (uma
por mês e pessoa); movimento, turnover, tempo de casa, textos, cartões e o contrato rodam de
verdade.
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
from app.telas.moldura import Cliente, UltimaCarga
from app.telas.paineis import pessoas
from app.telas.paineis.base import Conteudo
from app.telas.paineis.contrato import (
    CartaoBarrasHorizontais,
    CartaoGraficos,
    CartaoListaDetalhe,
    CartaoTabela,
    GraficoEmpilhado,
    PainelPayload,
)
from app.telas.visao_geral.vendas import mes_menos

D = Decimal
CARGA = UltimaCarga(data_snapshot=date(2026, 10, 13), fim=None)
INICIO = date(2025, 10, 1)
SETEMBRO = date(2026, 9, 1)
MESES = [mes_menos(SETEMBRO, k) for k in range(11, -1, -1)]


def _ctx() -> ContextoConsolidado:
    sessao = Sessao(conn=object(), usuario=UsuarioToken(id_autenticacao=UUID(int=1)))  # type: ignore[arg-type]
    return ContextoConsolidado(
        sessao=sessao,
        usuario_id=UUID(int=2),
        cliente_id=UUID(int=3),
        empresa_id=None,
        empresas=(UUID(int=4),),
        papel="gestor",
        codigo_tela="PES-01",
        ve_dado_nominal=False,
    )


def _linha(
    mes: date,
    pessoa: str,
    setor: str | None,
    cargo: str = "MOTORISTA",
    salario: int = 2000,
    ajuda: int = 300,
    total: int = 3000,
) -> dict[str, Any]:
    return {
        "mes": mes,
        "pessoa": pessoa,
        "setor": setor,
        "cargo": cargo,
        "salario": D(salario),
        "ajuda": D(ajuda),
        "total": D(total),
    }


def _linhas() -> list[dict[str, Any]]:
    """Folha de out/25 a set/26:
    - A, B e G: Logística o tempo todo (já estavam no começo da folha);
    - C: Comercial de out/25 a mar/26 (sai em abril);
    - D: Comercial, entra em fev/26;
    - E: Logística em out-dez/25 e de mar/26 em diante (sumiu e voltou: não saiu);
    - F: entra em set/26, com o setor escrito "LOGISTICA" (é a mesma Logística);
    - H: um setor de uma pessoa só (Estoque), o tempo todo."""
    linhas = []
    for m in MESES:
        linhas += [
            _linha(m, "A", "Logística", salario=3000, ajuda=450, total=4500),
            _linha(m, "B", "Logística", cargo="AJUDANTE", ajuda=0, total=2500),
            _linha(m, "G", "Logística", cargo="Ajudante"),
            _linha(m, "H", "Estoque", cargo="ESTOQUISTA"),
        ]
        if m <= date(2026, 3, 1):
            linhas.append(_linha(m, "C", "Comercial", cargo="VENDEDOR"))
        if m >= date(2026, 2, 1):
            linhas.append(_linha(m, "D", "Comercial", cargo="VENDEDOR", total=5000))
        if m <= date(2025, 12, 1) or m >= date(2026, 3, 1):
            linhas.append(_linha(m, "E", "Logística"))
    linhas.append(_linha(SETEMBRO, "F", "LOGISTICA"))
    return linhas


def _faturado() -> list[dict[str, Any]]:
    # 60 mil por mês; setembro, 72 mil
    return [{"mes": m, "valor": D(72000) if m == SETEMBRO else D(60000)} for m in MESES]


@pytest.fixture
def folha() -> pessoas.Folha:
    return pessoas.folha_das_linhas(_linhas(), _faturado())


@pytest.fixture
def leitura(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    estado: dict[str, Any] = {"tabelas": [object()], "linhas": _linhas(), "faturado": _faturado()}
    monkeypatch.setattr(pessoas, "tabelas_folha", lambda conn, ctx: estado["tabelas"])
    monkeypatch.setattr(
        pessoas,
        "ler",
        lambda conn, ctx, t: pessoas.folha_das_linhas(estado["linhas"], estado["faturado"]),
    )
    monkeypatch.setattr(pessoas, "farol", lambda conn, c, indicador, ref: PADROES[indicador])
    return estado


def _montar(
    escolhido: date | None = None, pedidos: dict[str, str | None] | None = None
) -> Conteudo:
    return pessoas.montar(_ctx(), escolhido, CARGA, pedidos or {})


def _payload(c: Conteudo) -> dict[str, Any]:
    p = PainelPayload(
        tela="pessoas",
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


# ---------- regras ----------


def test_setores_que_so_mudam_na_grafia_viram_um(folha: pessoas.Folha) -> None:
    assert folha.setores["logistica"] == "Logística"
    assert folha.meses[SETEMBRO]["F"].setor == "logistica"
    # cargo em maiúsculas vira rótulo; as duas grafias de "ajudante" são o mesmo cargo
    assert folha.cargos["ajudante"] == "Ajudante"
    assert pessoas.pessoas(folha, SETEMBRO, "logistica") == 5


def test_admissao_e_desligamento_pelo_primeiro_e_o_ultimo_mes(folha: pessoas.Folha) -> None:
    mov = pessoas.movimento(folha)

    # quem já estava no começo da folha não é admissão
    assert {m: dict(c) for m, c in mov.admissoes.items()} == {
        date(2026, 2, 1): {"comercial": 1},
        SETEMBRO: {"logistica": 1},
    }
    # C trabalhou até março: a saída conta em abril, no setor do último mês; E sumiu e
    # voltou: não saiu
    assert {m: dict(c) for m, c in mov.desligamentos.items()} == {
        date(2026, 4, 1): {"comercial": 1}
    }


def test_turnover_do_mes_e_do_periodo(folha: pessoas.Folha) -> None:
    mov = pessoas.movimento(folha)
    p = pessoas.periodo(folha.meses, SETEMBRO)

    # abril: 1 saída para 6 pessoas
    assert pessoas.turnover(folha, mov, date(2026, 4, 1)) == D(1) / 6 * 100
    assert pessoas.turnover(folha, mov, date(2026, 5, 1)) == 0
    # o primeiro mês da folha não tem mês anterior: fora do movimento
    assert p.janela == MESES
    assert p.movimento == MESES[1:]
    assert p.texto == "11 meses"
    t = pessoas.turnover_periodo(folha, mov, p)
    assert (t.admissoes, t.desligamentos) == (2, 1)
    assert t.acumulado == D(1) / t.media_pessoas * 100


def test_farol_do_periodo_curto_e_proporcional() -> None:
    f = pessoas.farol_do_periodo(PADROES["turnover"], 6)
    assert (f.faixa.atencao, f.faixa.critico) == (D("7.5"), D(15))
    assert f.status(D(8)) == "atencao"


def test_tempo_de_casa_abre_a_faixa_em_que_o_comeco_da_folha_cai(folha: pessoas.Folha) -> None:
    p = pessoas.periodo(folha.meses, SETEMBRO)
    faixas = pessoas.tempo_de_casa(folha, p)

    # a folha cobre 11 meses: quem está desde out/25 tem 11 meses ou mais
    assert [(f.rotulo, f.pessoas, f.aberta) for f in faixas] == [
        ("Até 6 meses", 1, False),
        ("Mais de 6 meses", 6, True),
    ]
    # sem a faixa de 6 meses a 1 ano inteira, não dá para dizer quantos têm menos de 1 ano
    assert pessoas.com_menos_de_um_ano(faixas) is None


def test_tempo_de_casa_com_mais_de_dois_anos_de_folha() -> None:
    meses = [mes_menos(SETEMBRO, k) for k in range(30, -1, -1)]
    linhas = [_linha(m, "A", "Logística") for m in meses]
    linhas += [_linha(m, "B", "Logística") for m in meses if m >= date(2026, 1, 1)]
    folha = pessoas.folha_das_linhas(linhas)
    faixas = pessoas.tempo_de_casa(folha, pessoas.periodo(folha.meses, SETEMBRO))

    assert [(f.rotulo, f.pessoas) for f in faixas] == [
        ("Até 6 meses", 0),
        ("6 meses a 1 ano", 1),
        ("1 a 2 anos", 0),
        ("Mais de 2 anos", 1),
    ]
    assert pessoas.com_menos_de_um_ano(faixas) == 50


def test_tempo_de_casa_sem_seis_meses_de_folha_fica_vazio() -> None:
    meses = [mes_menos(SETEMBRO, k) for k in range(2, -1, -1)]
    folha = pessoas.folha_das_linhas([_linha(m, "A", "Logística") for m in meses])
    assert pessoas.tempo_de_casa(folha, pessoas.periodo(folha.meses, SETEMBRO)) == []


# ---------- montagem ----------


@pytest.mark.usefixtures("leitura")
def test_cartoes_na_ordem_e_o_contrato_valida() -> None:
    c = _montar()
    corpo = _payload(c)

    assert [x["id"] for x in corpo["cartoes"]] == [
        "mudou-na-equipe",
        "faturamento-por-colaborador",
        "colaboradores-por-setor",
        "setor-mes-a-mes",
        "cargos-salarios",
        "folha-por-setor",
        "admissoes-turnover",
        "turnover-por-setor",
        "tempo-de-casa",
        "produtividade-por-setor",
    ]
    # os pares da grade (7 + 5 e 6 + 6) dividem a fileira
    larguras = {x["id"]: x["largura"] for x in corpo["cartoes"]}
    assert (larguras["cargos-salarios"], larguras["folha-por-setor"]) == (7, 5)
    assert (larguras["turnover-por-setor"], larguras["tempo-de-casa"]) == (6, 6)
    assert corpo["filtros"] == []
    assert corpo["destaques"] == []
    assert corpo["aviso"]["tom"] == "info"
    # sem folha em outubro (o mês da carga), a tela mostra setembro e avisa
    assert corpo["aviso"]["texto"][1]["texto"] == (
        " Sem folha em outubro de 2026: os números são de setembro de 2026."
    )


@pytest.mark.usefixtures("leitura")
def test_kpis() -> None:
    kpis = {k.rotulo: k for k in _montar().kpis}

    assert list(kpis) == [
        "Colaboradores",
        "Folha com encargos",
        "Faturamento por colaborador",
        "Custo médio por pessoa",
    ]
    assert kpis["Colaboradores"].valor.valor == 7
    assert kpis["Colaboradores"].contexto == "eram 6 em outubro de 2025"
    # setembro: A 4.500, B 2.500, D 5.000 e mais 4 de 3.000
    folha = kpis["Folha com encargos"]
    assert folha.valor.valor == 24000
    assert folha.contexto.startswith("33,3% do faturamento; média de 11 meses ")
    por_pessoa = kpis["Faturamento por colaborador"]
    assert por_pessoa.valor.valor == 10286
    assert por_pessoa.valor.casas_mil == 1
    assert por_pessoa.contexto == "por mês; R$ 10,0 mil em outubro de 2025"
    assert por_pessoa.status == "saudavel"
    assert kpis["Custo médio por pessoa"].valor.valor == 3429


@pytest.mark.usefixtures("leitura")
def test_admissoes_turnover_tem_uma_visao_por_setor_com_gente_suficiente() -> None:
    c = _montar()
    cartao = _cartao(c, "admissoes-turnover")
    assert isinstance(cartao, CartaoGraficos)
    assert cartao.graficos == []
    assert cartao.rotulo_visoes == "Setor"
    assert cartao.visoes is not None
    # Comercial (média de 1 a 2 pessoas) e Estoque (1 pessoa) ficam de fora
    assert [v.id for v in cartao.visoes] == ["todos", "logistica"]
    todos = cartao.visoes[0]
    barras, linha = todos.graficos
    abril = barras.grafico.grupos[5]  # type: ignore[union-attr]
    assert abril.titulo == "abr-26"
    assert [(b.nome, b.valor) for b in abril.barras] == [("Admissões", 0), ("Desligamentos", 1)]
    assert linha.grafico.tipo == "linhas"
    assert todos.nota is not None
    assert "".join(t.texto for t in todos.nota).endswith("2 admissões e 1 desligamento no período.")

    turnover = _cartao(c, "turnover-por-setor")
    assert isinstance(turnover, CartaoBarrasHorizontais)
    assert [i.rotulo for i in turnover.itens] == ["Logística"]
    assert turnover.itens[0].sub == "0 saídas em 11 meses"


@pytest.mark.usefixtures("leitura")
def test_colaboradores_por_setor_soma_o_topo_de_cada_barra() -> None:
    cartao = _cartao(_montar(), "colaboradores-por-setor")
    grafico = cartao.graficos[0].grafico
    assert isinstance(grafico, GraficoEmpilhado)
    assert grafico.rotulos_nas_barras is True
    assert grafico.totais is not None
    assert grafico.totais[-1] == 7
    assert [s.nome for s in grafico.series] == ["Logística", "Comercial", "Estoque"]
    assert grafico.series[0].valores[-1] == 5


@pytest.mark.usefixtures("leitura")
def test_cargos_agregados_sem_nome() -> None:
    cartao = _cartao(_montar(), "cargos-salarios")
    assert isinstance(cartao, CartaoTabela)
    linhas = {ln.celulas[0].valor: ln.celulas for ln in cartao.linhas}
    assert next(iter(linhas)) == "Motorista"
    motorista = linhas["Motorista"]
    # A, E e F: salário médio (3.000 + 2.000 + 2.000) / 3; ajuda média (450 + 300 + 300) / 3
    assert motorista[1].valor.valor == 3  # type: ignore[union-attr]
    assert motorista[2].valor.valor == 2333  # type: ignore[union-attr]
    assert motorista[2].sub == "+ R$ 350 de ajuda de custo"
    assert motorista[3].valor.valor == 10500  # type: ignore[union-attr]
    assert linhas["Ajudante"][2].sub == "+ R$ 150 de ajuda de custo"


@pytest.mark.usefixtures("leitura")
def test_produtividade_so_dos_setores_com_fonte() -> None:
    cartao = _cartao(_montar(), "produtividade-por-setor")
    assert isinstance(cartao, CartaoListaDetalhe)
    assert [i.id for i in cartao.itens] == ["comercial"]
    item = cartao.itens[0]
    # uma célula a menos que as colunas: a última coluna é a tendência
    assert len(item.celulas) == len(cartao.colunas) - 1
    assert item.celulas[3].valor.valor == 72000  # type: ignore[union-attr]
    assert item.detalhe.titulo == "Comercial: faturamento por pessoa"


def test_destaques_com_os_fatos_do_mes(leitura: dict[str, Any]) -> None:
    # C sai em abril e mais duas pessoas da Logística saem em junho
    leitura["linhas"] = [
        x for x in _linhas() if not (x["pessoa"] in {"A", "G"} and x["mes"] >= date(2026, 6, 1))
    ]
    itens = _cartao(_montar(), "mudou-na-equipe").itens
    saidas = itens[0]
    assert saidas.categoria == "Saídas"
    assert saidas.titulo == "Logística perdeu 2 pessoas em junho"
    assert saidas.texto.endswith("Em 11 meses foram 2 saídas no setor.")
    assert [i.categoria for i in itens] == ["Saídas", "Rotatividade", "Produtividade"]


@pytest.mark.usefixtures("leitura")
def test_mes_escolhido_com_folha_nao_avisa() -> None:
    c = _montar(date(2026, 3, 1))
    assert c.aviso is not None
    assert len(c.aviso.texto) == 1
    assert c.kpis[0].valor.valor == 7  # A, B, C, D, E, G e H


def test_sem_folha_vem_vazio(leitura: dict[str, Any]) -> None:
    leitura["tabelas"] = []
    c = _montar()
    assert (c.kpis, c.cartoes, c.aviso) == ([], [], None)


@pytest.mark.usefixtures("leitura")
def test_mes_antes_da_folha_vem_vazio() -> None:
    assert _montar(date(2025, 1, 1)).cartoes == []


@pytest.mark.usefixtures("leitura")
def test_filtro_desconhecido_422() -> None:
    with pytest.raises(HTTPException) as erro:
        _montar(pedidos={"setor": "logistica"})
    assert erro.value.status_code == 422

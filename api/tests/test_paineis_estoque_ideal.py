"""Painel Estoque (EST-01): real contra ideal, saúde, KPIs e as listas — sem banco."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.telas.paineis.contrato import CartaoRealIdeal, CartaoSaudeEstoque, CartaoTabela
from app.telas.paineis.estoque import (
    ABACAXI,
    ABAIXO,
    ESTRELA,
    EXCESSO,
    IDEAL,
    PARADO,
    ZERADO,
    Produto,
    aviso,
    cartao_real_ideal,
    cartao_saude,
    comparar,
    faixa,
    kpis,
    montar_conteudo,
    tabela_criticos,
    tabela_preso,
)
from tests import estoque_demo

D = Decimal
DATA = date(2026, 10, 12)


def _p(
    codigo: str,
    estoque: float,
    *,
    custo_ano: float = 36500,
    familia: str = "Cortes",
    secao: str | None = "BOVINOS",
    sem_venda: int | None = 0,
    faturado: float | None = None,
) -> Produto:
    """Produto com venda por dia = custo_ano ÷ 365 (padrão: R$ 100 por dia; ideal de 30 dias =
    R$ 3.000) e o estoque a custo pedido. `sem_venda`: dias desde a última venda (None:
    nenhuma nos dados)."""
    return Produto(
        codigo=codigo,
        nome=f"Produto {codigo}",
        familia=familia,
        secao=secao,
        faturado=D(str(custo_ano * 1.5 if faturado is None else faturado)),
        custo_vendas=D(str(custo_ano)),
        estoque=D(str(estoque)),
        dias_ideais=D(30),
        ultima_venda=None if sem_venda is None else DATA - timedelta(days=sem_venda),
    )


# ---------- a regra ----------


def test_ideal_minimo_e_cobertura() -> None:
    p = _p("1", 1500)
    assert (p.venda_dia, p.ideal, p.minimo, p.cobertura) == (D(100), D(3000), D(1500), D(15))


@pytest.mark.parametrize(
    ("produto", "esperada"),
    [
        (_p("zerado", 0), ZERADO),
        (_p("abaixo", 1499), ABAIXO),
        (_p("no-minimo", 1500), IDEAL),
        (_p("ideal", 4500), IDEAL),
        (_p("excesso", 4501), EXCESSO),
        # parado vence as outras: estoque e 90 dias sem venda
        (_p("parado", 9000, sem_venda=90), PARADO),
        (_p("quase", 9000, sem_venda=89), EXCESSO),
        # sem venda em 12 meses e com estoque: parado
        (_p("sem-venda", 100, custo_ano=0, faturado=0, sem_venda=None), PARADO),
        # sem estoque e sem dias de venda conhecidos, mas vendeu no ano: zerado, não parado
        (_p("zerado-velho", 0, sem_venda=200), ZERADO),
    ],
)
def test_faixas_na_prioridade(produto: Produto, esperada: str) -> None:
    assert faixa(produto, DATA) == esperada


@pytest.mark.parametrize(
    ("real", "ideal", "status", "texto"),
    [
        (0, 3000, "critico", "zerado"),
        (1400, 3000, "critico", "abaixo do mínimo"),
        (2000, 3000, "saudavel", "−R$ 1 mil · 0,7×"),
        (3000, 3000, "saudavel", "+R$ 0 · 1,0×"),
        (4000, 3000, "atencao", "+R$ 1 mil · 1,3×"),
        (4500, 3000, "atencao", "+R$ 2 mil · 1,5×"),
        (300000, 120000, "critico", "+R$ 180 mil · 2,5×"),
        (5000, 0, "critico", "+R$ 5 mil · sem venda"),
    ],
)
def test_comparar(real: int, ideal: int, status: str, texto: str) -> None:
    assert comparar(D(real), D(ideal)) == (status, texto)


def test_aviso_com_a_data() -> None:
    assert aviso(DATA, DATA).texto[0].texto == "Retrato do estoque de hoje, 12/10, a custo. "
    assert aviso(DATA, DATA + timedelta(days=1)).texto[0].texto == (
        "Retrato do estoque de 12/10, a custo. "
    )


# ---------- KPIs ----------


def test_kpis() -> None:
    produtos = [
        _p("a", 3000),
        _p("b", 9000),  # excesso: 6.000 acima
        _p("c", 0),  # zerado
        _p("d", 1000),  # abaixo
    ]
    valor, acima, abaixo, cobertura = kpis(produtos, DATA, secao_escolhida=False)
    assert (valor.valor.valor, valor.contexto) == (D(13000), "a custo · ideal: R$ 12 mil")
    assert acima.valor.valor == D(6000)
    assert acima.contexto == "46% do estoque, em 1 produto"
    assert acima.status == "critico"
    assert (abaixo.valor.valor, abaixo.valor.sufixo) == (D(2), "produtos")
    assert (abaixo.contexto, abaixo.status) == ("1 deles zerado", "atencao")
    # 13.000 ÷ 400 por dia = 32,5 dias; ideal 30: até 1,25× é saudável
    assert (cobertura.valor.valor, cobertura.valor.unidade) == (D("32.5"), "dias")
    assert (cobertura.contexto, cobertura.status) == ("ideal da empresa: 30 dias", "saudavel")


def test_kpis_sem_excesso_nem_falta_e_na_secao() -> None:
    _, acima, abaixo, cobertura = kpis([_p("a", 3000)], DATA, secao_escolhida=True)
    assert (acima.contexto, acima.status) == ("nenhum produto acima de 1,5× o ideal", "saudavel")
    assert (abaixo.contexto, abaixo.status) == ("nenhum zerado", "saudavel")
    assert cobertura.contexto == "ideal da seção: 30 dias"


def test_acima_do_ideal_atencao_entre_10_e_20_por_cento() -> None:
    # 1.600 acima de um estoque de 4.600 + 9.000 = 13.600 → 11,8%
    produtos = [_p("a", 4600, custo_ano=36500 * 1.0), _p("b", 9000, custo_ano=36500 * 2.4)]
    acima = kpis(produtos, DATA, secao_escolhida=False)[1]
    assert acima.status == "atencao"


# ---------- real contra ideal ----------


def test_real_ideal_secao_familia_e_outros() -> None:
    bov = [
        _p("1", 9000, familia="Cortes"),
        _p("2", 3000, familia="Cortes"),
        _p("3", 1000, familia="Moídas"),
        _p("4", 6000, familia="Moídas"),  # fora dos principais
    ]
    aves = [_p("5", 3000, secao="AVES", familia="Frango")]
    principais = [bov[0], bov[1], bov[2], aves[0]]
    cartao = cartao_real_ideal([*bov, *aves], principais, "aves")
    assert isinstance(cartao, CartaoRealIdeal)
    assert (cartao.rotulo_raiz, cartao.aberto) == ("Todas as seções", ["aves"])
    # do maior desvio para o menor: Bovinos (+7 mil), Aves (0)
    bovinos, frango = cartao.itens
    assert (bovinos.id, bovinos.nome, bovinos.sub) == ("bovinos", "Bovinos", "ideal de 30 dias")
    assert (bovinos.real, bovinos.ideal) == (D(19000), D(12000))
    assert (bovinos.diferenca, bovinos.status) == ("+R$ 7 mil · 1,6×", "critico")
    assert bovinos.subtitulo_aberto == (
        "Famílias da seção Bovinos, do maior desvio para o menor. Clique para abrir os produtos."
    )
    assert bovinos.itens is not None
    cortes, moidas, outros = bovinos.itens
    assert (cortes.id, cortes.sub, cortes.diferenca) == (
        "familia-cortes",
        "2 produtos",
        "+R$ 6 mil · 2,0×",
    )
    assert (moidas.sub, moidas.diferenca, moidas.status) == (
        "1 produto",
        "abaixo do mínimo",
        "critico",
    )
    assert (outros.nome, outros.sub, outros.outros, outros.itens) == (
        "Outros produtos da seção",
        "fora dos principais",
        True,
        None,
    )
    assert cortes.itens is not None
    assert [p.id for p in cortes.itens] == ["1", "2"]
    assert cortes.itens[0].sub == "cobertura 90,0 dias · ideal 30"
    assert cortes.itens[0].itens is None
    # sem fora dos principais: sem a linha "Outros"
    assert frango.itens is not None
    assert [f.nome for f in frango.itens] == ["Frango"]


def test_real_ideal_produto_sem_estoque_e_secao_que_nao_existe() -> None:
    cartao = cartao_real_ideal([_p("1", 0)], [_p("1", 0)], "outra")
    assert cartao.aberto is None
    familia = cartao.itens[0].itens[0]  # type: ignore[index]
    assert familia.itens[0].sub == "sem estoque · ideal 30 dias"  # type: ignore[index]


# ---------- saúde ----------


def test_saude_faixas_e_barras() -> None:
    produtos = [
        _p("z", 0),
        _p("a", 1000),
        _p("i", 3000),
        _p("e", 9000),
        _p("p", 500, sem_venda=120),
    ]
    cartao = cartao_saude(produtos, DATA)
    assert isinstance(cartao, CartaoSaudeEstoque)
    valor, quantos = cartao.barras
    assert valor.total.valor == D(13500)  # type: ignore[union-attr]
    # sem o zerado, que não tem valor
    assert [(p.faixa, p.valor) for p in valor.partes] == [
        ("abaixo", D(1000)),
        ("ideal", D(3000)),
        ("excesso", D(9000)),
        ("parado", D(500)),
    ]
    assert [p.faixa for p in quantos.partes] == ["zerado", "abaixo", "ideal", "excesso", "parado"]
    assert [g.rotulo for g in cartao.grupos] == ["Falta", "No ponto", "Sobra"]
    zerado, abaixo = cartao.grupos[0].faixas
    assert (zerado.estilo, zerado.valor) == ("critico", None)
    assert (abaixo.estilo, abaixo.produtos.sufixo) == ("critico-claro", "produto")  # type: ignore[union-attr]
    assert [f.estilo for f in cartao.grupos[2].faixas] == ["atencao", "atencao-escura"]


# ---------- as listas ----------


def test_criticos_zerados_primeiro_e_o_rodape() -> None:
    a, b, c = _p("a", 1400), _p("b", 0), _p("c", 600)
    fora = [_p("f1", 0), _p("f2", 100)]
    codigos = {"a": ESTRELA, "b": ABACAXI, "c": ESTRELA}
    tabela = tabela_criticos([a, b, c, *fora, _p("ok", 3000)], codigos, None, DATA)
    assert isinstance(tabela, CartaoTabela)
    assert [linha.celulas[0].valor for linha in tabela.linhas] == [
        "Produto b",
        "Produto c",
        "Produto a",
    ]
    zerado = tabela.linhas[0].celulas
    assert zerado[1].quadrante is not None
    assert (zerado[1].quadrante.id, zerado[1].ordem) == ("abacaxi", 3)
    assert zerado[4].sub == "mínimo 15 · ideal 30"
    assert (zerado[6].pill.rotulo, zerado[6].ordem) == ("Zerado", 0)  # type: ignore[union-attr]
    assert zerado[6].link is not None
    assert zerado[6].link.tela == "compras"
    assert (zerado[5].valor, zerado[5].tom) == ("—", "apagado")
    assert [t.texto for t in tabela.rodape or []] == [
        "Mais 2 produtos abaixo do mínimo fora dos principais ",
        "(1 zerado).",
    ]
    assert all(c.ordenavel for c in tabela.colunas)
    # filtro de classe
    so_estrela = tabela_criticos([a, b, c], codigos, ESTRELA, DATA)
    assert len(so_estrela.linhas) == 2
    assert so_estrela.rodape is None


def test_dinheiro_preso_excesso_e_parados() -> None:
    excesso = _p("e", 9000)  # 6.000 preso
    parado = _p("p", 4000, sem_venda=100)  # 4.000 preso, fora dos principais
    parado_velho = _p("v", 2000, custo_ano=0, faturado=0, sem_venda=None)
    fora = _p("f", 6000)  # excesso fora dos principais: só no rodapé
    recorte = [excesso, parado, parado_velho, fora]
    codigos = {"e": ESTRELA}

    tabela = tabela_preso(recorte, codigos, None, "tudo", DATA)
    assert tabela.limite == 10
    linhas = [linha.celulas for linha in tabela.linhas]
    assert [c[0].valor for c in linhas] == ["Produto e", "Produto p", "Produto v"]
    assert [c[2].valor.valor for c in linhas] == [D(6000), D(4000), D(2000)]  # type: ignore[union-attr]
    assert [(c[2].barra.proporcao, c[2].barra.estilo) for c in linhas] == [  # type: ignore[union-attr]
        (1.0, "atencao"),
        (0.667, "atencao-escura"),
        (0.333, "atencao-escura"),
    ]
    # o parado fora dos principais é Abacaxi, com a ação de liquidar
    assert linhas[1][1].quadrante.id == ABACAXI  # type: ignore[union-attr]
    assert linhas[1][0].sub == "fora dos principais · Bovinos"
    assert linhas[1][5].valor == "Liquidar: promoção com os vendedores"
    assert linhas[0][5].valor == "Segurar a compra"
    # cobertura e sem venda
    assert (linhas[0][3].valor.valor, linhas[0][3].sub) == (D(90), "ideal 30")  # type: ignore[union-attr]
    assert (linhas[1][3].valor, linhas[1][3].ordem) == ("—", 9999)
    assert (linhas[0][4].valor, linhas[0][4].tom) == ("vende", "apagado")
    assert linhas[1][4].valor.valor == D(100)  # type: ignore[union-attr]
    assert (linhas[2][4].valor, linhas[2][4].ordem) == ("mais de 1 ano", 9999)
    assert [t.texto for t in tabela.rodape or []] == [
        "3 produtos somam ",
        "R$ 12 mil",
        " presos. Mais 1 produto com excesso fora dos principais: R$ 3 mil.",
    ]
    assert tabela.rodape is not None
    assert tabela.rodape[1].destaque

    so_parados = tabela_preso(recorte, codigos, None, "parados", DATA)
    assert len(so_parados.linhas) == 2
    assert [t.texto for t in so_parados.rodape or []][-1] == " presos."
    so_excesso = tabela_preso(recorte, codigos, None, "excesso", DATA)
    assert len(so_excesso.linhas) == 1
    # classe: o parado fora dos principais só aparece com Abacaxi
    assert len(tabela_preso(recorte, codigos, ESTRELA, "tudo", DATA).linhas) == 1
    vazio = tabela_preso([_p("ok", 3000)], {}, None, "tudo", DATA)
    assert (vazio.linhas, vazio.rodape, vazio.vazio) == ([], None, "Nada preso neste recorte.")


# ---------- a montagem ----------


def test_montar_conteudo_filtra_pela_secao() -> None:
    carteira = estoque_demo.produtos()
    secoes = [p.secao for p in carteira if p.secao]
    tudo = montar_conteudo(carteira, secoes, {}, DATA, DATA)
    aves = montar_conteudo(carteira, secoes, {"secao": "aves"}, DATA, DATA)
    assert tudo.kpis[0].valor.valor > aves.kpis[0].valor.valor
    real_ideal = aves.cartoes[2]
    assert isinstance(real_ideal, CartaoRealIdeal)
    # o real contra ideal lista todas as seções, aberto na escolhida
    assert (len(real_ideal.itens), real_ideal.aberto) == (4, ["aves"])
    saude = aves.cartoes[3]
    assert isinstance(saude, CartaoSaudeEstoque)
    assert saude.barras[1].total.valor < tudo.cartoes[3].barras[1].total.valor  # type: ignore[union-attr, attr-defined]


@pytest.mark.parametrize("pedidos", [{"lista": "outra"}, {"classe": "x"}, {"secao": "x"}])
def test_montar_conteudo_filtro_invalido(pedidos: dict[str, str]) -> None:
    with pytest.raises(HTTPException) as exc:
        montar_conteudo(estoque_demo.produtos(), ["BOVINOS"], pedidos, DATA, DATA)
    assert exc.value.status_code == 422


def test_montar_conteudo_sem_produto() -> None:
    conteudo = montar_conteudo([_p("x", 0, custo_ano=0, faturado=0)], [], {}, DATA, DATA)
    assert (conteudo.cartoes, conteudo.kpis, conteudo.aviso) == ([], [], None)
    assert [f.id for f in conteudo.filtros] == ["classe", "lista"]


def test_demo_tem_todas_as_partes() -> None:
    p = estoque_demo.payload()
    assert [c.id for c in p.cartoes] == [
        "matriz-bcg",
        "acoes-bcg",
        "real-ideal",
        "saude-estoque",
        "criticos",
        "excesso-parados",
    ]
    assert all(c.explicacao for c in p.cartoes)
    parados = estoque_demo.payload(lista="parados").cartoes[5]
    assert isinstance(parados, CartaoTabela)
    assert len(parados.linhas) == len(estoque_demo.PARADOS)

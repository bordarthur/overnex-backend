"""Painel Estoque (EST-01): a regra da Matriz BCG e a rota — sem banco."""

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.db import Sessao, get_sessao
from app.main import create_app
from app.seguranca.auth import UsuarioToken
from app.telas import preleitura
from app.telas.moldura import Cliente, UltimaCarga
from app.telas.paineis import estoque
from app.telas.paineis import router as paineis
from app.telas.paineis.contrato import CartaoAcoesBcg, CartaoBcg, PainelPayload
from app.telas.paineis.estoque import (
    ABACAXI,
    ESTRELA,
    INTERROGACAO,
    VACA,
    Familia,
    Medianas,
    Produto,
    dias_ideais,
    frescor,
    medianas,
    montar_bcg,
    principais,
    quadrante,
    um_ano_antes,
)
from app.telas.preleitura import Inicio
from tests import estoque_demo

D = Decimal


def _p(
    codigo: str,
    faturado: int,
    margem: int,
    giro: float | None,
    *,
    familia: str = "Cortes",
    secao: str | None = "BOVINOS",
    dias: int = 30,
) -> Produto:
    """Produto com a margem e o giro pedidos (giro None = sem estoque hoje)."""
    fat = D(faturado)
    custo = fat * (100 - margem) / 100
    return Produto(
        codigo=codigo,
        nome=f"Produto {codigo}",
        familia=familia,
        secao=secao,
        faturado=fat,
        custo_vendas=custo,
        estoque=D(0) if giro is None else custo / 12 / D(str(giro)),
        dias_ideais=D(dias),
    )


# ---------- a regra ----------


def test_margem_giro_e_cobertura() -> None:
    p = Produto("1", "P", "F", None, D(1200), D(840), D(35), D(30))
    assert p.margem == 30
    # 840 ÷ 12 = 70 por mês; 70 ÷ 35 = 2 vezes por mês
    assert p.giro == 2
    # 840 ÷ 365 por dia: 35 cobrem 15,2 dias; mínimo = metade de 30 = 15
    assert p.cobertura is not None
    assert round(p.cobertura, 1) == D("15.2")
    assert not p.abaixo_do_minimo
    assert Produto("2", "P", "F", None, D(1200), D(840), D(30), D(30)).abaixo_do_minimo


def test_sem_estoque_e_giro_none_e_abaixo_do_minimo() -> None:
    p = _p("1", 1000, 30, None)
    assert p.giro is None
    assert p.cobertura == 0
    assert p.abaixo_do_minimo


def test_sem_custo_de_venda_nunca_fica_abaixo_do_minimo() -> None:
    p = Produto("1", "P", "F", None, D(1000), D(0), D(0), D(30))
    assert p.cobertura is None
    assert not p.abaixo_do_minimo


def test_principais_e_a_curva_a_com_o_produto_que_passa_dos_80() -> None:
    # 50 + 30 = 80 de 100: o segundo fecha os 80%, e ninguém mais entra
    lista = [_p("a", 50, 30, 2), _p("b", 30, 30, 2), _p("c", 15, 30, 2), _p("d", 5, 30, 2)]
    assert [p.codigo for p in principais(lista)] == ["a", "b"]
    # 50 + 20 = 70 < 80: o terceiro passa dos 80% e entra
    lista = [_p("a", 50, 30, 2), _p("b", 20, 30, 2), _p("c", 20, 30, 2), _p("d", 10, 30, 2)]
    assert [p.codigo for p in principais(lista)] == ["a", "b", "c"]


def test_principais_no_maximo_40_e_sem_quem_nao_vendeu() -> None:
    lista = [_p(f"{i:03}", 100, 30, 2) for i in range(60)] + [_p("zero", 0, 30, 2)]
    escolhidos = principais(lista)
    assert len(escolhidos) == 40
    assert "zero" not in {p.codigo for p in escolhidos}


def test_mediana_conta_o_sem_estoque_como_o_maior_giro() -> None:
    lista = [_p("a", 100, 10, 1), _p("b", 100, 20, 2), _p("c", 100, 30, None)]
    m = medianas(lista)
    assert (m.giro, m.margem) == (2, 20)
    # se o sem estoque cai na mediana, vale o maior giro que existe
    lista = [_p("a", 100, 10, 1), _p("b", 100, 20, None), _p("c", 100, 30, None)]
    assert medianas(lista).giro == 1


@pytest.mark.parametrize(
    ("giro", "margem", "esperado"),
    [
        (D(5), D(40), ESTRELA),
        (D(4), D(30), ESTRELA),  # empate vai para cima e para a direita
        (D(5), D(20), VACA),
        (D(3), D(40), INTERROGACAO),
        (D(3), D(20), ABACAXI),
        (None, D(20), VACA),  # sem estoque: giro alto
    ],
)
def test_quadrante(giro: Decimal | None, margem: Decimal, esperado: str) -> None:
    assert quadrante(giro, margem, Medianas(giro=D(4), margem=D(30))) == esperado


def test_familia_soma_e_aplica_a_mesma_formula() -> None:
    a = _p("a", 1000, 40, 2)
    b = _p("b", 3000, 20, 4)
    f = Familia("Cortes", (a, b))
    assert f.faturado == 4000
    # (4000 − 600 − 2400) ÷ 4000
    assert f.margem == 25
    # custo por mês (3000 ÷ 12 = 250) ÷ estoque (25 + 50 = 75)
    assert f.giro is not None
    assert round(f.giro, 4) == round(D(250) / D(75), 4)


def _bcg(
    lista: list[Produto], secoes: tuple[str, ...] | None = None, classe: Any = None
) -> tuple[CartaoBcg, CartaoAcoesBcg]:
    cartoes = montar_bcg(lista, secoes, classe)
    assert cartoes is not None
    return cartoes


CARTEIRA = [
    _p("1", 270, 40, 6, familia="Nobres", secao="BOVINOS", dias=12),  # estrela, abaixo do mín.
    _p("2", 260, 40, 2, familia="Nobres", secao="BOVINOS"),  # interrogação
    _p("3", 250, 20, 6, familia="Aves", secao="AVES"),  # vaca
    _p("4", 220, 20, 1, familia="Aves", secao="AVES"),  # abacaxi
]


def test_cartao_bcg_familias_com_os_produtos_e_os_cortes() -> None:
    bcg, _ = _bcg(CARTEIRA)
    assert (bcg.tipo, bcg.largura, bcg.id) == ("bcg", 8, "matriz-bcg")
    assert bcg.corte_x.rotulo == "mediana 4,0×"
    assert bcg.corte_y.rotulo == "mediana 30%"
    assert [k.id for k in bcg.quadrantes] == [ESTRELA, VACA, INTERROGACAO, ABACAXI]
    assert bcg.trilha == "2 famílias · 4 principais produtos · clique numa bolha para abrir"
    nobres, aves = bcg.pontos
    assert (nobres.nome, nobres.trilha) == ("Nobres", "Nobres · 2 produtos")
    assert nobres.subtitulo_aberto is not None
    assert nobres.produtos is not None
    assert [p.quadrante for p in nobres.produtos] == [ESTRELA, INTERROGACAO]
    assert aves.produtos is not None
    assert [p.quadrante for p in aves.produtos] == [VACA, ABACAXI]
    assert all(p.produtos is None and p.trilha is None for p in nobres.produtos)
    # a dica, pronta
    rotulos = [i.rotulo for i in nobres.produtos[0].dica]
    assert rotulos == [
        "Seção",
        "Família",
        "Quadrante",
        "Giro",
        "Margem",
        "Faturamento 12m",
        "Estoque hoje",
    ]
    assert nobres.produtos[0].dica[3].valor == "6,0×"
    assert [i.rotulo for i in nobres.dica][1] == "Produtos"


def test_filtros_apagam_sem_mudar_as_medianas_nem_tirar_pontos() -> None:
    todos, _ = _bcg(CARTEIRA)
    aves, _ = _bcg(CARTEIRA, secoes=("AVES",))
    assert (aves.corte_x, aves.corte_y) == (todos.corte_x, todos.corte_y)
    assert [p.apagado for p in aves.pontos] == [True, False]
    estrelas, _ = _bcg(CARTEIRA, classe=ESTRELA)
    assert len(estrelas.pontos) == 2
    nobres = estrelas.pontos[0]
    assert nobres.produtos is not None
    assert [p.apagado for p in nobres.produtos] == [False, True]


def test_sem_estoque_fica_na_borda_direita_com_nota() -> None:
    lista = [*CARTEIRA, _p("5", 350, 35, None, familia="Nobres")]
    bcg, _ = _bcg(lista)
    produto = next(p for f in bcg.pontos for p in (f.produtos or []) if p.id == "5")
    maior = max(p.x for f in bcg.pontos for p in (f.produtos or []) if p.id != "5")
    assert produto.x > maior
    assert produto.dica[3].valor == "sem estoque hoje"
    assert bcg.nota == "1 produto sem estoque hoje fica na borda direita, como giro alto."


def test_acoes_respeitam_a_secao_e_ignoram_a_classe() -> None:
    _, acoes = _bcg(CARTEIRA, classe=VACA)
    assert (acoes.tipo, acoes.largura) == ("acoes-bcg", 4)
    por_quad = {i.quadrante: i for i in acoes.itens}
    assert list(por_quad) == [ESTRELA, VACA, INTERROGACAO, ABACAXI]
    assert [i.selecionado for i in acoes.itens] == [False, True, False, False]
    assert por_quad[ESTRELA].acao == "Repor e não deixar faltar."
    # o produto 1 (Estrela) cobre 30,4 ÷ 6 ≈ 5 dias, abaixo da metade de 12
    assert por_quad[ESTRELA].nota == "1 abaixo do mínimo"
    assert por_quad[ESTRELA].numeros[2].status == "critico"
    # vaca: 250 de 1000 faturados (os quatro somam 1000; 27 + 26 + 25 = 78%: todos são A)
    assert por_quad[VACA].nota == "25% do faturamento"
    assert por_quad[INTERROGACAO].nota == "26% do faturamento"
    assert por_quad[ABACAXI].nota.endswith("% do estoque")

    _, so_aves = _bcg(CARTEIRA, secoes=("AVES",))
    por_quad = {i.quadrante: i for i in so_aves.itens}
    assert por_quad[ESTRELA].numeros[0].valor.valor == 0  # type: ignore[union-attr]
    assert por_quad[ESTRELA].nota == "nenhum abaixo do mínimo"
    assert por_quad[ESTRELA].numeros[2].status is None
    # na seção Aves, a vaca faz 250 de 470
    assert por_quad[VACA].nota == "53% do faturamento"


def test_alerta_do_abacaxi_acima_de_20_por_cento_do_estoque() -> None:
    _, acoes = _bcg(CARTEIRA, secoes=("AVES",))
    abacaxi = next(i for i in acoes.itens if i.quadrante == ABACAXI)
    # Aves: estoque do abacaxi (176 ÷ 12 ÷ 1) contra o da vaca (200 ÷ 12 ÷ 6)
    assert abacaxi.nota == "84% do estoque"
    assert abacaxi.numeros[2].status == "critico"


def test_sem_venda_nenhum_cartao() -> None:
    assert montar_bcg([_p("1", 0, 30, 2)], None, None) is None


def test_dias_ideais_do_parametro() -> None:
    assert dias_ideais(None) == (D(30), {})
    assert dias_ideais({"padrao": 12, "secoes": {"Aves ": 8}}) == (D(12), {"aves": D(8)})
    # inválido: o padrão
    assert dias_ideais({"padrao": "muitos"}) == (D(30), {})


def test_frescor_e_janela() -> None:
    fim = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
    carga = UltimaCarga(date(2026, 10, 12), fim)
    assert frescor(carga, date(2026, 10, 12)) == "Estoque de hoje, 12/10, atualizado às 9h"
    tarde = UltimaCarga(date(2026, 10, 11), datetime(2026, 10, 11, 12, 14, tzinfo=UTC))
    assert frescor(tarde, date(2026, 10, 12)) == "Estoque de 11/10, atualizado às 9h14"
    assert um_ano_antes(date(2028, 2, 29)) == date(2027, 2, 28)
    assert um_ano_antes(date(2026, 10, 12)) == date(2025, 10, 12)


def test_demo_bate_com_a_regra() -> None:
    # demo/estoque.json sai de tests/estoque_demo.py pela regra de verdade
    original = json.loads((paineis.DEMO / "estoque.json").read_text("utf-8"))
    assert estoque_demo.como_json(estoque_demo.payload()) == original
    bcg = original["cartoes"][0]
    assert bcg["trilha"] == "15 famílias · 38 principais produtos · clique numa bolha para abrir"
    assert (bcg["corteX"]["rotulo"], bcg["corteY"]["rotulo"]) == ("mediana 4,2×", "mediana 33%")


# ---------- a rota ----------

USUARIO = UUID("d1000000-0000-4000-8000-00000000000a")
CLIENTE = UUID("c1000000-0000-4000-8000-00000000000a")
EMPRESA = UUID("e1000000-0000-4000-8000-0000000000a1")
# unidade de outro setor do mesmo cliente
LOJA = UUID("e1000000-0000-4000-8000-0000000000a2")


class _Resultado:
    def __init__(self, linha: dict[str, Any]) -> None:
        self.linha = linha

    def fetchone(self) -> dict[str, Any]:
        return self.linha


class _Conexao:
    def __init__(self, unidades: dict[UUID, str | None]) -> None:
        # unidade liberada → setor dela
        self.unidades = unidades

    def execute(self, consulta: str, params: Any = None) -> _Resultado:
        assert "seg.contexto_acesso" in consulta
        return _Resultado(
            {
                "usuario_id": USUARIO,
                "ativo": True,
                "vinculos": [{"cliente_id": str(CLIENTE), "papel": "gestor"}],
                "perfis_tela": {str(CLIENTE): False},
                "empresas": [
                    {"cliente_id": str(CLIENTE), "empresa_id": str(u), "setor": s}
                    for u, s in self.unidades.items()
                ],
                "perfil": "cliente_principal",
                "cliente_id": CLIENTE,
                "setores": sorted({s for s in self.unidades.values() if s}),
            }
        )


@pytest.fixture
def estado(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    e: dict[str, Any] = {
        "unidades": {EMPRESA: "distribuidora"},
        "carga": UltimaCarga(date(2026, 10, 12), datetime(2026, 10, 12, 12, tzinfo=UTC)),
        "lidos": (CARTEIRA, ["BOVINOS", "AVES"]),
    }

    def preler(sessao: Any, cliente_id: Any, *, com_meses: bool = False) -> Inicio:
        return Inicio(
            carga=e["carga"],
            cliente=Cliente(nome="Alfa", sigla="AL", logo_url=None),
            meses=None,
            versao="",
        )

    monkeypatch.setattr(paineis, "preler", preler)
    for modulo in (paineis, preleitura):
        monkeypatch.setattr(modulo, "consultas_adiantadas", lambda conn, **_: {})

    def ler_produtos(ctx: Any, data: Any) -> Any:
        e["somadas"] = (ctx.unidades, ctx.empresas)
        return e["lidos"]

    monkeypatch.setattr(estoque, "ler_produtos", ler_produtos)
    return e


@pytest.fixture
def client(estado: dict[str, Any]) -> Iterator[TestClient]:
    app = create_app()
    settings = get_settings().model_copy(update={"visao_geral_cache_segundos": 0})
    app.dependency_overrides[get_sessao] = lambda: Sessao(
        conn=_Conexao(estado["unidades"]),  # type: ignore[arg-type]
        usuario=UsuarioToken(id_autenticacao=UUID(int=1)),
    )
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def test_rota_monta_a_bcg_com_os_filtros_e_o_frescor(client: TestClient) -> None:
    r = client.get("/api/telas/estoque", params={"secao": "aves", "classe": "vaca-leiteira"})

    assert r.status_code == 200
    corpo = PainelPayload.model_validate(r.json())
    assert corpo.tela == "estoque"
    assert corpo.frescor is not None
    assert corpo.frescor.startswith("Estoque de")
    assert [f.id for f in corpo.filtros] == ["secao", "classe", "lista"]
    assert [o.id for o in corpo.filtros[0].opcoes] == ["todas", "aves", "bovinos"]
    # a chave do dinheiro preso vai no cabeçalho do cartão
    assert corpo.filtros[2].cartao == "excesso-parados"
    assert [c.tipo for c in corpo.cartoes] == [
        "bcg",
        "acoes-bcg",
        "real-ideal",
        "saude-estoque",
        "tabela",
        "tabela",
    ]
    assert [c.id for c in corpo.cartoes][2:] == [
        "real-ideal",
        "saude-estoque",
        "criticos",
        "excesso-parados",
    ]
    # com a seção, o real contra ideal vem aberto nela
    assert r.json()["cartoes"][2]["aberto"] == ["aves"]
    assert [k.rotulo for k in corpo.kpis] == [
        "Valor em estoque",
        "Acima do ideal",
        "Abaixo do mínimo",
        "Cobertura média",
    ]
    assert corpo.aviso is not None
    # o "i" vem pronto
    assert all(c.explicacao for c in corpo.cartoes)
    # o JSON traz apagado e rotular também quando falsos
    ponto = r.json()["cartoes"][0]["pontos"][0]
    assert {"apagado", "rotular", "produtos", "trilha", "subtituloAberto"} <= ponto.keys()
    # painel de um setor: sempre com o escopo
    assert r.json()["escopo"] == {
        "setores": ["distribuidora"],
        "rotulo": "Este painel soma só a Distribuidora",
    }


def test_soma_so_as_unidades_do_setor(client: TestClient, estado: dict[str, Any]) -> None:
    estado["unidades"] = {EMPRESA: "distribuidora", LOJA: "varejo"}
    r = client.get("/api/telas/estoque", params={"unidades": f"{EMPRESA},{LOJA}"})
    assert r.status_code == 200
    assert estado["somadas"] == ((EMPRESA,), (EMPRESA,))
    assert r.json()["escopo"]["setores"] == ["distribuidora"]


def test_sem_unidade_pedida_do_setor_so_o_aviso(client: TestClient, estado: dict[str, Any]) -> None:
    estado["unidades"] = {EMPRESA: "distribuidora", LOJA: "varejo"}
    r = client.get("/api/telas/estoque", params={"unidades": str(LOJA)})
    assert r.status_code == 200
    corpo = r.json()
    assert "somadas" not in estado  # nem chega a ler o estoque
    assert (corpo["cartoes"], corpo["kpis"], corpo["filtros"]) == ([], [], [])
    assert corpo["escopo"] == {
        "setores": ["distribuidora"],
        "rotulo": "Este painel soma só a Distribuidora",
    }
    assert corpo["aviso"] == {
        "tom": "info",
        "texto": [
            {
                "texto": "Estoque é um painel da Distribuidora. Volta quando ela estiver "
                "marcada em Negócios."
            }
        ],
    }


@pytest.mark.parametrize(
    "params", [{"classe": "outra"}, {"secao": "nao-existe"}, {"lista": "todos"}]
)
def test_filtro_invalido_422(client: TestClient, params: dict[str, str]) -> None:
    r = client.get("/api/telas/estoque", params=params)
    assert (r.status_code, r.json()) == (422, {"detail": "Filtro inválido."})


def test_sem_fonte_de_estoque_so_os_filtros(client: TestClient, estado: dict[str, Any]) -> None:
    estado["lidos"] = None
    corpo = client.get("/api/telas/estoque").json()
    assert corpo["cartoes"] == []
    assert [f["id"] for f in corpo["filtros"]] == ["classe"]


@pytest.mark.parametrize("setor", ["industria", None])
def test_sem_unidade_liberada_do_setor_403(
    client: TestClient, estado: dict[str, Any], setor: str | None
) -> None:
    estado["unidades"] = {EMPRESA: setor}
    r = client.get("/api/telas/estoque")
    assert (r.status_code, r.json()) == (403, {"detail": "Acesso negado."})

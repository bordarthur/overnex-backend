"""Rotas dos painéis por cartões — sem banco.

A sessão é falsa: a checagem de acesso lê os fatos de uma conexão que só responde a
seg.contexto_acesso; moldura (cliente, carga, meses) e o conteúdo das telas são trocados.
"""

from collections.abc import Iterator, Mapping
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.db import Sessao, get_sessao
from app.main import create_app
from app.seguranca.acesso import ContextoConsolidado
from app.seguranca.auth import UsuarioToken
from app.telas import preleitura
from app.telas.moldura import Cliente, MesComDados, UltimaCarga
from app.telas.paineis import router as paineis
from app.telas.paineis.base import Conteudo, escolher, opcoes
from app.telas.paineis.contrato import ItemValor, TabelaNomes
from app.telas.paineis.explicacoes import EXPLICACOES
from app.telas.paineis.pessoas import COLUNAS_NOMES
from app.telas.preleitura import Inicio
from app.telas.visao_geral.contrato import Valor

USUARIO = UUID("d1000000-0000-4000-8000-00000000000a")
CLIENTE = UUID("c1000000-0000-4000-8000-00000000000a")
EMPRESA = UUID("e1000000-0000-4000-8000-0000000000a1")
CARGA = UltimaCarga(date(2026, 10, 12), datetime(2026, 10, 12, 12, tzinfo=UTC))


class Resultado:
    def __init__(self, linha: dict[str, Any]) -> None:
        self.linha = linha

    def fetchone(self) -> dict[str, Any]:
        return self.linha


class ConexaoFalsa:
    """Só responde à consulta dos fatos de acesso (seg.contexto_acesso)."""

    def __init__(self, perfis_tela: dict[str, bool]) -> None:
        self.perfis_tela = perfis_tela

    def execute(self, consulta: str, params: Any = None) -> Resultado:
        assert "seg.contexto_acesso" in consulta
        return Resultado(
            {
                "usuario_id": USUARIO,
                "ativo": True,
                "vinculos": [{"cliente_id": str(CLIENTE), "papel": "gestor"}],
                "perfis_tela": self.perfis_tela,
                "empresas": [{"cliente_id": str(CLIENTE), "empresa_id": str(EMPRESA)}],
                "perfil": "cliente_principal",
                "cliente_id": CLIENTE,
            }
        )


@pytest.fixture
def estado(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Moldura e conteúdo trocados; guarda o que cada tela recebeu."""
    e: dict[str, Any] = {"carga": CARGA, "perfis": {str(CLIENTE): True}, "chamadas": []}

    def preler(sessao: Any, cliente_id: Any, *, com_meses: bool = False) -> Inicio:
        return Inicio(
            carga=e["carga"],
            cliente=Cliente(nome="Alfa", sigla="AL", logo_url=None),
            meses=[MesComDados(date(2026, 10, 1), date(2026, 10, 12))] if com_meses else None,
            versao=e.get("versao", ""),
        )

    monkeypatch.setattr(paineis, "preler", preler)
    # nada vai junto com a checagem de acesso: a conexão falsa só responde a ela
    for modulo in (paineis, preleitura):
        monkeypatch.setattr(modulo, "consultas_adiantadas", lambda conn, **_: {})

    def montador(
        ctx: ContextoConsolidado,
        mes: date | None,
        carga: UltimaCarga,
        filtros: Mapping[str, str | None],
        a_decidir: bool,
    ) -> Conteudo:
        e["chamadas"].append((ctx.codigo_tela, ctx.empresas, mes, dict(filtros)))
        e["a_decidir"] = a_decidir
        canal = opcoes("canal", "Canal", ["Varejo", "Atacado"])
        escolher(canal, filtros.get("canal"))
        return Conteudo(
            filtros=[canal.filtro],
            destaques=[ItemValor(rotulo="Vendido", valor=Valor(valor=1, unidade="brl"))],
        )

    for tela in paineis.MONTADORES:
        monkeypatch.setitem(paineis.MONTADORES, tela, montador)
    return e


@pytest.fixture
def client(estado: dict[str, Any]) -> Iterator[TestClient]:
    app = create_app()
    settings = get_settings().model_copy(update={"visao_geral_cache_segundos": 0})

    def sessao() -> Sessao:
        s = Sessao(
            conn=ConexaoFalsa(estado["perfis"]),  # type: ignore[arg-type]
            usuario=UsuarioToken(id_autenticacao=UUID(int=1)),
        )
        # as linhas de seg.log_acesso de cada requisição (aqui ninguém grava)
        estado.setdefault("sessoes", []).append(s)
        return s

    app.dependency_overrides[get_sessao] = sessao
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


@pytest.mark.parametrize(
    "tela",
    [
        "vendas",
        "equipe-comercial",
        "clientes",
        "resultado",
        "receber-pagar",
        "fluxo-caixa",
        "pessoas",
    ],
)
def test_painel_200_com_a_moldura(client: TestClient, estado: dict[str, Any], tela: str) -> None:
    r = client.get(f"/api/telas/{tela}")

    assert r.status_code == 200
    corpo = r.json()
    assert corpo["tela"] == tela
    assert corpo["versaoContrato"] == "1.0"
    assert corpo["cliente"] == {"nome": "Alfa", "sigla": "AL", "logoUrl": None}
    assert corpo["dadosAte"] == "2026-10-12"
    assert corpo["destaques"] == [{"rotulo": "Vendido", "valor": {"valor": 1.0, "unidade": "brl"}}]
    # opcional sem valor não sai
    assert "aviso" not in corpo
    codigo, empresas, mes, _ = estado["chamadas"][0]
    assert codigo == paineis.CODIGOS[tela]  # type: ignore[index]
    assert empresas == (EMPRESA,)
    assert mes is None


def test_filtros_e_mes_chegam_na_tela(client: TestClient, estado: dict[str, Any]) -> None:
    r = client.get(
        "/api/telas/vendas",
        params={"mes": "2026-10", "empresa": str(EMPRESA), "canal": "varejo", "linha": "aves"},
    )
    assert r.status_code == 200
    _, _, mes, filtros = estado["chamadas"][0]
    assert mes == date(2026, 10, 1)
    assert filtros == {"canal": "varejo", "linha": "aves"}

    client.get("/api/telas/equipe-comercial", params={"supervisor": "paula"})
    assert estado["chamadas"][1][3] == {"canal": None, "supervisor": "paula"}


def test_filtro_invalido_422(client: TestClient) -> None:
    r = client.get("/api/telas/vendas", params={"canal": "exterior"})
    assert r.status_code == 422
    assert r.json() == {"detail": "Filtro inválido."}


@pytest.mark.parametrize("mes", ["2025-01", "2026-13"])
def test_mes_fora_da_lista_ou_do_formato_422(client: TestClient, mes: str) -> None:
    r = client.get("/api/telas/clientes", params={"mes": mes})
    assert r.status_code == 422
    if mes == "2025-01":
        assert r.json() == {"detail": "Mês sem dados."}


@pytest.mark.parametrize("tela", ["vendas", "resultado", "receber-pagar", "fluxo-caixa", "pessoas"])
def test_tela_fora_do_perfil_403(client: TestClient, estado: dict[str, Any], tela: str) -> None:
    estado["perfis"] = {}
    r = client.get(f"/api/telas/{tela}")
    assert r.status_code == 403
    assert r.json() == {"detail": "Acesso negado."}
    assert estado["chamadas"] == []


def test_equipe_exige_dado_nominal(client: TestClient, estado: dict[str, Any]) -> None:
    estado["perfis"] = {str(CLIENTE): False}
    assert client.get("/api/telas/equipe-comercial").status_code == 403
    # Receber e pagar mostra clientes, fornecedores e vendedores
    assert client.get("/api/telas/receber-pagar").status_code == 403
    # as outras não mostram nomes de pessoas
    assert client.get("/api/telas/vendas").status_code == 200
    assert client.get("/api/telas/clientes").status_code == 200
    assert client.get("/api/telas/resultado").status_code == 200
    assert client.get("/api/telas/fluxo-caixa").status_code == 200
    # Pessoas tem folha, mas sem nomes: quem libera é o perfil (diretoria e RH)
    assert client.get("/api/telas/pessoas").status_code == 200


@pytest.mark.parametrize("tela", ["vendas", "resultado", "receber-pagar", "fluxo-caixa", "pessoas"])
def test_empresa_fora_do_vinculo_403(client: TestClient, tela: str) -> None:
    r = client.get(f"/api/telas/{tela}", params={"empresa": str(UUID(int=9))})
    assert r.status_code == 403


def test_filtros_do_resultado_e_do_receber_pagar(
    client: TestClient, estado: dict[str, Any]
) -> None:
    client.get(
        "/api/telas/resultado",
        params={"visao": "ano", "cenario": "meta", "comparar": "mes-anterior"},
    )
    # o padrão de `comparar` vira ausente (mesma chave da pré-montagem)
    client.get("/api/telas/resultado", params={"comparar": "orcado"})
    client.get("/api/telas/receber-pagar", params={"vendedor": "ana", "atraso": "ate-30"})

    assert [c[3] for c in estado["chamadas"]] == [
        {"visao": "ano", "cenario": "meta", "comparar": "mes-anterior"},
        {"visao": None, "cenario": None, "comparar": None},
        {"vendedor": "ana", "atraso": "ate-30"},
    ]
    assert estado["a_decidir"] is False


def test_comparar_desconhecido_422_ate_sem_carga(
    client: TestClient, estado: dict[str, Any]
) -> None:
    r = client.get("/api/telas/resultado", params={"comparar": "ano-anterior"})
    assert r.status_code == 422
    assert r.json() == {"detail": "Filtro inválido."}

    estado["carga"] = None
    # `comparar` vem do chip global: vale mesmo sem nada a mostrar
    r = client.get("/api/telas/resultado", params={"comparar": "mes-anterior"})
    assert r.status_code == 200
    assert r.json()["cartoes"] == []
    assert client.get("/api/telas/resultado", params={"visao": "ano"}).status_code == 422


def test_antes_da_primeira_carga_vem_vazio(client: TestClient, estado: dict[str, Any]) -> None:
    estado["carga"] = None
    r = client.get("/api/telas/vendas")

    assert r.status_code == 200
    corpo = r.json()
    assert corpo["dadosAte"] is None
    assert corpo["atualizadoEm"] is None
    for campo in ("filtros", "destaques", "kpis", "apoios", "cartoes"):
        assert corpo[campo] == []
    assert estado["chamadas"] == []
    # sem carga, nenhum filtro vale
    assert client.get("/api/telas/vendas", params={"canal": "varejo"}).status_code == 422


def test_pessoas_antes_da_primeira_carga_vem_vazio(
    client: TestClient, estado: dict[str, Any]
) -> None:
    estado["carga"] = None
    corpo = client.get("/api/telas/pessoas", params={"mes": "2026-10"}).json()

    assert corpo["tela"] == "pessoas"
    assert (corpo["dadosAte"], corpo["atualizadoEm"]) == (None, None)
    assert "aviso" not in corpo
    for campo in ("filtros", "destaques", "kpis", "apoios", "cartoes"):
        assert corpo[campo] == []
    assert estado["chamadas"] == []


def test_pessoas_demo_e_o_mockup(client: TestClient) -> None:
    app: Any = client.app
    demo = get_settings().model_copy(
        update={"visao_geral_demo": True, "visao_geral_cache_segundos": 0}
    )
    app.dependency_overrides[get_settings] = lambda: demo

    corpo = client.get("/api/telas/pessoas").json()
    assert [c["tipo"] for c in corpo["cartoes"]][:2] == ["destaques", "numero-grafico"]
    assert corpo["cartoes"][-1]["tipo"] == "lista-detalhe"
    admissoes = next(c for c in corpo["cartoes"] if c["id"] == "admissoes-turnover")
    assert admissoes["graficos"] == []
    assert admissoes["visoes"][0]["id"] == "todos"


def test_demo_devolve_o_mockup_e_confere_os_filtros(
    client: TestClient, estado: dict[str, Any]
) -> None:
    app: Any = client.app
    demo = get_settings().model_copy(
        update={"visao_geral_demo": True, "visao_geral_cache_segundos": 0}
    )
    app.dependency_overrides[get_settings] = lambda: demo

    corpo = client.get("/api/telas/equipe-comercial", params={"canal": "varejo"}).json()
    assert next(c["id"] for c in corpo["cartoes"]) == "destaques-mes"
    # o cliente é o do usuário, não o da fixture
    assert corpo["cliente"]["nome"] == "Alfa"
    assert client.get("/api/telas/vendas", params={"linha": "x"}).status_code == 422
    # o aviso da fixture cita um módulo que ainda não existe
    assert "aviso" not in client.get("/api/telas/clientes").json()

    # cartões a decidir ficam de fora sem PAINEIS_A_DECIDIR; o nível 3 da DRE sai sempre
    corpo = client.get("/api/telas/resultado", params={"comparar": "mes-anterior"}).json()
    ids = [c["id"] for c in corpo["cartoes"]]
    assert "ponte-ebitda" not in ids
    # aprovada em 30/10/2026
    assert "tabela-financeira" in ids
    dre = next(c for c in corpo["cartoes"] if c["id"] == "dre-gerencial")
    assert dre["niveis"] == [1, 2, 3]
    assert any(linha["nivel"] == 3 for linha in dre["linhas"])
    assert client.get("/api/telas/receber-pagar", params={"atraso": "x"}).status_code == 422
    # o demo já traz o ciclo de caixa do mockup (a tela de verdade ainda manda os prazos)
    cartoes = client.get("/api/telas/receber-pagar").json()["cartoes"]
    ciclo = next(c for c in cartoes if c["tipo"] == "ciclo-caixa")
    assert [x["rotulo"] for x in ciclo["linhas"]][-1] == "Dinheiro preso"
    # idade dos títulos: o a vencer só na barra; nas outras faixas o campo não vem
    lados = next(c for c in cartoes if c["id"] == "contas-receber-pagar")["lados"]
    assert [lado["rotuloParticipacao"] for lado in lados] == ["da carteira", "do total"]
    for lado in lados:
        assert [f.get("soNaBarra") for f in lado["idade"]] == [True, None, None, None, None]

    com_a_decidir = demo.model_copy(update={"paineis_a_decidir": True})
    app.dependency_overrides[get_settings] = lambda: com_a_decidir
    ids = [c["id"] for c in client.get("/api/telas/resultado").json()["cartoes"]]
    assert {"ponte-ebitda", "maiores-desvios"} <= set(ids)


def test_resposta_fica_no_cache_com_a_chave_dos_filtros(
    client: TestClient, estado: dict[str, Any]
) -> None:
    app: Any = client.app
    com_cache = get_settings().model_copy(update={"visao_geral_cache_segundos": 60})
    app.dependency_overrides[get_settings] = lambda: com_cache
    paineis._respostas.limpar()

    client.get("/api/telas/vendas", params={"canal": "varejo"})
    client.get("/api/telas/vendas", params={"canal": "varejo"})
    client.get("/api/telas/vendas", params={"canal": "atacado"})
    paineis._respostas.limpar()

    assert [c[3]["canal"] for c in estado["chamadas"]] == ["varejo", "atacado"]


def test_cadastro_mudado_troca_a_chave_do_cache(client: TestClient, estado: dict[str, Any]) -> None:
    app: Any = client.app
    com_cache = get_settings().model_copy(update={"visao_geral_cache_segundos": 3600})
    app.dependency_overrides[get_settings] = lambda: com_cache
    paineis._respostas.limpar()

    client.get("/api/telas/vendas")
    client.get("/api/telas/vendas")
    # ex.: um parâmetro mudou no banco, sem carga nova
    estado["versao"] = "outra"
    client.get("/api/telas/vendas")
    paineis._respostas.limpar()

    assert len(estado["chamadas"]) == 2


def test_openapi_tem_as_rotas_e_os_filtros(client: TestClient) -> None:
    esquema = client.app.openapi()  # type: ignore[attr-defined]
    for caminho, filtros in (
        ("/api/telas/vendas", {"mes", "empresa", "canal", "linha"}),
        ("/api/telas/equipe-comercial", {"mes", "empresa", "canal", "supervisor"}),
        ("/api/telas/clientes", {"mes", "empresa"}),
        ("/api/telas/resultado", {"mes", "empresa", "visao", "cenario", "comparar"}),
        ("/api/telas/receber-pagar", {"mes", "empresa", "vendedor", "atraso"}),
        # o setor se escolhe dentro do cartão; `comparar` não vale aqui
        ("/api/telas/fluxo-caixa", {"mes", "empresa", "visao", "projecao"}),
        ("/api/telas/pessoas", {"mes", "empresa"}),
    ):
        parametros = esquema["paths"][caminho]["get"]["parameters"]
        # `cliente` (slug do cliente escolhido) vale em toda tela
        assert {p["name"] for p in parametros} == filtros | {"cliente"}
        assert all(p["required"] is False for p in parametros)
    assert "PainelPayload" in esquema["components"]["schemas"]


def _cartao_graficos(id_: str) -> Any:
    from app.telas.paineis.contrato import CartaoGraficos, GraficoCartao, GraficoEmpilhado

    return CartaoGraficos(
        id=id_,
        tipo="graficos",
        largura=12,
        titulo=id_,
        graficos=[
            GraficoCartao(
                grafico=GraficoEmpilhado(tipo="empilhado", unidade="pct", rotulos=[], series=[])
            )
        ],
    )


def test_explicacao_so_nos_cartoes_que_tem_texto(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def montador(*_: Any) -> Conteudo:
        return Conteudo(cartoes=[_cartao_graficos("canais"), _cartao_graficos("vendas-12-meses")])

    monkeypatch.setitem(paineis.MONTADORES, "vendas", montador)
    canais, doze_meses = client.get("/api/telas/vendas").json()["cartoes"]

    assert canais["explicacao"] == EXPLICACOES["vendas"]["canais"]
    assert "explicacao" not in doze_meses


def test_explicacoes_sao_as_da_fixture_do_front() -> None:
    # textos copiados iguais das fixtures; os ids existem na tela
    for tela, textos in EXPLICACOES.items():
        cartoes = {c.id: c for c in paineis.demo(tela).cartoes}
        for id_, texto in textos.items():
            assert cartoes[id_].explicacao == texto, (tela, id_)


# ---------- nomes por cartão (Pessoas, "Cargos e salários") ----------

NOMES = "/api/telas/pessoas/cartoes/cargos-salarios/nomes"


def _demo(client: TestClient, cache_segundos: int = 0) -> None:
    app: Any = client.app
    demo = get_settings().model_copy(
        update={"visao_geral_demo": True, "visao_geral_cache_segundos": cache_segundos}
    )
    app.dependency_overrides[get_settings] = lambda: demo


def _cargos(client: TestClient) -> dict[str, Any]:
    cartoes = client.get("/api/telas/pessoas").json()["cartoes"]
    return next(c for c in cartoes if c["id"] == "cargos-salarios")


def _log(estado: dict[str, Any]) -> tuple[Any, ...]:
    """A linha de seg.log_acesso da última requisição."""
    (linha,) = estado["sessoes"][-1].logs
    return tuple(linha)


def test_chave_de_nomes_so_para_quem_ve_dado_nominal(
    client: TestClient, estado: dict[str, Any]
) -> None:
    _demo(client)
    assert _cargos(client)["nomes"] == {"rotulo": "Mostrar nomes"}
    # diretoria: vê a tela, mas não o dado nominal; o resto do cartão não muda
    estado["perfis"] = {str(CLIENTE): False}
    sem = _cargos(client)
    assert "nomes" not in sem
    assert sem["titulo"] == "Cargos e salários"


def test_chave_de_nomes_entra_na_chave_do_cache(client: TestClient, estado: dict[str, Any]) -> None:
    _demo(client, cache_segundos=60)
    paineis._respostas.limpar()
    try:
        assert "nomes" in _cargos(client)
        estado["perfis"] = {str(CLIENTE): False}
        # mesmo cliente, empresas e filtros: a resposta do perfil de RH não serve
        assert "nomes" not in _cargos(client)
    finally:
        paineis._respostas.limpar()


@pytest.mark.parametrize(
    "caminho",
    [
        "/api/telas/pessoas/cartoes/folha-por-setor/nomes",
        "/api/telas/vendas/cartoes/cargos-salarios/nomes",
        "/api/telas/nenhuma/cartoes/cargos-salarios/nomes",
    ],
)
def test_nomes_de_cartao_sem_essa_opcao_404(
    client: TestClient, estado: dict[str, Any], caminho: str
) -> None:
    r = client.get(caminho)
    assert r.status_code == 404
    assert r.json() == {"detail": "Este cartão não tem nomes."}


def test_nomes_sem_dado_nominal_403(
    client: TestClient, estado: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    chamadas: list[Any] = []
    monkeypatch.setitem(paineis.NOMES, ("pessoas", "cargos-salarios"), chamadas.append)
    # a chave não aparece, mas qualquer um pode chamar a URL
    estado["perfis"] = {str(CLIENTE): False}
    r = client.get(NOMES, params={"mes": "2026-10"})

    assert r.status_code == 403
    assert r.json() == {"detail": "Você não pode ver os nomes deste cartão."}
    assert chamadas == []
    assert _log(estado)[4:] == (
        "PES-01:cargos-salarios:nomes",
        "negado",
        6,
        "tela exige dado nominal e o perfil não permite (filtros: mes=2026-10)",
    )


def test_nomes_sem_a_tela_403_generico(client: TestClient, estado: dict[str, Any]) -> None:
    estado["perfis"] = {}
    r = client.get(NOMES)
    assert r.status_code == 403
    assert r.json() == {"detail": "Acesso negado."}
    assert _log(estado)[6] == 4


def test_nomes_do_cartao(
    client: TestClient, estado: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    recebido: list[tuple[Any, ...]] = []

    def montador(ctx: ContextoConsolidado, mes: date | None, carga: Any) -> TabelaNomes:
        recebido.append((ctx.codigo_tela, ctx.empresas, mes, carga))
        return TabelaNomes(colunas=COLUNAS_NOMES, linhas=[], vazio="Nenhuma pessoa.")

    monkeypatch.setitem(paineis.NOMES, ("pessoas", "cargos-salarios"), montador)
    r = client.get(NOMES, params={"mes": "2026-10", "empresa": str(EMPRESA)})

    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    assert r.json()["colunas"][1] == {
        "rotulo": "Cargo",
        "alinhar": "esquerda",
        "ocultarNoCelular": True,
    }
    assert recebido == [("PES-01", (EMPRESA,), date(2026, 10, 1), CARGA)]
    # quem pediu, de qual cliente e empresa, com quais filtros
    assert _log(estado) == (
        USUARIO,
        UUID(int=1),
        CLIENTE,
        EMPRESA,
        "PES-01:cargos-salarios:nomes",
        "permitido",
        None,
        "filtros: mes=2026-10",
    )
    assert client.get(NOMES, params={"mes": "2026-09"}).status_code == 422


def test_nomes_demo(client: TestClient, estado: dict[str, Any]) -> None:
    _demo(client)
    r = client.get(NOMES)
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    corpo = r.json()
    assert [c["rotulo"] for c in corpo["colunas"]] == [
        "Pessoa",
        "Cargo",
        "Salário",
        "Custo mensal",
    ]
    assert corpo["linhas"][0]["celulas"][0] == {"valor": "Renato Albuquerque", "sub": "Logística"}
    # o demo também exige o perfil
    estado["perfis"] = {str(CLIENTE): False}
    assert client.get(NOMES).status_code == 403

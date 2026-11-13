"""Provisões do Fluxo de caixa: escolhas salvas, gravação e as rotas PUT — sem banco.

Nas rotas, a sessão é falsa (só responde a seg.contexto_acesso); a gravação, o commit e os
parâmetros salvos são trocados por registros.
"""

import json
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.core.db import Sessao, get_sessao
from app.farol.parametro import Parametro
from app.main import create_app
from app.seguranca.auth import UsuarioToken
from app.telas.paineis import provisoes, router
from app.telas.paineis.provisoes import (
    EscolhaGrupo,
    chave_grupo,
    chave_periodo,
    gravar_grupo,
    ler_escolhas,
)

USUARIO = UUID("d1000000-0000-4000-8000-00000000000a")
CLIENTE = UUID("c1000000-0000-4000-8000-00000000000a")
OUTRO_CLIENTE = UUID("c1000000-0000-4000-8000-00000000000b")
EMPRESA = UUID("e1000000-0000-4000-8000-0000000000a1")
HOJE = date(2026, 11, 9)
ROTA = "/api/telas/fluxo-caixa/cartoes/provisoes"


# ---------- escolhas salvas ----------


def _p(chave: str, valor: Any, dia: date = date(2026, 11, 1)) -> Parametro:
    return Parametro(chave, valor, dia, None)


def test_sem_nada_salvo_vale_o_padrao() -> None:
    e = ler_escolhas([], None, HOJE)
    assert (e.periodo, e.meses) == ("6-meses", 6)
    assert e.do_grupo("folha") == EscolhaGrupo(ligado=True)
    assert e.do_grupo("investimentos") == EscolhaGrupo(ligado=False)


def test_escolhas_por_empresa_e_a_mais_recente() -> None:
    parametros = [
        _p(chave_periodo(None), "3-meses"),
        _p(chave_periodo(EMPRESA), "12-meses", date(2026, 10, 1)),
        _p(chave_periodo(EMPRESA), "ultimo-mes", date(2026, 11, 5)),
        _p(
            chave_grupo(None, "folha"),
            {"ligado": True, "valor": 800000, "por": "Ana", "em": "2026-11-05"},
        ),
    ]
    assert ler_escolhas(parametros, None, HOJE).periodo == "3-meses"
    assert ler_escolhas(parametros, EMPRESA, HOJE).periodo == "ultimo-mes"
    folha = ler_escolhas(parametros, None, HOJE).do_grupo("folha")
    assert folha == EscolhaGrupo(
        ligado=True, valor=Decimal(800000), por="Ana", em=date(2026, 11, 5)
    )
    # cada empresa tem as suas
    assert ler_escolhas(parametros, EMPRESA, HOJE).do_grupo("folha").valor is None


@pytest.mark.parametrize(
    "salvo",
    [
        "ligado",
        {"valor": 10},
        {"ligado": "sim", "valor": 10},
    ],
)
def test_grupo_salvo_invalido_vira_o_padrao(salvo: Any) -> None:
    e = ler_escolhas([_p(chave_grupo(None, "folha"), salvo)], None, HOJE)
    assert e.do_grupo("folha") == EscolhaGrupo(ligado=True)


def test_valor_salvo_negativo_e_ignorado() -> None:
    salvo = {"ligado": False, "valor": -5, "por": "Ana", "em": "2026-11-05"}
    e = ler_escolhas([_p(chave_grupo(None, "folha"), salvo)], None, HOJE)
    assert e.do_grupo("folha") == EscolhaGrupo(ligado=False)


def test_periodo_salvo_invalido_vira_o_padrao() -> None:
    assert ler_escolhas([_p(chave_periodo(None), "2-anos")], None, HOJE).periodo == "6-meses"


def test_chaves_ficam_nas_preferencias_do_cliente() -> None:
    # a migration 19 só deixa o api_site gravar chaves "preferencia.*"
    assert chave_periodo(None) == "preferencia.provisao.todas.periodo"
    assert chave_grupo(EMPRESA, "folha") == f"preferencia.provisao.{EMPRESA}.grupo.folha"


# ---------- gravação ----------


class ConexaoGravacao:
    def __init__(self) -> None:
        self.chamadas: list[tuple[str, dict[str, Any]]] = []

    def execute(self, consulta: str, params: dict[str, Any]) -> None:
        self.chamadas.append((consulta, params))


def _gravado(conn: ConexaoGravacao) -> tuple[dict[str, Any], bool]:
    (consulta, params) = conn.chamadas[-1]
    assert "seg.log_alteracao" in consulta
    return json.loads(params["valor"]), params["com_nome"]


def test_valor_novo_grava_quem_e_quando() -> None:
    conn = ConexaoGravacao()
    gravar_grupo(
        conn,  # type: ignore[arg-type]
        CLIENTE,
        USUARIO,
        None,
        "folha",
        True,
        Decimal(800000),
        EscolhaGrupo(ligado=True),
        HOJE,
    )
    salvo, com_nome = _gravado(conn)
    # o nome vem de seg.usuario, na mesma consulta
    assert salvo == {"ligado": True, "valor": 800000, "em": "2026-11-09"}
    assert com_nome is True
    assert conn.chamadas[-1][1]["chave"] == "preferencia.provisao.todas.grupo.folha"


def test_desligar_sem_mudar_o_valor_mantem_o_autor() -> None:
    conn = ConexaoGravacao()
    anterior = EscolhaGrupo(ligado=True, valor=Decimal(800000), por="Ana", em=date(2026, 11, 5))
    gravar_grupo(
        conn,  # type: ignore[arg-type]
        CLIENTE,
        USUARIO,
        EMPRESA,
        "folha",
        False,
        Decimal(800000),
        anterior,
        HOJE,
    )
    assert _gravado(conn) == (
        {"ligado": False, "valor": 800000, "por": "Ana", "em": "2026-11-05"},
        False,
    )


def test_valor_nulo_volta_ao_sugerido() -> None:
    conn = ConexaoGravacao()
    anterior = EscolhaGrupo(ligado=True, valor=Decimal(5), por="Ana", em=date(2026, 11, 5))
    gravar_grupo(
        conn,  # type: ignore[arg-type]
        CLIENTE,
        USUARIO,
        None,
        "folha",
        True,
        None,
        anterior,
        HOJE,
    )
    assert _gravado(conn) == ({"ligado": True, "valor": None, "por": None, "em": None}, False)


# ---------- rotas ----------


class Resultado:
    def __init__(self, linha: dict[str, Any]) -> None:
        self.linha = linha

    def fetchone(self) -> dict[str, Any]:
        return self.linha


class ConexaoFalsa:
    """Só responde à consulta dos fatos de acesso (seg.contexto_acesso)."""

    def __init__(self, estado: dict[str, Any]) -> None:
        self.estado = estado

    def execute(self, consulta: str, params: Any = None) -> Resultado:
        assert "seg.contexto_acesso" in consulta
        return Resultado(
            {
                "usuario_id": USUARIO,
                "ativo": True,
                "vinculos": [{"cliente_id": str(CLIENTE), "papel": self.estado["papel"]}],
                "perfis_tela": self.estado["perfis"],
                "empresas": [{"cliente_id": str(CLIENTE), "empresa_id": str(EMPRESA)}],
                "perfil": "cliente_principal",
                "cliente_id": CLIENTE,
            }
        )


@pytest.fixture
def estado(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    e: dict[str, Any] = {
        "papel": "leitor",
        "perfis": {str(CLIENTE): False},
        "parametros": [],
        "eventos": [],
    }

    def gravar_periodo(
        conn: Any, cliente: UUID, usuario: UUID, empresa: UUID | None, periodo: str, hoje: date
    ) -> None:
        e["eventos"].append(("periodo", cliente, usuario, empresa, periodo))

    def gravar_grupo(
        conn: Any,
        cliente: UUID,
        usuario: UUID,
        empresa: UUID | None,
        grupo: str,
        ligado: bool,
        valor: Decimal | None,
        anterior: EscolhaGrupo,
        hoje: date,
    ) -> None:
        e["eventos"].append(("grupo", cliente, empresa, grupo, ligado, valor, anterior))

    def confirmar(sessao: Sessao) -> None:
        e["eventos"].append(("confirmar",))

    monkeypatch.setattr(provisoes, "gravar_periodo", gravar_periodo)
    monkeypatch.setattr(provisoes, "gravar_grupo", gravar_grupo)
    monkeypatch.setattr(router, "confirmar_agora", confirmar)
    monkeypatch.setattr(router, "parametros_do_cliente", lambda conn, c: e["parametros"])
    router._respostas.limpar()
    yield e
    router._respostas.limpar()


@pytest.fixture
def client(estado: dict[str, Any]) -> TestClient:
    app = create_app()

    def sessao() -> Sessao:
        return Sessao(
            conn=ConexaoFalsa(estado),  # type: ignore[arg-type]
            usuario=UsuarioToken(id_autenticacao=UUID(int=1)),
        )

    app.dependency_overrides[get_sessao] = sessao
    return TestClient(app)


def _guardar(cliente: UUID, tela: str) -> tuple[Any, ...]:
    chave = (tela, cliente, None, (EMPRESA,), False, None, (), None, HOJE, False, False, "v")
    router._respostas.guardar(chave, object(), 900)  # type: ignore[arg-type]
    return chave


def test_periodo_grava_confirma_e_apaga_o_cache_do_cliente(
    client: TestClient, estado: dict[str, Any]
) -> None:
    deste = [_guardar(CLIENTE, "fluxo-caixa"), _guardar(CLIENTE, "vendas")]
    do_outro = _guardar(OUTRO_CLIENTE, "fluxo-caixa")

    r = client.put(f"{ROTA}/periodo", json={"periodo": "12-meses"})

    assert r.status_code == 204
    assert r.content == b""
    # leitor pode: vale quem vê a FIN-04, sem restrição de papel
    assert estado["eventos"] == [
        ("periodo", CLIENTE, USUARIO, None, "12-meses"),
        ("confirmar",),
    ]
    assert all(router._respostas.obter(c) is None for c in deste)
    assert router._respostas.obter(do_outro) is not None


def test_periodo_da_empresa(client: TestClient, estado: dict[str, Any]) -> None:
    r = client.put(f"{ROTA}/periodo", params={"empresa": str(EMPRESA)}, json={"periodo": "3-meses"})
    assert r.status_code == 204
    assert estado["eventos"][0] == ("periodo", CLIENTE, USUARIO, EMPRESA, "3-meses")


def test_periodo_invalido_422(client: TestClient, estado: dict[str, Any]) -> None:
    r = client.put(f"{ROTA}/periodo", json={"periodo": "2-anos"})
    assert r.status_code == 422
    assert r.json() == {"detail": "Período inválido."}
    assert estado["eventos"] == []


def test_grupo_grava_com_a_escolha_anterior(client: TestClient, estado: dict[str, Any]) -> None:
    salvo = {"ligado": True, "valor": 5, "por": "Ana", "em": "2026-11-05"}
    estado["parametros"] = [Parametro(chave_grupo(None, "folha"), salvo, HOJE, None)]

    r = client.put(f"{ROTA}/grupos/folha", json={"ligado": False, "valor": 800000})

    assert r.status_code == 204
    anterior = EscolhaGrupo(ligado=True, valor=Decimal(5), por="Ana", em=date(2026, 11, 5))
    assert estado["eventos"] == [
        ("grupo", CLIENTE, None, "folha", False, Decimal(800000), anterior),
        ("confirmar",),
    ]


def test_grupo_valor_nulo_volta_ao_sugerido(client: TestClient, estado: dict[str, Any]) -> None:
    r = client.put(f"{ROTA}/grupos/investimentos", json={"ligado": True, "valor": None})
    assert r.status_code == 204
    assert estado["eventos"][0][3:6] == ("investimentos", True, None)


@pytest.mark.parametrize("valor", [-1, "800000", "mil", True, float("inf")])
def test_grupo_valor_invalido_422(client: TestClient, estado: dict[str, Any], valor: Any) -> None:
    if isinstance(valor, float):
        # JSON não tem infinito: vai como texto cru
        r = client.put(
            f"{ROTA}/grupos/folha",
            content='{"ligado": true, "valor": 1e999}',
            headers={"Content-Type": "application/json"},
        )
    else:
        r = client.put(f"{ROTA}/grupos/folha", json={"ligado": True, "valor": valor})
    assert r.status_code == 422
    assert r.json() == {"detail": "O valor precisa ser zero ou mais."}
    assert estado["eventos"] == []


def test_grupo_inexistente_404(client: TestClient, estado: dict[str, Any]) -> None:
    r = client.put(f"{ROTA}/grupos/marketing", json={"ligado": True, "valor": None})
    assert r.status_code == 404
    assert r.json() == {"detail": "Grupo de provisão não encontrado."}
    assert estado["eventos"] == []


@pytest.mark.parametrize(
    "caminho",
    [
        "/api/telas/fluxo-caixa/cartoes/saldo-projetado/periodo",
        "/api/telas/vendas/cartoes/provisoes/periodo",
    ],
)
def test_cartao_sem_provisoes_404(client: TestClient, estado: dict[str, Any], caminho: str) -> None:
    r = client.put(caminho, json={"periodo": "6-meses"})
    assert r.status_code == 404
    assert r.json() == {"detail": "Este cartão não tem provisões."}


def test_sem_acesso_ao_fluxo_de_caixa_403(client: TestClient, estado: dict[str, Any]) -> None:
    estado["perfis"] = {}
    r = client.put(f"{ROTA}/periodo", json={"periodo": "6-meses"})
    assert r.status_code == 403
    assert r.json() == {"detail": "Você não pode mudar as provisões deste painel."}
    assert estado["eventos"] == []


def test_sem_token_401() -> None:
    r = TestClient(create_app()).put(f"{ROTA}/periodo", json={"periodo": "6-meses"})
    assert r.status_code == 401

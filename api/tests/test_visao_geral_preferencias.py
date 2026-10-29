"""PUT /api/telas/visao-geral/preferencias/{id} e a leitura da escolha salva — sem banco.

A sessão é falsa: a checagem de acesso lê os fatos de uma conexão que só responde a
seg.contexto_acesso; a gravação e o commit são trocados por registros.
"""

from collections.abc import Iterator
from datetime import date
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.core.cache import memo, por_requisicao
from app.core.db import Sessao, get_sessao
from app.farol.parametro import Parametro
from app.main import create_app
from app.seguranca.auth import UsuarioToken
from app.telas.visao_geral import router
from app.telas.visao_geral.preferencias import PREFERENCIAS, valor, valores

USUARIO = UUID("d1000000-0000-4000-8000-00000000000a")
CLIENTE = UUID("c1000000-0000-4000-8000-00000000000a")
OUTRO_CLIENTE = UUID("c1000000-0000-4000-8000-00000000000b")
EMPRESA = UUID("e1000000-0000-4000-8000-0000000000a1")
ROTA = "/api/telas/visao-geral/preferencias"


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
            }
        )


@pytest.fixture
def estado(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    e: dict[str, Any] = {"papel": "gestor", "perfis": {str(CLIENTE): True}, "eventos": []}

    def gravar(conn: Any, cliente_id: UUID, usuario_id: UUID, p: Any, escolha: str, hoje: date):
        e["eventos"].append(("gravar", cliente_id, usuario_id, p.id, escolha))

    def confirmar(sessao: Sessao) -> None:
        e["eventos"].append(("confirmar",))

    monkeypatch.setattr(router.preferencias, "gravar", gravar)
    monkeypatch.setattr(router, "confirmar_agora", confirmar)
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


def _guardar(cliente: UUID, mes: str | None) -> tuple[Any, ...]:
    chave = (cliente, None, (EMPRESA,), mes, None, date(2026, 10, 28), False, ())
    router._respostas.guardar(chave, object(), 900)  # type: ignore[arg-type]
    return chave


def test_grava_confirma_e_apaga_o_cache_do_cliente(
    client: TestClient, estado: dict[str, Any]
) -> None:
    deste = [_guardar(CLIENTE, None), _guardar(CLIENTE, "2026-09")]
    do_outro = _guardar(OUTRO_CLIENTE, None)

    r = client.put(f"{ROTA}/linhas-custo", json={"valor": "3"})

    assert r.status_code == 204
    assert r.content == b""
    # grava para o cliente do vínculo e confirma antes de responder
    assert estado["eventos"] == [
        ("gravar", CLIENTE, USUARIO, "linhas-custo", "3"),
        ("confirmar",),
    ]
    # todos os meses e empresas do cliente saem do cache; os de outro cliente ficam
    assert all(router._respostas.obter(c) is None for c in deste)
    assert router._respostas.obter(do_outro) is not None


def test_automatico_tambem_vale(client: TestClient, estado: dict[str, Any]) -> None:
    assert client.put(f"{ROTA}/linhas-custo", json={"valor": "auto"}).status_code == 204


def test_preferencia_desconhecida_404(client: TestClient, estado: dict[str, Any]) -> None:
    chave = _guardar(CLIENTE, None)
    r = client.put(f"{ROTA}/cores", json={"valor": "3"})
    assert r.status_code == 404
    assert r.json() == {"detail": "Preferência não encontrada."}
    assert estado["eventos"] == []
    assert router._respostas.obter(chave) is not None


@pytest.mark.parametrize("valor_", ["8", "0", "", "Auto"])
def test_opcao_invalida_422(client: TestClient, estado: dict[str, Any], valor_: str) -> None:
    r = client.put(f"{ROTA}/linhas-custo", json={"valor": valor_})
    assert r.status_code == 422
    assert r.json() == {"detail": "Opção inválida."}
    assert estado["eventos"] == []


def test_corpo_sem_valor_422(client: TestClient, estado: dict[str, Any]) -> None:
    assert client.put(f"{ROTA}/linhas-custo", json={}).status_code == 422
    assert estado["eventos"] == []


def test_leitor_nao_muda_403(client: TestClient, estado: dict[str, Any]) -> None:
    estado["papel"] = "leitor"
    r = client.put(f"{ROTA}/linhas-custo", json={"valor": "3"})
    assert r.status_code == 403
    assert r.json() == {"detail": "Você não pode mudar esta escolha."}
    assert estado["eventos"] == []


def test_sem_acesso_a_visao_geral_403_generico(client: TestClient, estado: dict[str, Any]) -> None:
    estado["perfis"] = {}
    r = client.put(f"{ROTA}/linhas-custo", json={"valor": "3"})
    assert r.status_code == 403
    assert r.json() == {"detail": "Acesso negado."}
    assert estado["eventos"] == []


def test_sem_token_401() -> None:
    r = TestClient(create_app()).put(f"{ROTA}/linhas-custo", json={"valor": "3"})
    assert r.status_code == 401


def _com_parametros(*parametros: Parametro) -> Any:
    """Conexão que não pode ser usada: os parâmetros vêm do cache da requisição."""
    memo(("parametros", CLIENTE), lambda: list(parametros))
    return None


def test_sem_escolha_salva_vale_o_automatico() -> None:
    with por_requisicao():
        conn = _com_parametros()
        assert valor(conn, CLIENTE, "linhas-custo") == "auto"
        assert valores(conn, CLIENTE) == (("linhas-custo", "auto"),)


def test_vale_a_escolha_mais_recente() -> None:
    with por_requisicao():
        conn = _com_parametros(
            Parametro("preferencia.linhas-custo", "2", date(2026, 1, 1), None),
            Parametro("preferencia.linhas-custo", "5", date(2026, 3, 1), None),
        )
        assert valor(conn, CLIENTE, "linhas-custo") == "5"


@pytest.mark.parametrize("salvo", ["9", 3, {"valor": "3"}])
def test_escolha_salva_invalida_vira_o_padrao(salvo: Any) -> None:
    with por_requisicao():
        conn = _com_parametros(Parametro("preferencia.linhas-custo", salvo, date(2026, 1, 1), None))
        assert valor(conn, CLIENTE, "linhas-custo") == "auto"


def test_seletor_da_custos_como_no_mockup() -> None:
    s = PREFERENCIAS["linhas-custo"].seletor("auto")
    assert s.model_dump(mode="json", by_alias=True, exclude_defaults=True) == {
        "id": "linhas-custo",
        "rotulo": "Linhas de custo",
        "opcoes": [
            {"id": "auto", "rotulo": "Automático (80%)"},
            *({"id": str(n), "rotulo": str(n)} for n in range(1, 8)),
        ],
        "valor": "auto",
        "nota": (
            "Automático: as linhas que somam 80% do custo, no máximo 7, e o resto em Outros. "
            "A escolha fica salva para o cliente."
        ),
    }

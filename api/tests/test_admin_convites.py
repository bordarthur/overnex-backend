"""Convite pela Gestão (app/admin/convites.py): regras do pedido, do reenvio e o acionamento
do job — sem banco (conexão falsa) e sem rede (transporte falso)."""

import json
from typing import Any
from uuid import UUID

import httpx
import pytest
from psycopg import errors
from pydantic import SecretStr

from app.admin import convites, usuarios
from app.admin.usuarios import NaoEncontrado, RegraInvalida
from app.core.config import Settings, get_settings

ADMIN = UUID("d1000000-0000-4000-8000-0000000000ad")
CARLA = UUID("d1000000-0000-4000-8000-0000000000c1")
CLIENTE_A = UUID("c1000000-0000-4000-8000-00000000000a")
CLIENTE_B = UUID("c1000000-0000-4000-8000-00000000000b")
DOMINIOS = "bordgestao.com.br"


class Resultado:
    def __init__(self, linha: dict[str, Any] | None) -> None:
        self.linha = linha

    def fetchone(self) -> dict[str, Any] | None:
        return self.linha


class ConexaoFalsa:
    """Responde às leituras do convite e guarda as gravações."""

    def __init__(
        self, atual: dict[str, Any] | None = None, erro_ao_gravar: Exception | None = None
    ) -> None:
        self.atual = atual
        self.erro_ao_gravar = erro_ao_gravar
        self.lido: Any = None
        self.gravacoes: list[tuple[str, dict[str, Any]]] = []

    def execute(self, consulta: str, params: Any = None) -> Resultado:
        if consulta in (convites.SQL_CONFERIR, convites.SQL_PENDENTE):
            self.lido = params
            return Resultado(self.atual)
        if self.erro_ao_gravar and consulta in (convites.SQL_PEDIR, convites.SQL_REPEDIR):
            raise self.erro_ao_gravar
        self.gravacoes.append((consulta, params))
        return Resultado({"id": CARLA} if consulta == convites.SQL_PEDIR else None)


def _conferido(**mudancas: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "existe": False,
        "padrao": 240,
        "pedidos": [{"id": str(CLIENTE_A), "slug": "alfa"}, {"id": str(CLIENTE_B), "slug": "beta"}],
    }
    return base | mudancas


def _pedir(conn: ConexaoFalsa, **mudancas: Any) -> UUID:
    pedido: dict[str, Any] = {
        "email": " Carla@Alfa.example ",
        "nome": " Carla Alfa ",
        "perfil": "cliente_usuario",
        "clientes": ["alfa", "beta", "alfa"],
        "sessao_minutos": 480,
    }
    return convites.pedir(conn, ADMIN, admin_dominios=DOMINIOS, **(pedido | mudancas))  # type: ignore[arg-type]


# ---------- pedido de convite ----------


def test_pedir_cria_a_pessoa_com_os_vinculos_e_registra() -> None:
    conn = ConexaoFalsa(_conferido())

    assert _pedir(conn) == CARLA

    # e-mail em minúsculas e sem espaços; empresas sem repetição
    assert conn.lido == {
        "email": "carla@alfa.example",
        "perfil": "cliente_usuario",
        "slugs": ["alfa", "beta"],
    }
    (pedido, log) = conn.gravacoes
    assert pedido == (
        convites.SQL_PEDIR,
        {
            "email": "carla@alfa.example",
            "nome": "Carla Alfa",
            "perfil": "cliente_usuario",
            "sessao": 480,
            "clientes": [str(CLIENTE_A), str(CLIENTE_B)],
        },
    )
    assert log[0] == usuarios.SQL_LOG
    assert (log[1]["autor"], log[1]["tabela"], log[1]["registro"], log[1]["operacao"]) == (
        ADMIN,
        "seg.usuario",
        str(CARLA),
        "insert",
    )
    assert json.loads(log[1]["depois"]) == {
        "email": "carla@alfa.example",
        "nome": "Carla Alfa",
        "perfil": "cliente_usuario",
        "sessao_minutos": 480,
        "clientes": ["alfa", "beta"],
        "convite": "fila",
    }


def test_pedir_sem_empresa_e_aceito() -> None:
    conn = ConexaoFalsa(_conferido())
    _pedir(conn, clientes=[])
    assert conn.gravacoes[0][1]["clientes"] == []


def test_pedir_sessao_igual_ao_padrao_fica_sem_valor_proprio() -> None:
    for minutos in (240, None):
        conn = ConexaoFalsa(_conferido())
        _pedir(conn, sessao_minutos=minutos)
        assert conn.gravacoes[0][1]["sessao"] is None


def test_pedir_admin_so_com_email_da_bord_e_sem_vinculos() -> None:
    conn = ConexaoFalsa(_conferido(padrao=1440))

    _pedir(conn, email="fulano@bordgestao.com.br", perfil="admin", sessao_minutos=1440)

    # os slugs pedidos são ignorados: o admin vê todas, sem vínculo
    assert conn.lido["slugs"] == []
    assert conn.gravacoes[0][1]["clientes"] == []
    assert conn.gravacoes[0][1]["sessao"] is None


@pytest.mark.parametrize(
    ("conferido", "mudancas", "mensagem"),
    [
        ({}, {"email": "carla"}, "E-mail inválido."),
        ({}, {"email": "carla@alfa"}, "E-mail inválido."),
        ({}, {"email": "carla alfa@alfa.example"}, "E-mail inválido."),
        ({}, {"perfil": "dono"}, "Perfil inválido."),
        ({}, {"perfil": "admin"}, "Admin BORD só com e-mail da BORD."),
        ({}, {"sessao_minutos": 45}, "Tempo de sessão inválido."),
        ({}, {"sessao_minutos": 1440}, "Mais de 12 h, só Admin BORD."),
        ({}, {"clientes": ["alfa", "gama"]}, "Empresa não encontrada: gama."),
        ({"existe": True}, {}, "Este e-mail já tem cadastro no Overnex."),
    ],
)
def test_pedir_recusado_nao_grava(
    conferido: dict[str, Any], mudancas: dict[str, Any], mensagem: str
) -> None:
    conn = ConexaoFalsa(_conferido(**conferido))
    with pytest.raises(RegraInvalida, match=mensagem):
        _pedir(conn, **mudancas)
    assert conn.gravacoes == []


def test_pedir_com_o_mesmo_email_ao_mesmo_tempo() -> None:
    # a conferência passou, mas o índice único do e-mail recusou a gravação
    conn = ConexaoFalsa(_conferido(), erro_ao_gravar=errors.UniqueViolation("duplicado"))
    with pytest.raises(RegraInvalida, match="já tem cadastro"):
        _pedir(conn)
    assert conn.gravacoes == []


# ---------- reenvio ----------


def _pendente(**mudancas: Any) -> dict[str, Any]:
    base = {"email": "carla@alfa.example", "ativo": True, "entrou": False, "na_fila": False}
    return base | mudancas


def test_repedir_poe_outro_convite_na_fila() -> None:
    conn = ConexaoFalsa(_pendente())

    convites.repedir(conn, ADMIN, CARLA)  # type: ignore[arg-type]

    (pedido, log) = conn.gravacoes
    assert pedido == (convites.SQL_REPEDIR, {"id": CARLA, "autor": ADMIN})
    assert (log[0], log[1]["tabela"], log[1]["registro"]) == (
        usuarios.SQL_LOG,
        "seg.convite_fila",
        str(CARLA),
    )


@pytest.mark.parametrize(
    ("atual", "erro", "mensagem"),
    [
        (None, NaoEncontrado, "Pessoa não encontrada."),
        (_pendente(ativo=False), RegraInvalida, "está desativado"),
        (_pendente(entrou=True), RegraInvalida, "já entrou no Overnex"),
        (_pendente(na_fila=True), RegraInvalida, "já está na fila"),
    ],
)
def test_repedir_recusado_nao_grava(
    atual: dict[str, Any] | None, erro: type[Exception], mensagem: str
) -> None:
    conn = ConexaoFalsa(atual)
    with pytest.raises(erro, match=mensagem):
        convites.repedir(conn, ADMIN, CARLA)  # type: ignore[arg-type]
    assert conn.gravacoes == []


def test_repedir_duas_vezes_ao_mesmo_tempo() -> None:
    conn = ConexaoFalsa(_pendente(), erro_ao_gravar=errors.UniqueViolation("duplicado"))
    with pytest.raises(RegraInvalida, match="já está na fila"):
        convites.repedir(conn, ADMIN, CARLA)  # type: ignore[arg-type]


# ---------- acionamento do job ----------


def _settings(**mudancas: Any) -> Settings:
    return get_settings().model_copy(update=mudancas)


def _com_token(**mudancas: Any) -> Settings:
    return _settings(
        github_convites_token=SecretStr("github_pat_teste"),
        github_convites_repo="dono/repositorio",
        **mudancas,
    )


def test_sem_token_nao_chama_o_github_e_o_texto_avisa() -> None:
    chamadas: list[httpx.Request] = []

    def github(pedido: httpx.Request) -> httpx.Response:
        chamadas.append(pedido)
        return httpx.Response(204)

    settings = _settings()
    convites.acionar_envio(settings, transport=httpx.MockTransport(github))

    assert chamadas == []
    assert convites.mensagem(settings) == (
        "Convite na fila: sai na próxima rodada de envio, em até uma hora."
    )


def test_com_token_aciona_o_workflow_de_convites() -> None:
    chamadas: list[httpx.Request] = []

    def github(pedido: httpx.Request) -> httpx.Response:
        chamadas.append(pedido)
        return httpx.Response(204)

    settings = _com_token(github_convites_ref="dev")
    convites.acionar_envio(settings, transport=httpx.MockTransport(github))

    (pedido,) = chamadas
    assert pedido.method == "POST"
    assert str(pedido.url) == (
        "https://api.github.com/repos/dono/repositorio/actions/workflows/convites.yml/dispatches"
    )
    assert pedido.headers["authorization"] == "Bearer github_pat_teste"
    assert json.loads(pedido.content) == {"ref": "dev"}
    assert convites.mensagem(settings) == "Convite na fila: sai em instantes."


@pytest.mark.parametrize(
    "resposta", [httpx.Response(401), httpx.Response(404), httpx.ConnectError("recusada")]
)
def test_github_fora_do_ar_ou_recusando_nao_derruba_o_pedido(
    resposta: httpx.Response | Exception, caplog: pytest.LogCaptureFixture
) -> None:
    def github(pedido: httpx.Request) -> httpx.Response:
        if isinstance(resposta, Exception):
            raise resposta
        return resposta

    convites.acionar_envio(_com_token(), transport=httpx.MockTransport(github))

    assert "Job de convites não acionado" in caplog.text
    # o token nunca vai para o log
    assert "github_pat_teste" not in caplog.text


@pytest.mark.parametrize(
    "mudancas",
    [
        {"GITHUB_CONVITES_REPO": "sem-barra"},
        {"GITHUB_CONVITES_REPO": "dono/repo/../outro"},
        {"GITHUB_CONVITES_WORKFLOW": "../../outro"},
        {"GITHUB_CONVITES_REF": "main?x=1"},
    ],
)
def test_configuracao_do_github_fora_do_formato(
    monkeypatch: pytest.MonkeyPatch, mudancas: dict[str, str]
) -> None:
    for nome, valor in mudancas.items():
        monkeypatch.setenv(nome, valor)
    with pytest.raises(ValueError, match="GITHUB_CONVITES"):
        Settings()

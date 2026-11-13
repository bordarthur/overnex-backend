"""Rotas /api/auth/* — sem banco, com o GoTrue simulado (httpx.MockTransport)."""

import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.auth.cadastro import UsuarioCadastrado, get_verificar_cadastro
from app.auth.gotrue import GoTrue
from app.auth.router import get_gotrue
from app.auth.uso import get_uso_sessao
from app.core.config import Ambiente, ProvedorAuth, Settings, get_settings
from app.main import create_app

SUPABASE_URL = "https://projeto-teste.supabase.co"
ANON = "sb_publishable_teste"
FRONT_URL = "https://overnex.teste"
ID_ANA = UUID("5b000000-0000-4000-8000-00000000000a")
SESSAO_ANA = UUID("5e000000-0000-4000-8000-0000000000a1")
# atende à política do front: 8+ caracteres, maiúscula e número
SENHA = "Senha-secreta-123"
REFRESH_VELHO = "refresh-velho-xyz"
JSON = {"Content-Type": "application/json"}

# a API só lê o session_id do access token (sem conferir a assinatura: veio do GoTrue)
CHAVE_JWT = "chave-de-teste-com-32-bytes-ou-mais"
ACCESS = jwt.encode(
    {"sub": str(ID_ANA), "session_id": str(SESSAO_ANA), "role": "authenticated"},
    CHAVE_JWT,
    algorithm="HS256",
)

# 2030-01-01T00:00:00Z
SESSAO: dict[str, Any] = {
    "access_token": ACCESS,
    "token_type": "bearer",
    "expires_in": 3600,
    "expires_at": 1893456000,
    "refresh_token": "refresh-novo-def",
    "user": {"id": str(ID_ANA), "email": "ana@alfa.example"},
}
ESPERADO = {
    "accessToken": ACCESS,
    "expiraEm": "2030-01-01T00:00:00Z",
    "usuario": {"nome": "Ana Alfa", "email": "ana@alfa.example"},
    # o tempo de sessão parada da pessoa (4 h: padrão do Cliente principal)
    "inatividadeSegundos": 14400,
}

Resposta = httpx.Response | Exception
Responder = Callable[[httpx.Request], Resposta]


@dataclass
class GoTrueFalso:
    """Respostas por (método, caminho); guarda os pedidos recebidos."""

    respostas: dict[tuple[str, str], Responder] = field(default_factory=dict)
    pedidos: list[httpx.Request] = field(default_factory=list)

    def responder(self, metodo: str, caminho: str, resposta: Resposta) -> None:
        def responder(_pedido: httpx.Request) -> Resposta:
            if isinstance(resposta, Exception):
                return resposta
            # resposta nova a cada chamada (o httpx consome a anterior)
            return httpx.Response(
                resposta.status_code, headers=resposta.headers, content=resposta.content
            )

        self.respostas[(metodo, caminho)] = responder

    def __call__(self, pedido: httpx.Request) -> httpx.Response:
        self.pedidos.append(pedido)
        caminho = pedido.url.path.removeprefix("/auth/v1")
        responder = self.respostas.get((pedido.method, caminho))
        resposta = responder(pedido) if responder else httpx.Response(404)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta

    def em(self, metodo: str, caminho: str) -> list[httpx.Request]:
        return [
            p for p in self.pedidos if p.method == metodo and p.url.path == f"/auth/v1{caminho}"
        ]


@dataclass
class CadastroFalso:
    ativos: dict[UUID, UsuarioCadastrado] = field(
        default_factory=lambda: {
            ID_ANA: UsuarioCadastrado(
                nome="Ana Alfa", email="ana@alfa.example", inatividade_segundos=14400
            )
        }
    )
    chamadas: list[tuple[UUID, str]] = field(default_factory=list)

    def __call__(self, id_autenticacao: UUID, recurso: str) -> UsuarioCadastrado | None:
        self.chamadas.append((id_autenticacao, recurso))
        return self.ativos.get(id_autenticacao)


@dataclass
class UsoFalso:
    """seg.sessao_uso em memória, com relógio que o teste avança."""

    limite: timedelta = timedelta(minutes=30)
    agora: datetime = datetime(2030, 1, 1, 12, 0)
    ultimo_uso: dict[UUID, tuple[UUID, datetime]] = field(default_factory=dict)

    def iniciar(self, sessao_id: UUID, id_autenticacao: UUID) -> None:
        self.ultimo_uso[sessao_id] = (id_autenticacao, self.agora)

    def renovar(self, sessao_id: UUID, id_autenticacao: UUID) -> bool:
        registro = self.ultimo_uso.get(sessao_id)
        if registro and registro[0] == id_autenticacao and registro[1] > self.agora - self.limite:
            self.ultimo_uso[sessao_id] = (id_autenticacao, self.agora)
            return True
        self.ultimo_uso.pop(sessao_id, None)
        return False


def _erro(status: int, codigo: str, msg: str = "", **extra: Any) -> httpx.Response:
    return httpx.Response(status, json={"code": status, "error_code": codigo, "msg": msg, **extra})


@pytest.fixture
def gotrue() -> GoTrueFalso:
    falso = GoTrueFalso()
    falso.responder("POST", "/token", httpx.Response(200, json=SESSAO))
    falso.responder("POST", "/logout", httpx.Response(204))
    falso.responder("POST", "/recover", httpx.Response(200, json={}))
    falso.responder("POST", "/verify", httpx.Response(200, json=SESSAO))
    falso.responder("PUT", "/user", httpx.Response(200, json=SESSAO["user"]))
    return falso


@pytest.fixture
def cadastro() -> CadastroFalso:
    return CadastroFalso()


@pytest.fixture
def uso() -> UsoFalso:
    # sessão da Ana em uso agora (os testes de renovar partem de um login recente)
    falso = UsoFalso()
    falso.iniciar(SESSAO_ANA, ID_ANA)
    return falso


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url=SecretStr("postgresql://x@localhost/x"),
        auth_provedor=ProvedorAuth.SUPABASE,
        supabase_url=SUPABASE_URL,
        supabase_anon_key=SecretStr(ANON),
        front_url=FRONT_URL,
        ambiente=Ambiente.DEV,
        cookie_seguro=True,
    )


@pytest.fixture
def client(
    gotrue: GoTrueFalso, cadastro: CadastroFalso, uso: UsoFalso, settings: Settings
) -> Iterator[TestClient]:
    app = create_app(settings)
    cliente_gotrue = GoTrue(SUPABASE_URL, ANON, transport=httpx.MockTransport(gotrue))
    app.dependency_overrides[get_gotrue] = lambda: cliente_gotrue
    app.dependency_overrides[get_verificar_cadastro] = lambda: cadastro
    app.dependency_overrides[get_uso_sessao] = lambda: uso
    app.dependency_overrides[get_settings] = lambda: settings
    # sem o "with": o lifespan (pool do banco) não roda
    yield TestClient(app, base_url="https://testserver")
    cliente_gotrue.close()


def _cookies(r: httpx.Response) -> list[str]:
    return r.headers.get_list("set-cookie")


def _atributos(cookie: str) -> dict[str, str]:
    partes = [p.strip() for p in cookie.split(";")]
    return {p.split("=", 1)[0].lower(): (p.split("=", 1)[1] if "=" in p else "") for p in partes}


def _confere_cookie_gravado(r: httpx.Response, valor: str) -> None:
    (cookie,) = _cookies(r)
    atributos = _atributos(cookie)
    assert atributos["ovx_refresh"] == valor
    assert "httponly" in atributos
    assert "secure" in atributos
    assert atributos["samesite"].lower() == "strict"
    assert atributos["path"] == "/api/auth"
    assert atributos["max-age"] == str(7 * 24 * 3600)


def _confere_cookie_apagado(r: httpx.Response) -> None:
    (cookie,) = _cookies(r)
    atributos = _atributos(cookie)
    assert atributos["ovx_refresh"] in ("", '""')
    assert atributos["max-age"] == "0"
    assert atributos["path"] == "/api/auth"


def _entrar(client: TestClient, email: str = "Ana@Alfa.example ", senha: str = SENHA) -> Any:
    return client.post("/api/auth/entrar", json={"email": email, "senha": senha})


# --- entrar ------------------------------------------------------------------------


def test_entrar_sucesso(client: TestClient, gotrue: GoTrueFalso, cadastro: CadastroFalso) -> None:
    r = _entrar(client)

    assert r.status_code == 200
    assert r.json() == ESPERADO
    assert r.headers["cache-control"] == "no-store"
    _confere_cookie_gravado(r, "refresh-novo-def")

    (pedido,) = gotrue.em("POST", "/token")
    assert pedido.url.params["grant_type"] == "password"
    assert json.loads(pedido.content) == {"email": "ana@alfa.example", "password": SENHA}
    assert pedido.headers["apikey"] == ANON
    # chave publishable não é JWT: não vai no Authorization
    assert "authorization" not in pedido.headers
    assert cadastro.chamadas == [(ID_ANA, "auth:entrar")]


def test_entrar_registra_o_uso_da_sessao(client: TestClient, uso: UsoFalso) -> None:
    uso.ultimo_uso.clear()

    assert _entrar(client).status_code == 200

    assert uso.ultimo_uso == {SESSAO_ANA: (ID_ANA, uso.agora)}


def test_entrar_sem_cadastro_ativo_nao_registra_o_uso(
    client: TestClient, cadastro: CadastroFalso, uso: UsoFalso
) -> None:
    uso.ultimo_uso.clear()
    cadastro.ativos.clear()

    assert _entrar(client).status_code == 403

    assert uso.ultimo_uso == {}


def test_entrar_access_token_sem_session_id_503(client: TestClient, gotrue: GoTrueFalso) -> None:
    sem_sessao = jwt.encode({"sub": str(ID_ANA)}, CHAVE_JWT, algorithm="HS256")
    gotrue.responder(
        "POST", "/token", httpx.Response(200, json={**SESSAO, "access_token": sem_sessao})
    )

    r = _entrar(client)

    assert r.status_code == 503
    assert _cookies(r) == []


@pytest.mark.parametrize(
    "resposta",
    [
        # o GoTrue responde assim tanto para senha errada quanto para e-mail inexistente
        _erro(400, "invalid_credentials", "Invalid login credentials"),
        _erro(400, "email_not_confirmed", "Email not confirmed"),
        httpx.Response(400, json={"error": "invalid_grant", "error_description": "x"}),
        _erro(422, "validation_failed", "Unable to validate email address"),
    ],
)
def test_entrar_credenciais_recusadas_401(
    client: TestClient, gotrue: GoTrueFalso, cadastro: CadastroFalso, resposta: httpx.Response
) -> None:
    gotrue.responder("POST", "/token", resposta)

    r = _entrar(client, email="naoexiste@alfa.example")

    assert r.status_code == 401
    assert r.json() == {"detail": "E-mail ou senha não conferem. Confira os dois e tente de novo."}
    assert _cookies(r) == []
    assert cadastro.chamadas == []


def test_entrar_sem_cadastro_ativo_403_sem_cookie(
    client: TestClient, gotrue: GoTrueFalso, cadastro: CadastroFalso
) -> None:
    cadastro.ativos.clear()

    r = _entrar(client)

    assert r.status_code == 403
    assert r.json() == {"detail": "Acesso negado."}
    assert _cookies(r) == []
    # a sessão aberta no GoTrue é encerrada
    (logout,) = gotrue.em("POST", "/logout")
    assert logout.headers["authorization"] == f"Bearer {ACCESS}"


@pytest.mark.parametrize(
    "falha",
    [
        httpx.ConnectError("recusada"),
        httpx.ReadTimeout("lento"),
        httpx.Response(500, text="erro"),
        httpx.Response(502, text="gateway"),
    ],
)
def test_entrar_gotrue_fora_do_ar_503(
    client: TestClient, gotrue: GoTrueFalso, falha: Resposta
) -> None:
    gotrue.responder("POST", "/token", falha)

    r = _entrar(client)

    assert r.status_code == 503
    assert r.json() == {"detail": "Autenticação indisponível."}
    assert _cookies(r) == []


def test_entrar_limite_do_supabase_429(client: TestClient, gotrue: GoTrueFalso) -> None:
    gotrue.responder("POST", "/token", _erro(429, "over_request_rate_limit"))

    r = _entrar(client)

    assert r.status_code == 429
    assert r.json() == {"detail": "Muitas tentativas. Tente de novo em alguns minutos."}
    assert r.headers["retry-after"] == "60"


def test_entrar_5_senhas_erradas_429_com_retry_after(
    client: TestClient, gotrue: GoTrueFalso
) -> None:
    gotrue.responder("POST", "/token", _erro(400, "invalid_credentials"))

    for _ in range(5):
        assert _entrar(client).status_code == 401
    r = _entrar(client)

    assert r.status_code == 429
    assert r.json() == {"detail": "Foram 5 tentativas com a senha errada."}
    # até a primeira das 5 sair da janela de 15 min
    assert 895 <= int(r.headers["retry-after"]) <= 900
    # a 6ª nem chega ao GoTrue, e insistir não conta (a espera não cresce)
    assert len(gotrue.em("POST", "/token")) == 5
    assert int(_entrar(client).headers["retry-after"]) <= int(r.headers["retry-after"])
    # outro e-mail do mesmo IP segue
    assert _entrar(client, email="bruno@beta.example").status_code == 401


def test_entrar_acertar_a_senha_zera_as_falhas(client: TestClient, gotrue: GoTrueFalso) -> None:
    gotrue.responder("POST", "/token", _erro(400, "invalid_credentials"))
    for _ in range(4):
        assert _entrar(client).status_code == 401

    gotrue.responder("POST", "/token", httpx.Response(200, json=SESSAO))
    assert _entrar(client).status_code == 200

    gotrue.responder("POST", "/token", _erro(400, "invalid_credentials"))
    for _ in range(5):
        assert _entrar(client).status_code == 401
    assert _entrar(client).status_code == 429


def test_entrar_limite_por_ip(client: TestClient, gotrue: GoTrueFalso) -> None:
    gotrue.responder("POST", "/token", _erro(400, "invalid_credentials"))

    for i in range(50):
        assert _entrar(client, email=f"usuario{i}@alfa.example").status_code == 401
    r = _entrar(client, email="outro@alfa.example")

    assert r.status_code == 429
    assert r.json() == {"detail": "Muitas tentativas. Tente de novo em alguns minutos."}
    assert 0 < int(r.headers["retry-after"]) <= 300


def test_entrar_corpo_invalido_422(client: TestClient) -> None:
    r = client.post("/api/auth/entrar", json={"email": "ana@alfa.example"})
    assert r.status_code == 422
    assert SENHA not in r.text


def test_entrar_nao_registra_senha_nem_tokens(
    client: TestClient,
    gotrue: GoTrueFalso,
    cadastro: CadastroFalso,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    assert _entrar(client).status_code == 200
    cadastro.ativos.clear()
    assert _entrar(client).status_code == 403
    gotrue.responder("POST", "/token", _erro(400, "invalid_credentials"))
    assert _entrar(client).status_code == 401

    for segredo in (SENHA, ACCESS, "refresh-novo-def"):
        assert segredo not in caplog.text


# --- Content-Type ------------------------------------------------------------------


@pytest.mark.parametrize("rota", ["entrar", "renovar", "sair", "esqueci-senha", "definir-senha"])
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Content-Type": "text/plain"},
        {"Content-Type": "application/x-www-form-urlencoded"},
        {"Content-Type": "multipart/form-data; boundary=x"},
    ],
)
def test_content_type_obrigatorio_415(
    client: TestClient, gotrue: GoTrueFalso, rota: str, headers: dict[str, str]
) -> None:
    corpo = json.dumps({"email": "ana@alfa.example", "senha": SENHA})

    r = client.post(f"/api/auth/{rota}", content=corpo, headers=headers)

    assert r.status_code == 415
    assert r.json() == {"detail": "Envie o corpo em JSON (Content-Type: application/json)."}
    assert gotrue.pedidos == []


def test_content_type_com_charset_aceito(client: TestClient) -> None:
    corpo = json.dumps({"email": "ana@alfa.example", "senha": SENHA})
    r = client.post(
        "/api/auth/entrar",
        content=corpo,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    assert r.status_code == 200


def test_get_nao_existe(client: TestClient) -> None:
    assert client.get("/api/auth/entrar", headers=JSON).status_code == 405


# --- renovar -----------------------------------------------------------------------


def _renovar(client: TestClient, cookie: str | None = REFRESH_VELHO) -> Any:
    headers = dict(JSON)
    if cookie is not None:
        headers["Cookie"] = f"ovx_refresh={cookie}"
    return client.post("/api/auth/renovar", json={}, headers=headers)


def test_renovar_sucesso_troca_o_cookie(
    client: TestClient, gotrue: GoTrueFalso, cadastro: CadastroFalso
) -> None:
    r = _renovar(client)

    assert r.status_code == 200
    assert r.json() == ESPERADO
    _confere_cookie_gravado(r, "refresh-novo-def")
    (pedido,) = gotrue.em("POST", "/token")
    assert pedido.url.params["grant_type"] == "refresh_token"
    assert json.loads(pedido.content) == {"refresh_token": REFRESH_VELHO}
    assert cadastro.chamadas == [(ID_ANA, "auth:renovar")]


def test_renovar_sem_corpo_aceito(client: TestClient) -> None:
    r = client.post("/api/auth/renovar", headers={**JSON, "Cookie": f"ovx_refresh={REFRESH_VELHO}"})
    assert r.status_code == 200


def test_renovar_sem_cookie_401(client: TestClient, gotrue: GoTrueFalso) -> None:
    r = _renovar(client, cookie=None)

    assert r.status_code == 401
    assert r.json() == {"detail": "Sessão expirada. Entre de novo."}
    _confere_cookie_apagado(r)
    assert gotrue.pedidos == []


@pytest.mark.parametrize(
    "codigo", ["refresh_token_not_found", "refresh_token_already_used", "session_expired"]
)
def test_renovar_recusado_401_e_cookie_apagado(
    client: TestClient, gotrue: GoTrueFalso, codigo: str
) -> None:
    gotrue.responder("POST", "/token", _erro(400, codigo))

    r = _renovar(client)

    assert r.status_code == 401
    assert r.json() == {"detail": "Sessão expirada. Entre de novo."}
    _confere_cookie_apagado(r)


def test_renovar_usuario_desativado_403_e_cookie_apagado(
    client: TestClient, gotrue: GoTrueFalso, cadastro: CadastroFalso
) -> None:
    cadastro.ativos.clear()

    r = _renovar(client)

    assert r.status_code == 403
    assert r.json() == {"detail": "Acesso negado."}
    _confere_cookie_apagado(r)
    assert len(gotrue.em("POST", "/logout")) == 1


@pytest.mark.parametrize(
    "falha", [httpx.ConnectError("recusada"), _erro(429, "over_request_rate_limit")]
)
def test_renovar_gotrue_indisponivel_503_mantem_cookie(
    client: TestClient, gotrue: GoTrueFalso, falha: Resposta
) -> None:
    gotrue.responder("POST", "/token", falha)

    r = _renovar(client)

    assert r.status_code == 503
    assert r.json() == {"detail": "Autenticação indisponível."}
    assert _cookies(r) == []


# --- sessão sem uso ----------------------------------------------------------------

SEM_USO = {"detail": "Sua sessão terminou por falta de uso. Entre de novo."}


def test_renovar_logo_depois_de_entrar_200(client: TestClient, uso: UsoFalso) -> None:
    uso.ultimo_uso.clear()
    assert _entrar(client).status_code == 200
    uso.agora += timedelta(minutes=1)

    r = _renovar(client, cookie="refresh-novo-def")

    assert r.status_code == 200
    assert r.json() == ESPERADO
    _confere_cookie_gravado(r, "refresh-novo-def")
    # a renovação conta como uso
    assert uso.ultimo_uso[SESSAO_ANA] == (ID_ANA, uso.agora)


def test_renovar_dentro_do_limite_recomeca_a_contagem(client: TestClient, uso: UsoFalso) -> None:
    for _ in range(3):
        uso.agora += timedelta(minutes=29)
        assert _renovar(client).status_code == 200


def test_renovar_sessao_parada_401_cookie_apagado_e_sessao_encerrada(
    client: TestClient, gotrue: GoTrueFalso, cadastro: CadastroFalso, uso: UsoFalso
) -> None:
    uso.agora += timedelta(minutes=30, seconds=1)

    r = _renovar(client)

    assert r.status_code == 401
    assert r.json() == SEM_USO
    assert r.headers["cache-control"] == "no-store"
    _confere_cookie_apagado(r)
    # o refresh token que o GoTrue acabou de emitir também é revogado
    (logout,) = gotrue.em("POST", "/logout")
    assert logout.headers["authorization"] == f"Bearer {ACCESS}"
    assert logout.url.params["scope"] == "local"
    assert cadastro.chamadas == []
    # parada uma vez, parada para sempre: a sessão não volta
    uso.agora = datetime(2030, 1, 1, 12, 0)
    assert _renovar(client).json() == SEM_USO


def test_renovar_sessao_sem_registro_401(client: TestClient, uso: UsoFalso) -> None:
    # sessão aberta antes da migration 21 (ou cuja linha já foi apagada)
    uso.ultimo_uso.clear()

    r = _renovar(client)

    assert r.status_code == 401
    assert r.json() == SEM_USO
    _confere_cookie_apagado(r)


def test_renovar_sessao_parada_com_logout_recusado_401(
    client: TestClient, gotrue: GoTrueFalso, uso: UsoFalso
) -> None:
    gotrue.responder("POST", "/logout", httpx.ConnectError("recusada"))
    uso.agora += timedelta(hours=2)

    r = _renovar(client)

    assert r.status_code == 401
    assert r.json() == SEM_USO
    _confere_cookie_apagado(r)


def test_renovar_depois_de_sair_401(client: TestClient, gotrue: GoTrueFalso) -> None:
    # GoTrue com estado: o logout revoga os refresh tokens da sessão
    revogados: set[str] = set()

    def logout(pedido: httpx.Request) -> httpx.Response:
        assert pedido.url.params["scope"] == "local"
        revogados.add(SESSAO["refresh_token"])
        return httpx.Response(204)

    def token(pedido: httpx.Request) -> httpx.Response:
        refresh = pedido.url.params["grant_type"] == "refresh_token"
        if refresh and json.loads(pedido.content)["refresh_token"] in revogados:
            return _erro(400, "refresh_token_not_found", "Invalid Refresh Token")
        return httpx.Response(200, json=SESSAO)

    gotrue.respostas[("POST", "/logout")] = logout
    gotrue.respostas[("POST", "/token")] = token
    entrou = _entrar(client)
    assert entrou.status_code == 200

    saiu = client.post(
        "/api/auth/sair",
        json={},
        headers={
            "Authorization": f"Bearer {entrou.json()['accessToken']}",
            "Cookie": "ovx_refresh=refresh-novo-def",
        },
    )
    assert saiu.status_code == 204
    _confere_cookie_apagado(saiu)

    # mesmo que o navegador ainda tivesse o cookie
    r = _renovar(client, cookie="refresh-novo-def")

    assert r.status_code == 401
    assert r.json() == {"detail": "Sessão expirada. Entre de novo."}
    _confere_cookie_apagado(r)


def test_limite_de_inatividade_nao_e_configuracao(settings: Settings) -> None:
    """O limite sai do cadastro da pessoa (seg.limite_sessao): a API não o passa."""
    pool = object()
    uso = get_uso_sessao(pool=pool)  # type: ignore[arg-type]
    assert uso.pool is pool
    assert not hasattr(settings, "auth_inatividade_segundos")


@pytest.mark.parametrize(
    ("mudanca", "variavel"),
    [
        ({"auth_convite_horas": 0}, "AUTH_CONVITE_HORAS"),
        ({"admin_dominios": " , "}, "ADMIN_DOMINIOS"),
    ],
)
def test_configuracao_da_gestao_invalida(
    settings: Settings, mudanca: dict[str, Any], variavel: str
) -> None:
    with pytest.raises(ValueError, match=variavel):
        Settings(**{**settings.model_dump(), **mudanca})


# --- sair --------------------------------------------------------------------------


def test_sair_com_access_token(client: TestClient, gotrue: GoTrueFalso) -> None:
    r = client.post(
        "/api/auth/sair",
        json={},
        headers={"Authorization": "Bearer access-atual", "Cookie": f"ovx_refresh={REFRESH_VELHO}"},
    )

    assert r.status_code == 204
    assert r.content == b""
    _confere_cookie_apagado(r)
    (logout,) = gotrue.em("POST", "/logout")
    assert logout.headers["authorization"] == "Bearer access-atual"
    assert logout.headers["apikey"] == ANON
    assert logout.url.params["scope"] == "local"


def test_sair_sem_access_token(client: TestClient, gotrue: GoTrueFalso) -> None:
    r = client.post("/api/auth/sair", json={})

    assert r.status_code == 204
    _confere_cookie_apagado(r)
    assert gotrue.pedidos == []


@pytest.mark.parametrize(
    "falha", [httpx.ConnectError("recusada"), _erro(401, "bad_jwt"), httpx.Response(500)]
)
def test_sair_sempre_204(client: TestClient, gotrue: GoTrueFalso, falha: Resposta) -> None:
    gotrue.responder("POST", "/logout", falha)

    r = client.post("/api/auth/sair", json={}, headers={"Authorization": "Bearer vencido"})

    assert r.status_code == 204
    _confere_cookie_apagado(r)


# --- esqueci-senha -----------------------------------------------------------------


def _esqueci(client: TestClient, email: str = "ana@alfa.example") -> Any:
    return client.post("/api/auth/esqueci-senha", json={"email": email})


def test_esqueci_senha_204(client: TestClient, gotrue: GoTrueFalso) -> None:
    r = _esqueci(client, email=" Ana@Alfa.example")

    assert r.status_code == 204
    assert r.content == b""
    (pedido,) = gotrue.em("POST", "/recover")
    assert pedido.url.params["redirect_to"] == f"{FRONT_URL}/definir-senha"
    assert json.loads(pedido.content) == {"email": "ana@alfa.example"}


@pytest.mark.parametrize(
    "resposta",
    [
        httpx.Response(200, json={}),  # o GoTrue já não revela e-mail inexistente
        _erro(404, "user_not_found"),
        _erro(429, "over_email_send_rate_limit"),
        _erro(422, "validation_failed"),
    ],
)
def test_esqueci_senha_email_inexistente_204(
    client: TestClient, gotrue: GoTrueFalso, resposta: httpx.Response
) -> None:
    gotrue.responder("POST", "/recover", resposta)

    r = _esqueci(client, email="ninguem@nada.example")

    assert r.status_code == 204
    assert r.content == b""


def test_esqueci_senha_limite_429(client: TestClient, gotrue: GoTrueFalso) -> None:
    for _ in range(3):
        assert _esqueci(client).status_code == 204
    r = _esqueci(client)

    assert r.status_code == 429
    assert r.json() == {"detail": "Muitas tentativas. Tente de novo em alguns minutos."}
    assert 895 <= int(r.headers["retry-after"]) <= 900
    assert len(gotrue.em("POST", "/recover")) == 3


def test_esqueci_senha_gotrue_fora_do_ar_503(client: TestClient, gotrue: GoTrueFalso) -> None:
    gotrue.responder("POST", "/recover", httpx.ConnectError("recusada"))

    r = _esqueci(client)

    assert r.status_code == 503
    assert r.json() == {"detail": "Autenticação indisponível."}


# --- definir-senha -----------------------------------------------------------------


def _definir(client: TestClient, tipo: str = "convite", senha: str = SENHA) -> Any:
    return client.post(
        "/api/auth/definir-senha", json={"tokenHash": "hash-do-link", "tipo": tipo, "senha": senha}
    )


@pytest.mark.parametrize(
    ("tipo", "tipo_gotrue"), [("convite", "invite"), ("recuperacao", "recovery")]
)
def test_definir_senha_sucesso(
    client: TestClient,
    gotrue: GoTrueFalso,
    cadastro: CadastroFalso,
    uso: UsoFalso,
    tipo: str,
    tipo_gotrue: str,
) -> None:
    uso.ultimo_uso.clear()
    r = _definir(client, tipo=tipo)

    assert r.status_code == 200
    assert r.json() == ESPERADO
    _confere_cookie_gravado(r, "refresh-novo-def")
    (verify,) = gotrue.em("POST", "/verify")
    assert json.loads(verify.content) == {"type": tipo_gotrue, "token_hash": "hash-do-link"}
    (put,) = gotrue.em("PUT", "/user")
    assert put.headers["authorization"] == f"Bearer {ACCESS}"
    assert json.loads(put.content) == {"password": SENHA}
    assert cadastro.chamadas == [(ID_ANA, "auth:definir-senha")]
    assert SESSAO_ANA in uso.ultimo_uso


@pytest.mark.parametrize(
    ("senha", "mensagem"),
    [
        ("Abc1234", "A senha precisa ter pelo menos 8 caracteres."),
        ("", "A senha precisa ter pelo menos 8 caracteres, uma letra maiúscula e um número."),
        ("abcdefgh", "A senha precisa ter uma letra maiúscula e um número."),
        ("abcdefg1", "A senha precisa ter uma letra maiúscula."),
        ("Abcdefgh", "A senha precisa ter um número."),
        ("1234567", "A senha precisa ter pelo menos 8 caracteres e uma letra maiúscula."),
        ("A1" + "a" * 71, "A senha pode ter no máximo 72 caracteres."),
    ],
)
def test_definir_senha_fora_da_politica_400_sem_gastar_o_link(
    client: TestClient, gotrue: GoTrueFalso, senha: str, mensagem: str
) -> None:
    r = _definir(client, senha=senha)

    assert r.status_code == 400
    assert r.json() == {"detail": mensagem}
    assert gotrue.pedidos == []


@pytest.mark.parametrize(
    "resposta",
    [
        _erro(403, "otp_expired", "Email link is invalid or has expired"),
        _erro(400, "bad_code_verifier"),
        _erro(404, "not_found"),
        _erro(422, "validation_failed"),
    ],
)
def test_definir_senha_link_vencido_ou_usado_410(
    client: TestClient, gotrue: GoTrueFalso, resposta: httpx.Response
) -> None:
    gotrue.responder("POST", "/verify", resposta)

    r = _definir(client)

    assert r.status_code == 410
    assert r.json() == {"detail": "Este link não vale mais."}
    assert _cookies(r) == []
    assert gotrue.em("PUT", "/user") == []


@pytest.mark.parametrize(
    ("resposta", "mensagem"),
    [
        (
            _erro(422, "weak_password", "x", weak_password={"reasons": ["length"]}),
            "A senha precisa ter pelo menos 8 caracteres, uma letra maiúscula e um número.",
        ),
        (
            _erro(422, "weak_password", "x", weak_password={"reasons": ["characters"]}),
            "A senha precisa ter pelo menos 8 caracteres, uma letra maiúscula e um número.",
        ),
        (
            _erro(422, "weak_password", "x", weak_password={"reasons": ["pwned"]}),
            "Essa senha aparece em vazamentos conhecidos. Escolha outra.",
        ),
        (_erro(422, "same_password"), "A nova senha precisa ser diferente da anterior."),
    ],
)
def test_definir_senha_recusada_pelo_supabase_400(
    client: TestClient, gotrue: GoTrueFalso, resposta: httpx.Response, mensagem: str
) -> None:
    gotrue.responder("PUT", "/user", resposta)

    r = _definir(client, tipo="recuperacao")

    assert r.status_code == 400
    assert r.json() == {"detail": mensagem}
    assert _cookies(r) == []


def test_definir_senha_aceita_maiuscula_acentuada(client: TestClient) -> None:
    assert _definir(client, senha="Ácaro-2026").status_code == 200


def test_definir_senha_sem_cadastro_ativo_403(
    client: TestClient, gotrue: GoTrueFalso, cadastro: CadastroFalso
) -> None:
    cadastro.ativos.clear()

    r = _definir(client)

    assert r.status_code == 403
    assert r.json() == {"detail": "Acesso negado."}
    assert _cookies(r) == []


def test_definir_senha_tipo_invalido_422(client: TestClient, gotrue: GoTrueFalso) -> None:
    assert _definir(client, tipo="signup").status_code == 422
    assert gotrue.pedidos == []


def test_definir_senha_gotrue_fora_do_ar_503(client: TestClient, gotrue: GoTrueFalso) -> None:
    gotrue.responder("POST", "/verify", httpx.ReadTimeout("lento"))

    r = _definir(client)

    assert r.status_code == 503
    assert r.json() == {"detail": "Autenticação indisponível."}


# --- provedor ----------------------------------------------------------------------


def test_rotas_so_existem_com_supabase() -> None:
    settings = Settings(
        database_url=SecretStr("postgresql://x@localhost/x"),
        auth_provedor=ProvedorAuth.ENTRA,
        ambiente=Ambiente.DEV,
        auth_fake=True,
    )
    r = TestClient(create_app(settings)).post("/api/auth/entrar", json={}, headers=JSON)
    assert r.status_code == 404

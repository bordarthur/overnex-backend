"""Job de convites (app.cli.enviar_convites): envio sem banco e a fila com banco, pelo role
carga (migration 29)."""

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from uuid import UUID

import httpx
import psycopg
import pytest
from psycopg.rows import DictRow, dict_row

from app.auth.gotrue import GoTrue
from app.cli.enviar_convites import (
    MSG_DESATIVADO,
    MSG_EMAIL,
    MSG_GRAVACAO,
    MSG_INDISPONIVEL,
    MSG_JA_ENTROU,
    MSG_JA_EXISTE,
    MSG_LIMITE,
    FilaPostgres,
    Pedido,
    enviar,
    processar,
)
from tests.conftest import CLIENTE_A, DATABASE_URL_ADMIN

SUPABASE_URL = "https://projeto-teste.supabase.co"
FRONT_URL = "https://overnex.teste"
ID_NOVO = UUID("5b000000-0000-4000-8000-0000000000c1")
ADMIN = "d1000000-0000-4000-8000-0000000000ad"
EMAIL = "carla@alfa.example"

PEDIDO = Pedido(convite_id=7, email=EMAIL, nome="Carla Alfa", ativo=True, entrou=False)


@dataclass
class Auth:
    """Supabase Auth falso: responde ao convite e guarda os pedidos."""

    convite: httpx.Response | Exception = field(
        default_factory=lambda: httpx.Response(200, json={"id": str(ID_NOVO)})
    )
    pedidos: list[httpx.Request] = field(default_factory=list)

    def __call__(self, pedido: httpx.Request) -> httpx.Response:
        self.pedidos.append(pedido)
        if isinstance(self.convite, Exception):
            raise self.convite
        return self.convite


def _gotrue(auth: Auth) -> GoTrue:
    return GoTrue(SUPABASE_URL, "sb_secret_teste", transport=httpx.MockTransport(auth))


@dataclass
class FilaFalsa:
    itens: list[Pedido]
    erros: list[Exception] = field(default_factory=list)
    concluidos: list[tuple[int, UUID | None, str | None]] = field(default_factory=list)

    def pendentes(self) -> list[Pedido]:
        return self.itens

    def concluir(self, convite_id: int, id_autenticacao: UUID | None, motivo: str | None) -> None:
        if self.erros:
            raise self.erros.pop(0)
        self.concluidos.append((convite_id, id_autenticacao, motivo))


# --- envio (sem banco) -------------------------------------------------------------------


def test_envia_o_convite_com_o_link_de_definir_senha() -> None:
    auth = Auth()

    assert enviar(PEDIDO, _gotrue(auth), FRONT_URL) == (ID_NOVO, None)

    (pedido,) = auth.pedidos
    assert (pedido.method, pedido.url.path) == ("POST", "/auth/v1/invite")
    assert pedido.url.params["redirect_to"] == f"{FRONT_URL}/definir-senha"
    assert json.loads(pedido.content) == {"email": EMAIL, "data": {"nome": "Carla Alfa"}}
    assert pedido.headers["apikey"] == "sb_secret_teste"


def test_sem_nome_usa_o_comeco_do_email() -> None:
    auth = Auth()
    enviar(Pedido(7, EMAIL, None, True, False), _gotrue(auth), FRONT_URL)
    assert json.loads(auth.pedidos[0].content)["data"] == {"nome": "carla"}


@pytest.mark.parametrize(
    ("pedido", "motivo"),
    [
        (Pedido(7, EMAIL, "Carla", False, False), MSG_DESATIVADO),
        (Pedido(7, EMAIL, "Carla", True, True), MSG_JA_ENTROU),
    ],
)
def test_desativado_ou_ja_entrou_nao_recebe_convite(pedido: Pedido, motivo: str) -> None:
    auth = Auth()
    assert enviar(pedido, _gotrue(auth), FRONT_URL) == (None, motivo)
    assert auth.pedidos == []


@pytest.mark.parametrize(
    ("resposta", "motivo"),
    [
        (
            httpx.Response(429, json={"code": 429, "error_code": "over_email_send_rate_limit"}),
            MSG_LIMITE,
        ),
        (
            httpx.Response(
                422,
                json={
                    "code": 422,
                    "error_code": "email_exists",
                    "msg": "A user with this email address has already been registered",
                },
            ),
            MSG_JA_EXISTE,
        ),
        (httpx.Response(400, json={"code": 400, "error_code": "email_address_invalid"}), MSG_EMAIL),
        (
            httpx.Response(400, json={"code": 400, "error_code": "validation_failed"}),
            "O serviço de login recusou o convite (erro 400, validation_failed).",
        ),
        (httpx.Response(500), MSG_INDISPONIVEL),
        (httpx.ConnectError("recusada"), MSG_INDISPONIVEL),
    ],
)
def test_motivo_pronto_quando_o_convite_nao_sai(
    resposta: httpx.Response | Exception, motivo: str
) -> None:
    assert enviar(PEDIDO, _gotrue(Auth(convite=resposta)), FRONT_URL) == (None, motivo)


def test_processa_a_fila_inteira_e_grava_cada_resultado(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fila = FilaFalsa([PEDIDO, Pedido(8, "davi@alfa.example", "Davi", False, False)])

    assert processar(fila, _gotrue(Auth()), FRONT_URL) == (1, 1, 0)

    assert fila.concluidos == [(7, ID_NOVO, None), (8, None, MSG_DESATIVADO)]
    # a saída do job não leva e-mail nem nome
    saida = capsys.readouterr()
    assert "convite 7: enviado" in saida.out
    assert "@" not in saida.out + saida.err
    assert "Carla" not in saida.out + saida.err


def test_resultado_que_nao_grava_sai_da_fila_como_falha(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # ex.: o id do Supabase Auth já está em outro cadastro (índice único)
    fila = FilaFalsa([PEDIDO], erros=[psycopg.errors.UniqueViolation("duplicado")])

    assert processar(fila, _gotrue(Auth()), FRONT_URL) == (0, 0, 1)

    # sem isso, a próxima rodada mandaria o mesmo e-mail de novo
    assert fila.concluidos == [(7, None, MSG_GRAVACAO)]
    assert "resultado não gravado (UniqueViolation)" in capsys.readouterr().err


def test_fila_vazia_nao_chama_o_supabase() -> None:
    auth = Auth()
    assert processar(FilaFalsa([]), _gotrue(auth), FRONT_URL) == (0, 0, 0)
    assert auth.pedidos == []


# --- a fila no banco (role carga) --------------------------------------------------------


@pytest.fixture
def conn() -> Iterator[psycopg.Connection[DictRow]]:
    with psycopg.connect(DATABASE_URL_ADMIN, autocommit=True, row_factory=dict_row) as c:
        yield c
        c.execute("reset role")
        c.execute("delete from seg.usuario where lower(email) = %s", (EMAIL,))


def _pedir_como_admin(conn: psycopg.Connection[DictRow], clientes: list[str]) -> UUID:
    """O que a rota da Gestão faz: a Dora (admin) pede o convite, como api_site."""
    with conn.transaction():
        conn.execute("set local role api_site")
        conn.execute("select set_config('app.usuario_id', %s, true)", (ADMIN,))
        linha = conn.execute(
            "select seg.pedir_convite(%s, 'Carla Alfa', 'cliente_usuario', null, %s::uuid[]) as id",
            (EMAIL, clientes),
        ).fetchone()
    assert linha is not None
    usuario_id: UUID = linha["id"]
    return usuario_id


@pytest.mark.db
def test_fila_pelo_role_carga_envia_e_grava_o_login(conn: psycopg.Connection[DictRow]) -> None:
    usuario_id = _pedir_como_admin(conn, [CLIENTE_A])
    antes = conn.execute(
        "select id_autenticacao, convidado_em, tipo from seg.usuario where id = %s", (usuario_id,)
    ).fetchone()
    # a pessoa existe no cadastro antes de existir no Supabase Auth
    assert antes == {"id_autenticacao": None, "convidado_em": None, "tipo": "cliente"}

    conn.execute("set role carga")
    fila = FilaPostgres(conn)
    (pedido,) = [p for p in fila.pendentes() if p.email == EMAIL]
    assert (pedido.nome, pedido.ativo, pedido.entrou) == ("Carla Alfa", True, False)
    # o carga não lê seg direto: só pelas duas funções
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("select count(*) from seg.convite_fila")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("select count(*) from seg.usuario")

    fila.concluir(pedido.convite_id, ID_NOVO, None)

    assert [p for p in fila.pendentes() if p.email == EMAIL] == []
    # o mesmo pedido não se conclui duas vezes
    with pytest.raises(psycopg.errors.RaiseException, match="não está na fila"):
        fila.concluir(pedido.convite_id, ID_NOVO, None)
    conn.execute("reset role")
    depois = conn.execute(
        "select u.id_autenticacao, u.convidado_em > now() - interval '1 minute' as recente,"
        "       f.estado, f.motivo, f.pedido_por::text, f.processado_em is not null as processado"
        "  from seg.usuario u join seg.convite_fila f on f.usuario_id = u.id where u.id = %s",
        (usuario_id,),
    ).fetchone()
    assert depois == {
        "id_autenticacao": ID_NOVO,
        "recente": True,
        "estado": "enviado",
        "motivo": None,
        "pedido_por": ADMIN,
        "processado": True,
    }


@pytest.mark.db
def test_fila_grava_a_falha_com_o_motivo_e_a_pessoa_segue_sem_login(
    conn: psycopg.Connection[DictRow],
) -> None:
    usuario_id = _pedir_como_admin(conn, [])

    conn.execute("set role carga")
    fila = FilaPostgres(conn)
    (pedido,) = [p for p in fila.pendentes() if p.email == EMAIL]
    # enviado sem o id do Supabase Auth não existe
    with pytest.raises(psycopg.errors.RaiseException, match="precisa do id"):
        fila.concluir(pedido.convite_id, None, None)
    fila.concluir(pedido.convite_id, None, MSG_LIMITE)
    conn.execute("reset role")

    depois = conn.execute(
        "select u.id_autenticacao, u.convidado_em, f.estado, f.motivo"
        "  from seg.usuario u join seg.convite_fila f on f.usuario_id = u.id where u.id = %s",
        (usuario_id,),
    ).fetchone()
    assert depois == {
        "id_autenticacao": None,
        "convidado_em": None,
        "estado": "falhou",
        "motivo": MSG_LIMITE,
    }

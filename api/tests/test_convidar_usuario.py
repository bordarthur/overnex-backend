"""CLI de cadastro (app.cli.convidar_usuario): orquestração sem banco e SQL com banco."""

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import httpx
import psycopg
import pytest
from psycopg.rows import DictRow, dict_row

from app.auth.gotrue import GoTrue
from app.cli.convidar_usuario import (
    BancoPostgres,
    CadastroInvalido,
    Pedido,
    Plano,
    _pedido,
    convidar,
)
from tests.conftest import CLIENTE_A, DATABASE_URL_ADMIN, EMPRESA_A1, EMPRESA_A2

SUPABASE_URL = "https://projeto-teste.supabase.co"
FRONT_URL = "https://overnex.teste"
ID_NOVO = UUID("5b000000-0000-4000-8000-0000000000c1")
PERFIL_A = UUID("f1000000-0000-4000-8000-00000000000a")

PEDIDO = Pedido(
    email="carla@alfa.example",
    nome="Carla Alfa",
    cliente="padrao-lann-alfa",
    papel="leitor",
    perfis=("Financeiro",),
    empresas=None,
    tipo="cliente",
)
PLANO = Plano(
    cliente_id=UUID(CLIENTE_A),
    perfis={"Financeiro": PERFIL_A},
    empresas={"Alfa Comércio": UUID(EMPRESA_A2), "Alfa Indústria": UUID(EMPRESA_A1)},
)


@dataclass
class BancoFalso:
    erro_planejar: Exception | None = None
    erro_gravar: Exception | None = None
    gravados: list[UUID] = field(default_factory=list)

    def planejar(self, pedido: Pedido) -> Plano:
        if self.erro_planejar:
            raise self.erro_planejar
        return PLANO

    def gravar(self, pedido: Pedido, plano: Plano, id_autenticacao: UUID) -> UUID:
        if self.erro_gravar:
            raise self.erro_gravar
        self.gravados.append(id_autenticacao)
        return UUID("d1000000-0000-4000-8000-0000000000c1")


@dataclass
class Admin:
    convite: httpx.Response | Exception = field(
        default_factory=lambda: httpx.Response(200, json={"id": str(ID_NOVO)})
    )
    pedidos: list[httpx.Request] = field(default_factory=list)

    def __call__(self, pedido: httpx.Request) -> httpx.Response:
        self.pedidos.append(pedido)
        if pedido.url.path == "/auth/v1/invite":
            if isinstance(self.convite, Exception):
                raise self.convite
            return self.convite
        if pedido.method == "DELETE":
            return httpx.Response(200, json={})
        return httpx.Response(404)


def _gotrue(admin: Admin, chave: str = "sb_secret_teste") -> GoTrue:
    return GoTrue(SUPABASE_URL, chave, transport=httpx.MockTransport(admin))


# --- orquestração (sem banco) -----------------------------------------------------------


def test_convida_e_grava() -> None:
    admin, banco = Admin(), BancoFalso()

    usuario_id, plano = convidar(PEDIDO, banco, _gotrue(admin), FRONT_URL)

    assert usuario_id == UUID("d1000000-0000-4000-8000-0000000000c1")
    assert plano == PLANO
    assert banco.gravados == [ID_NOVO]
    (pedido,) = admin.pedidos
    assert pedido.method == "POST"
    assert pedido.url.params["redirect_to"] == f"{FRONT_URL}/definir-senha"
    assert json.loads(pedido.content) == {
        "email": "carla@alfa.example",
        "data": {"nome": "Carla Alfa"},
    }
    assert pedido.headers["apikey"] == "sb_secret_teste"
    # chave nova (sb_secret_…) não é JWT: só no apikey
    assert "authorization" not in pedido.headers


def test_chave_service_role_legada_vai_tambem_no_authorization() -> None:
    admin = Admin()
    convidar(PEDIDO, BancoFalso(), _gotrue(admin, chave="eyJlegado.x.y"), FRONT_URL)
    assert admin.pedidos[0].headers["authorization"] == "Bearer eyJlegado.x.y"


def test_email_ja_cadastrado_no_overnex_nao_convida() -> None:
    admin = Admin()
    banco = BancoFalso(erro_planejar=CadastroInvalido("já está cadastrado"))

    with pytest.raises(CadastroInvalido, match="já está cadastrado"):
        convidar(PEDIDO, banco, _gotrue(admin), FRONT_URL)
    assert admin.pedidos == []


@pytest.mark.parametrize(
    ("resposta", "mensagem"),
    [
        (
            httpx.Response(
                422,
                json={
                    "code": 422,
                    "error_code": "email_exists",
                    "msg": "A user with this email address has already been registered",
                },
            ),
            "já existe no Supabase Auth",
        ),
        (
            httpx.Response(400, json={"code": 400, "error_code": "validation_failed"}),
            "recusou o convite",
        ),
        (httpx.Response(500), "indisponível"),
        (httpx.ConnectError("recusada"), "indisponível"),
    ],
)
def test_convite_falhou_nao_grava_nada(resposta: httpx.Response | Exception, mensagem: str) -> None:
    banco = BancoFalso()

    with pytest.raises(CadastroInvalido, match=mensagem):
        convidar(PEDIDO, banco, _gotrue(Admin(convite=resposta)), FRONT_URL)
    assert banco.gravados == []


def test_gravacao_falhou_apaga_o_usuario_do_auth() -> None:
    admin = Admin()
    banco = BancoFalso(erro_gravar=psycopg.errors.UniqueViolation("duplicado"))

    with pytest.raises(psycopg.errors.UniqueViolation):
        convidar(PEDIDO, banco, _gotrue(admin), FRONT_URL)
    apagou = [p for p in admin.pedidos if p.method == "DELETE"]
    assert [p.url.path for p in apagou] == [f"/auth/v1/admin/users/{ID_NOVO}"]


# --- argumentos ------------------------------------------------------------------------


def _args(*extra: str) -> list[str]:
    base = ["--email", " Carla@Alfa.example ", "--nome", "Carla Alfa"]
    base += ["--cliente", "padrao-lann-alfa", "--papel", "leitor", "--perfil", "Financeiro"]
    return base + list(extra)


def test_argumentos_padrao_todas_as_empresas() -> None:
    pedido = _pedido(_args())
    assert pedido.email == "carla@alfa.example"
    assert pedido.empresas is None
    assert pedido.tipo == "cliente"
    assert pedido.perfis == ("Financeiro",)


def test_argumentos_empresas_e_perfis_repetidos() -> None:
    pedido = _pedido(
        _args("--perfil", "Comercial", "--perfil", "Financeiro", "--empresas", "Alfa Indústria, ")
    )
    assert pedido.perfis == ("Financeiro", "Comercial")
    assert pedido.empresas == ("Alfa Indústria",)


@pytest.mark.parametrize("extra", [("--papel", "dono"), ("--tipo", "outro"), ("--empresas", " , ")])
def test_argumentos_invalidos(extra: tuple[str, ...]) -> None:
    with pytest.raises(SystemExit):
        _pedido(_args(*extra))


# --- SQL do cadastro (banco local) -----------------------------------------------------


@pytest.fixture
def conn_admin() -> Iterator[psycopg.Connection[DictRow]]:
    with psycopg.connect(DATABASE_URL_ADMIN, autocommit=True, row_factory=dict_row) as conn:
        yield conn
        conn.execute("delete from seg.usuario where lower(email) = %s", (PEDIDO.email,))


@pytest.mark.db
def test_banco_planeja_e_grava(conn_admin: psycopg.Connection[DictRow]) -> None:
    banco = BancoPostgres(conn_admin)

    plano = banco.planejar(PEDIDO)
    assert plano == PLANO
    usuario_id = banco.gravar(PEDIDO, plano, ID_NOVO)

    usuario = conn_admin.execute(
        "select id_autenticacao, email, nome, tipo, ativo from seg.usuario where id = %s",
        (usuario_id,),
    ).fetchone()
    assert usuario == {
        "id_autenticacao": ID_NOVO,
        "email": "carla@alfa.example",
        "nome": "Carla Alfa",
        "tipo": "cliente",
        "ativo": True,
    }
    vinculo = conn_admin.execute(
        "select cliente_id, papel from seg.usuario_cliente where usuario_id = %s", (usuario_id,)
    ).fetchall()
    assert vinculo == [{"cliente_id": UUID(CLIENTE_A), "papel": "leitor"}]
    perfis = conn_admin.execute(
        "select perfil_id from seg.usuario_perfil where usuario_id = %s", (usuario_id,)
    ).fetchall()
    assert perfis == [{"perfil_id": PERFIL_A}]
    empresas = conn_admin.execute(
        "select empresa_id from seg.usuario_empresa where usuario_id = %s order by empresa_id",
        (usuario_id,),
    ).fetchall()
    assert empresas == [{"empresa_id": UUID(EMPRESA_A1)}, {"empresa_id": UUID(EMPRESA_A2)}]

    # de novo: o e-mail já existe
    with pytest.raises(CadastroInvalido, match="já está cadastrado"):
        banco.planejar(PEDIDO)


@pytest.mark.db
@pytest.mark.parametrize(
    ("mudanca", "mensagem"),
    [
        ({"email": "ANA@alfa.example"}, "já está cadastrado"),
        ({"cliente": "nao-existe"}, "Cliente 'nao-existe' não encontrado"),
        ({"perfis": ("Financeiro", "Diretoria")}, "Perfil não encontrado.*Diretoria"),
        ({"empresas": ("Beta Serviços",)}, "Empresa não encontrada.*Beta Serviços"),
    ],
)
def test_banco_recusa_pedido_invalido(
    conn_admin: psycopg.Connection[DictRow], mudanca: dict[str, Any], mensagem: str
) -> None:
    pedido = Pedido(**(PEDIDO.__dict__ | mudanca))
    with pytest.raises(CadastroInvalido, match=mensagem):
        BancoPostgres(conn_admin).planejar(pedido)


@pytest.mark.db
def test_banco_gravacao_e_atomica(conn_admin: psycopg.Connection[DictRow]) -> None:
    banco = BancoPostgres(conn_admin)
    # perfil de outro cliente: a FK composta recusa no meio da transação
    plano = Plano(
        cliente_id=PLANO.cliente_id,
        perfis={"Financeiro B": UUID("f1000000-0000-4000-8000-00000000000b")},
        empresas=PLANO.empresas,
    )

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        banco.gravar(PEDIDO, plano, ID_NOVO)
    sobrou = conn_admin.execute(
        "select count(*) as n from seg.usuario where lower(email) = %s", (PEDIDO.email,)
    ).fetchone()
    assert sobrou == {"n": 0}

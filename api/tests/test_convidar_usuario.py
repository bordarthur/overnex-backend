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
    Pendente,
    Plano,
    _pedido,
    conferir_admin,
    convidar,
    reenviar,
)
from tests.conftest import CLIENTE_A, CLIENTE_B, DATABASE_URL_ADMIN, EMPRESA_A1, EMPRESA_A2

SUPABASE_URL = "https://projeto-teste.supabase.co"
FRONT_URL = "https://overnex.teste"
ID_NOVO = UUID("5b000000-0000-4000-8000-0000000000c1")
USUARIO_NOVO = UUID("d1000000-0000-4000-8000-0000000000c1")

PEDIDO = Pedido(
    email="carla@alfa.example",
    nome="Carla Alfa",
    perfil="cliente_usuario",
    clientes=("padrao-lann-alfa",),
)
PLANO = Plano(clientes={"padrao-lann-alfa": UUID(CLIENTE_A)})


@dataclass
class BancoFalso:
    erro_planejar: Exception | None = None
    erro_gravar: Exception | None = None
    erro_pendente: Exception | None = None
    gravados: list[UUID] = field(default_factory=list)
    reenviados: list[UUID] = field(default_factory=list)

    def planejar(self, pedido: Pedido) -> Plano:
        if self.erro_planejar:
            raise self.erro_planejar
        return PLANO

    def gravar(self, pedido: Pedido, plano: Plano, id_autenticacao: UUID) -> UUID:
        if self.erro_gravar:
            raise self.erro_gravar
        self.gravados.append(id_autenticacao)
        return USUARIO_NOVO

    def pendente(self, email: str) -> Pendente:
        if self.erro_pendente:
            raise self.erro_pendente
        return Pendente(usuario_id=USUARIO_NOVO, nome="Carla Alfa")

    def marcar_convite(self, usuario_id: UUID) -> None:
        self.reenviados.append(usuario_id)


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

    assert usuario_id == USUARIO_NOVO
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


def test_reenvia_o_convite_de_quem_ainda_nao_entrou() -> None:
    admin, banco = Admin(), BancoFalso()

    assert reenviar("carla@alfa.example", banco, _gotrue(admin), FRONT_URL) == USUARIO_NOVO

    (pedido,) = admin.pedidos
    assert pedido.url.path == "/auth/v1/invite"
    assert json.loads(pedido.content)["email"] == "carla@alfa.example"
    # a data do convite recomeça (a Gestão mostra "Convite enviado" de novo)
    assert banco.reenviados == [USUARIO_NOVO]


def test_nao_reenvia_para_quem_ja_entrou_ou_nao_existe() -> None:
    admin = Admin()
    banco = BancoFalso(erro_pendente=CadastroInvalido("já entrou no Overnex"))

    with pytest.raises(CadastroInvalido, match="já entrou"):
        reenviar("ana@alfa.example", banco, _gotrue(admin), FRONT_URL)
    assert admin.pedidos == []
    assert banco.reenviados == []


def test_reenvio_recusado_pelo_supabase_nao_marca_o_convite() -> None:
    banco = BancoFalso()
    recusa = httpx.Response(422, json={"code": 422, "error_code": "email_exists"})

    with pytest.raises(CadastroInvalido, match="já existe no Supabase Auth"):
        reenviar("carla@alfa.example", banco, _gotrue(Admin(convite=recusa)), FRONT_URL)
    assert banco.reenviados == []


# --- argumentos ------------------------------------------------------------------------


def _args(*extra: str) -> list[str]:
    base = ["--email", " Carla@Alfa.example ", "--nome", "Carla Alfa"]
    return base + list(extra)


def test_argumentos_de_um_cliente_usuario() -> None:
    pedido = _pedido(_args("--perfil", "cliente_usuario", "--cliente", "padrao-lann-alfa"))
    assert pedido == PEDIDO
    assert isinstance(pedido, Pedido)
    assert pedido.tipo == "cliente"
    assert pedido.sessao is None


def test_argumentos_de_consultor_com_varios_clientes_e_sessao() -> None:
    pedido = _pedido(
        _args(
            "--perfil",
            "consultor",
            "--cliente",
            "padrao-lann-alfa",
            "--cliente",
            "padrao-lann-beta",
            "--cliente",
            "padrao-lann-alfa",
            "--sessao",
            "720",
        )
    )
    assert isinstance(pedido, Pedido)
    assert pedido.clientes == ("padrao-lann-alfa", "padrao-lann-beta")
    assert (pedido.sessao, pedido.tipo) == (720, "lann")


def test_argumentos_de_admin_sem_cliente() -> None:
    pedido = _pedido(
        ["--email", "fulano@bordgestao.com.br", "--nome", "Fulano", "--perfil", "admin"]
    )
    assert isinstance(pedido, Pedido)
    assert (pedido.perfil, pedido.clientes, pedido.tipo) == ("admin", (), "lann")
    conferir_admin(pedido)


def test_argumentos_de_reenvio_so_o_email() -> None:
    assert _pedido(["--email", " Carla@Alfa.example ", "--reenviar"]) == "carla@alfa.example"


@pytest.mark.parametrize(
    "extra",
    [
        ("--perfil", "dono", "--cliente", "padrao-lann-alfa"),
        # perfil de cliente sem cliente
        ("--perfil", "cliente_usuario"),
        # o admin vê todas: não leva cliente
        ("--perfil", "admin", "--cliente", "padrao-lann-alfa"),
        # mais de 12 h só para o admin
        ("--perfil", "cliente_principal", "--cliente", "padrao-lann-alfa", "--sessao", "1440"),
        ("--perfil", "cliente_principal", "--cliente", "padrao-lann-alfa", "--sessao", "45"),
        ("--cliente", "padrao-lann-alfa"),
        ("--perfil", "cliente_usuario", "--cliente", "padrao-lann-alfa", "--reenviar"),
    ],
)
def test_argumentos_invalidos(extra: tuple[str, ...]) -> None:
    with pytest.raises(SystemExit):
        _pedido(_args(*extra))


def test_admin_so_com_email_da_bord() -> None:
    pedido = Pedido(email="fulano@cliente.com.br", nome="Fulano", perfil="admin", clientes=())
    with pytest.raises(CadastroInvalido, match="Admin BORD só com e-mail da BORD"):
        conferir_admin(pedido)
    conferir_admin(pedido, "bordgestao.com.br, cliente.com.br")
    # os outros perfis e o reenvio não têm essa regra
    conferir_admin(PEDIDO)
    conferir_admin("fulano@cliente.com.br")


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
        "select id_autenticacao, email, nome, tipo, perfil, sessao_minutos, ativo,"
        " convidado_em is not null as convidado, primeiro_acesso_em"
        " from seg.usuario where id = %s",
        (usuario_id,),
    ).fetchone()
    assert usuario == {
        "id_autenticacao": ID_NOVO,
        "email": "carla@alfa.example",
        "nome": "Carla Alfa",
        "tipo": "cliente",
        "perfil": "cliente_usuario",
        "sessao_minutos": None,
        "ativo": True,
        "convidado": True,
        "primeiro_acesso_em": None,
    }
    vinculo = conn_admin.execute(
        "select cliente_id, papel from seg.usuario_cliente where usuario_id = %s", (usuario_id,)
    ).fetchall()
    # o papel é o do perfil (Cliente usuário = leitor)
    assert vinculo == [{"cliente_id": UUID(CLIENTE_A), "papel": "leitor"}]
    perfis = conn_admin.execute(
        "select p.cliente_id, p.perfil from seg.usuario_perfil up"
        " join cfg.perfil_acesso p on p.id = up.perfil_id where up.usuario_id = %s",
        (usuario_id,),
    ).fetchall()
    # o perfil fixo do cliente
    assert perfis == [{"cliente_id": UUID(CLIENTE_A), "perfil": "cliente_usuario"}]
    empresas = conn_admin.execute(
        "select empresa_id from seg.usuario_empresa where usuario_id = %s order by empresa_id",
        (usuario_id,),
    ).fetchall()
    assert empresas == [{"empresa_id": UUID(EMPRESA_A1)}, {"empresa_id": UUID(EMPRESA_A2)}]

    # de novo: o e-mail já existe
    with pytest.raises(CadastroInvalido, match="já está cadastrado"):
        banco.planejar(PEDIDO)

    # ainda não entrou: pode reenviar, e a data do convite recomeça
    pendente = banco.pendente("Carla@alfa.example")
    assert pendente == Pendente(usuario_id=usuario_id, nome="Carla Alfa")
    conn_admin.execute(
        "update seg.usuario set convidado_em = now() - interval '3 days' where id = %s",
        (usuario_id,),
    )
    banco.marcar_convite(usuario_id)
    recente = conn_admin.execute(
        "select convidado_em > now() - interval '1 minute' as recente from seg.usuario"
        " where id = %s",
        (usuario_id,),
    ).fetchone()
    assert recente == {"recente": True}


@pytest.mark.db
def test_banco_grava_consultor_com_dois_clientes(conn_admin: psycopg.Connection[DictRow]) -> None:
    pedido = Pedido(
        email=PEDIDO.email,
        nome="Carla Alfa",
        perfil="consultor",
        clientes=("padrao-lann-alfa", "padrao-lann-beta"),
        sessao=720,
    )
    banco = BancoPostgres(conn_admin)

    usuario_id = banco.gravar(pedido, banco.planejar(pedido), ID_NOVO)

    vinculos = conn_admin.execute(
        "select cliente_id, papel from seg.usuario_cliente where usuario_id = %s"
        " order by cliente_id",
        (usuario_id,),
    ).fetchall()
    assert vinculos == [
        {"cliente_id": UUID(CLIENTE_A), "papel": "gestor"},
        {"cliente_id": UUID(CLIENTE_B), "papel": "gestor"},
    ]
    resumo = conn_admin.execute(
        "select u.tipo, u.sessao_minutos,"
        " (select count(*) from seg.usuario_empresa ue where ue.usuario_id = u.id) as empresas,"
        " (select count(*) from seg.usuario_perfil up"
        "    join cfg.perfil_acesso p on p.id = up.perfil_id and p.perfil = 'consultor'"
        "   where up.usuario_id = u.id) as perfis"
        " from seg.usuario u where u.id = %s",
        (usuario_id,),
    ).fetchone()
    assert resumo == {"tipo": "lann", "sessao_minutos": 720, "empresas": 3, "perfis": 2}


@pytest.mark.db
def test_banco_grava_admin_sem_vinculo(conn_admin: psycopg.Connection[DictRow]) -> None:
    pedido = Pedido(email=PEDIDO.email, nome="Carla Alfa", perfil="admin", clientes=())
    banco = BancoPostgres(conn_admin)

    usuario_id = banco.gravar(pedido, banco.planejar(pedido), ID_NOVO)

    resumo = conn_admin.execute(
        "select u.perfil,"
        " (select count(*) from seg.usuario_cliente uc where uc.usuario_id = u.id) as vinculos"
        " from seg.usuario u where u.id = %s",
        (usuario_id,),
    ).fetchone()
    assert resumo == {"perfil": "admin", "vinculos": 0}


@pytest.mark.db
@pytest.mark.parametrize(
    ("mudanca", "mensagem"),
    [
        ({"email": "ANA@alfa.example"}, "já está cadastrado"),
        ({"clientes": ("nao-existe",)}, "Cliente 'nao-existe' não encontrado"),
        (
            {"clientes": ("padrao-lann-alfa", "outro-que-nao-existe")},
            "Cliente 'outro-que-nao-existe' não encontrado",
        ),
    ],
)
def test_banco_recusa_pedido_invalido(
    conn_admin: psycopg.Connection[DictRow], mudanca: dict[str, Any], mensagem: str
) -> None:
    pedido = Pedido(**(PEDIDO.__dict__ | mudanca))
    with pytest.raises(CadastroInvalido, match=mensagem):
        BancoPostgres(conn_admin).planejar(pedido)


@pytest.mark.db
@pytest.mark.parametrize(
    ("email", "mensagem"),
    [
        ("ninguem@alfa.example", "não está cadastrado"),
        # o seed não tem primeiro acesso: marca um para a Ana
        ("ana@alfa.example", "já entrou no Overnex"),
    ],
)
def test_banco_nao_acha_pendente(
    conn_admin: psycopg.Connection[DictRow], email: str, mensagem: str
) -> None:
    conn_admin.execute(
        "update seg.usuario set primeiro_acesso_em = now() where email = 'ana@alfa.example'"
    )
    try:
        with pytest.raises(CadastroInvalido, match=mensagem):
            BancoPostgres(conn_admin).pendente(email)
    finally:
        conn_admin.execute(
            "update seg.usuario set primeiro_acesso_em = null where email = 'ana@alfa.example'"
        )


@pytest.mark.db
def test_banco_gravacao_e_atomica(conn_admin: psycopg.Connection[DictRow]) -> None:
    banco = BancoPostgres(conn_admin)
    # cliente que não existe: a FK recusa no meio da transação, depois do insert do usuário
    plano = Plano(clientes={"fantasma": UUID("c1000000-0000-4000-8000-0000000000ff")})

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        banco.gravar(PEDIDO, plano, ID_NOVO)
    sobrou = conn_admin.execute(
        "select count(*) as n from seg.usuario where lower(email) = %s", (PEDIDO.email,)
    ).fetchone()
    assert sobrou == {"n": 0}

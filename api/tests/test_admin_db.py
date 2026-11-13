"""Admin BORD, consultor e cliente escolhido, de ponta a ponta contra o banco local (seed
"Padrão LANN": Ana em A, Bruno em B, Caio consultor nos dois, Dora admin; migrations 25 a 28).

O que só o banco prova: o RLS mostra um cliente por vez, o admin lê e grava o cadastro de
todos e quem não é admin não ganha nada com o slug.
"""

from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from tests.conftest import (
    CLIENTE_A,
    CLIENTE_B,
    OID_A,
    OID_ADMIN,
    OID_B,
    OID_CONSULTOR,
    SLUG_A,
    SLUG_B,
    USUARIO_B,
    auth,
)

pytestmark = pytest.mark.db

# as 9 telas ligadas em cada cliente do seed (o admin vê todas as ligadas)
TELAS_DO_SEED = [
    "COM-01",
    "COM-02",
    "COM-03",
    "FIN-01",
    "FIN-02",
    "FIN-03",
    "FIN-04",
    "PES-01",
    "VG-01",
]


def _sessao(client: TestClient, oid: str, slug: str | None = None) -> Any:
    return client.get("/api/sessao", params={"cliente": slug} if slug else None, headers=auth(oid))


# ---------- cliente escolhido ----------


def test_admin_sem_cliente_recebe_a_lista_e_a_gestao(client: TestClient) -> None:
    corpo = _sessao(client, OID_ADMIN).json()

    assert (corpo["gestao"], corpo["perfil"]["id"], corpo["cliente"]) == (True, "admin", None)
    assert [c["slug"] for c in corpo["clientes"]] == [SLUG_A, SLUG_B]
    assert (corpo["empresas"], corpo["telas"], corpo["periodos"]) == ([], [], [])


@pytest.mark.parametrize(("slug", "empresas"), [(SLUG_A, 2), (SLUG_B, 1)])
def test_admin_abre_qualquer_cliente_pelo_slug(
    client: TestClient, slug: str, empresas: int
) -> None:
    corpo = _sessao(client, OID_ADMIN, slug).json()

    assert (corpo["cliente"]["slug"], corpo["papel"]) == (slug, "admin")
    # todas as empresas do cliente e todas as telas ligadas nele, sem vínculo nem perfil
    assert len(corpo["empresas"]) == empresas
    assert corpo["telas"] == TELAS_DO_SEED


def test_admin_so_abre_tela_com_o_cliente_escolhido(client: TestClient) -> None:
    assert client.get("/api/telas/visao-geral", headers=auth(OID_ADMIN)).status_code == 403
    for slug, nome in ((SLUG_A, "Padrão LANN Alfa"), (SLUG_B, "Padrão LANN Beta")):
        r = client.get("/api/telas/visao-geral", params={"cliente": slug}, headers=auth(OID_ADMIN))
        assert r.status_code == 200
        assert r.json()["cliente"]["nome"] == nome


def test_consultor_escolhe_entre_os_clientes_da_carteira(client: TestClient) -> None:
    sem_slug = _sessao(client, OID_CONSULTOR).json()
    assert (sem_slug["gestao"], sem_slug["cliente"]) == (False, None)
    assert [c["slug"] for c in sem_slug["clientes"]] == [SLUG_A, SLUG_B]

    em_b = _sessao(client, OID_CONSULTOR, SLUG_B).json()
    assert (em_b["cliente"]["slug"], em_b["papel"]) == (SLUG_B, "gestor")
    # o perfil fixo de consultor do seed só libera duas telas
    assert em_b["telas"] == ["COM-01", "VG-01"]

    telas = "/api/telas/"
    cabecalho = auth(OID_CONSULTOR)
    assert client.get(telas + "visao-geral", headers=cabecalho).status_code == 403
    assert (
        client.get(telas + "visao-geral", params={"cliente": SLUG_B}, headers=cabecalho).status_code
        == 200
    )
    assert (
        client.get(telas + "resultado", params={"cliente": SLUG_B}, headers=cabecalho).status_code
        == 403
    )


def test_cliente_de_um_vinculo_nao_abre_outro_cliente(client: TestClient) -> None:
    assert _sessao(client, OID_A, SLUG_B).status_code == 403
    r = client.get("/api/telas/visao-geral", params={"cliente": SLUG_B}, headers=auth(OID_A))
    assert r.status_code == 403
    assert r.json() == {"detail": "Acesso negado."}

    # com o próprio slug (o front passa a mandar sempre) ou sem ele: igual a antes
    assert _sessao(client, OID_A, SLUG_A).json() == _sessao(client, OID_A).json()
    assert _sessao(client, OID_A).json()["clientes"] == [_sessao(client, OID_A).json()["cliente"]]


def test_log_de_acesso_leva_o_cliente_escolhido(
    client: TestClient, admin_conn: psycopg.Connection[tuple[object, ...]]
) -> None:
    _sessao(client, OID_ADMIN, SLUG_B)
    linha = admin_conn.execute(
        "select cliente_id::text, codigo_tela, resultado from seg.log_acesso"
        " where id_autenticacao = %s order by id desc limit 1",
        (OID_ADMIN,),
    ).fetchone()
    assert linha == (CLIENTE_B, "sessao", "permitido")


# ---------- Gestão ----------


def test_gestao_so_para_o_admin(client: TestClient) -> None:
    for rota in ("/api/admin/gestao", "/api/admin/usuarios", f"/api/admin/clientes/{SLUG_A}"):
        for oid in (OID_A, OID_CONSULTOR):
            assert client.get(rota, headers=auth(oid)).status_code == 403
        assert client.get(rota, headers=auth(OID_ADMIN)).status_code == 200
    r = client.get("/api/admin/clientes/nao-existe", headers=auth(OID_ADMIN))
    assert (r.status_code, r.json()) == (404, {"detail": "Empresa não encontrada."})


def test_gestao_le_o_cadastro_de_todos_os_clientes(client: TestClient) -> None:
    gestao = client.get("/api/admin/gestao", headers=auth(OID_ADMIN)).json()
    assert [c["slug"] for c in gestao["clientes"]] == [SLUG_A, SLUG_B]
    assert {p["id"]: p["sessaoPadraoMinutos"] for p in gestao["perfis"]} == {
        "admin": 1440,
        "consultor": 480,
        "cliente_principal": 240,
        "cliente_usuario": 240,
    }

    pessoas = client.get("/api/admin/usuarios", headers=auth(OID_ADMIN)).json()["usuarios"]
    assert {p["email"] for p in pessoas} >= {
        "ana@alfa.example",
        "bruno@beta.example",
        "caio@consultoria.example",
        "dora@bordgestao.com.br",
    }

    alfa = client.get(f"/api/admin/clientes/{SLUG_A}", headers=auth(OID_ADMIN)).json()
    assert alfa["cliente"]["slug"] == SLUG_A
    # só quem tem vínculo com A; a admin não aparece
    assert {u["email"] for u in alfa["usuarios"]} == {
        "ana@alfa.example",
        "caio@consultoria.example",
    }
    # o seed não tem carga das tabelas dos painéis: todos indisponíveis, e os do seed ligados
    assert {p["codigo"] for p in alfa["paineis"] if p["ligado"]} == set(TELAS_DO_SEED) - {"FIN-01"}
    assert not any(p["podeLigar"] for p in alfa["paineis"])


@pytest.fixture
def bruno_de_volta(admin_conn: psycopg.Connection[tuple[object, ...]]) -> Iterator[None]:
    """Devolve o Bruno ao que o seed grava: Cliente usuário de B, no perfil livre."""
    yield
    admin_conn.execute(
        "update seg.usuario set nome = 'Bruno Beta', perfil = 'cliente_usuario', ativo = true,"
        " sessao_minutos = null, desativado_em = null where id = %s",
        (USUARIO_B,),
    )
    admin_conn.execute("select seg.definir_vinculos(%s, %s::uuid[])", (USUARIO_B, [CLIENTE_B]))
    admin_conn.execute("delete from seg.usuario_perfil where usuario_id = %s", (USUARIO_B,))
    admin_conn.execute(
        "insert into seg.usuario_perfil (usuario_id, cliente_id, perfil_id)"
        " values (%s, %s, 'f1000000-0000-4000-8000-00000000000b')",
        (USUARIO_B, CLIENTE_B),
    )
    admin_conn.execute(
        "delete from seg.log_alteracao where tabela = 'seg.usuario' and registro_id = %s",
        (USUARIO_B,),
    )


@pytest.mark.usefixtures("bruno_de_volta")
def test_admin_muda_perfil_empresas_e_sessao_de_uma_pessoa(
    client: TestClient, admin_conn: psycopg.Connection[tuple[object, ...]]
) -> None:
    r = client.put(
        f"/api/admin/usuarios/{USUARIO_B}",
        json={
            "nome": "Bruno B.",
            "perfil": "cliente_principal",
            "clientes": [SLUG_A, SLUG_B],
            "sessaoMinutos": 720,
        },
        headers=auth(OID_ADMIN),
    )
    assert r.status_code == 204

    assert admin_conn.execute(
        "select nome, perfil, sessao_minutos from seg.usuario where id = %s", (USUARIO_B,)
    ).fetchone() == ("Bruno B.", "cliente_principal", 720)
    assert admin_conn.execute(
        "select cliente_id::text, papel from seg.usuario_cliente where usuario_id = %s order by 1",
        (USUARIO_B,),
    ).fetchall() == [(CLIENTE_A, "gestor"), (CLIENTE_B, "gestor")]
    # agora o Bruno escolhe entre os dois clientes
    assert [c["slug"] for c in _sessao(client, OID_B).json()["clientes"]] == [SLUG_A, SLUG_B]
    assert admin_conn.execute(
        "select count(*) from seg.log_alteracao where tabela = 'seg.usuario' and registro_id = %s",
        (USUARIO_B,),
    ).fetchone() == (1,)

    # regras: mais de 12 h só para o admin, e admin só com e-mail da BORD
    corpo = {"nome": "Bruno", "perfil": "cliente_usuario", "clientes": [SLUG_B]}
    longo = client.put(
        f"/api/admin/usuarios/{USUARIO_B}",
        json=corpo | {"sessaoMinutos": 1440},
        headers=auth(OID_ADMIN),
    )
    assert (longo.status_code, longo.json()) == (422, {"detail": "Mais de 12 h, só Admin BORD."})
    admin = client.put(
        f"/api/admin/usuarios/{USUARIO_B}",
        json=corpo | {"perfil": "admin"},
        headers=auth(OID_ADMIN),
    )
    assert (admin.status_code, admin.json()) == (
        422,
        {"detail": "Admin BORD só com e-mail da BORD."},
    )


@pytest.mark.usefixtures("bruno_de_volta")
def test_desativar_derruba_o_acesso_e_reativar_devolve(client: TestClient) -> None:
    rota = f"/api/admin/usuarios/{USUARIO_B}/acesso"

    assert client.put(rota, json={"ativo": False}, headers=auth(OID_ADMIN)).status_code == 204
    assert _sessao(client, OID_B).status_code == 403

    assert client.put(rota, json={"ativo": True}, headers=auth(OID_ADMIN)).status_code == 204
    assert _sessao(client, OID_B).status_code == 200

    # a única admin não desativa a si mesma
    dora = client.get("/api/admin/usuarios", headers=auth(OID_ADMIN)).json()["usuarios"]
    dora_id = next(u["id"] for u in dora if u["email"] == "dora@bordgestao.com.br")
    r = client.put(
        f"/api/admin/usuarios/{dora_id}/acesso", json={"ativo": False}, headers=auth(OID_ADMIN)
    )
    assert r.status_code == 422


@pytest.fixture
def paineis_de_volta(admin_conn: psycopg.Connection[tuple[object, ...]]) -> Iterator[None]:
    yield
    admin_conn.execute(
        "update cfg.cliente_tela set ligada = true where cliente_id = %s and codigo_tela = 'VG-01'",
        (CLIENTE_B,),
    )
    admin_conn.execute(
        "delete from cfg.perfil_tela pt using cfg.perfil_acesso p"
        " where p.id = pt.perfil_id and p.cliente_id = %s and p.perfil = 'cliente_usuario'",
        (CLIENTE_B,),
    )
    admin_conn.execute("delete from cfg.feriado where cliente_id = %s", (CLIENTE_B,))
    admin_conn.execute(
        "delete from seg.log_alteracao where registro_id like %s", (f"{CLIENTE_B}/%",)
    )


@pytest.mark.usefixtures("paineis_de_volta")
def test_painel_desligado_some_para_todo_mundo(client: TestClient) -> None:
    rota = f"/api/admin/clientes/{SLUG_B}/paineis/VG-01"

    assert client.put(rota, json={"ligado": False}, headers=auth(OID_ADMIN)).status_code == 204

    assert "VG-01" not in _sessao(client, OID_B).json()["telas"]
    assert client.get("/api/telas/visao-geral", headers=auth(OID_B)).status_code == 403
    r = client.get("/api/telas/visao-geral", params={"cliente": SLUG_B}, headers=auth(OID_ADMIN))
    assert r.status_code == 403
    # o seed não tem carga de vendas: painel sem dado não religa pela Gestão
    religar = client.put(rota, json={"ligado": True}, headers=auth(OID_ADMIN))
    assert (religar.status_code, religar.json()) == (
        422,
        {"detail": "Este painel ainda não tem dado nesta empresa."},
    )


@pytest.mark.usefixtures("paineis_de_volta")
def test_matriz_e_feriado_pela_gestao(
    client: TestClient, admin_conn: psycopg.Connection[tuple[object, ...]]
) -> None:
    base = f"/api/admin/clientes/{SLUG_B}"
    cabecalho = auth(OID_ADMIN)

    r = client.put(
        f"{base}/perfis/cliente_usuario/paineis/COM-02", json={"ve": True}, headers=cabecalho
    )
    assert r.status_code == 204
    # Equipe comercial mostra nomes: a linha nasce com dado nominal
    assert admin_conn.execute(
        "select pt.ve_dado_nominal from cfg.perfil_tela pt"
        " join cfg.perfil_acesso p on p.id = pt.perfil_id"
        " where p.cliente_id = %s and p.perfil = 'cliente_usuario' and pt.codigo_tela = 'COM-02'",
        (CLIENTE_B,),
    ).fetchone() == (True,)
    linhas = client.get(base, headers=cabecalho).json()["acesso"]["linhas"]
    assert next(x for x in linhas if x["codigo"] == "COM-02")["ve"]["cliente_usuario"] is True

    assert (
        client.put(
            f"{base}/feriados/2026-08-15", json={"nome": "Padroeira"}, headers=cabecalho
        ).status_code
        == 204
    )
    assert client.get(base, headers=cabecalho).json()["feriados"] == [
        {"data": "2026-08-15", "nome": "Padroeira"}
    ]
    assert client.delete(f"{base}/feriados/2026-08-15", headers=cabecalho).status_code == 204
    assert client.delete(f"{base}/feriados/2026-08-15", headers=cabecalho).status_code == 404


# ---------- convite pela Gestão (migration 29) ----------

EMAIL_NOVO = "carla@alfa.example"
ID_AUTH_NOVO = "5b000000-0000-4000-8000-0000000000c1"


@pytest.fixture
def sem_carla(admin_conn: psycopg.Connection[tuple[object, ...]]) -> Iterator[None]:
    yield
    admin_conn.execute("delete from seg.usuario where lower(email) = %s", (EMAIL_NOVO,))
    admin_conn.execute("delete from seg.log_alteracao where depois ->> 'convite' = 'fila'")


def _carla(client: TestClient) -> Any:
    pessoas = client.get("/api/admin/usuarios", headers=auth(OID_ADMIN)).json()["usuarios"]
    return next(p for p in pessoas if p["email"] == EMAIL_NOVO)


def _convite_na_fila(admin_conn: psycopg.Connection[tuple[object, ...]], usuario_id: str) -> int:
    linha = admin_conn.execute(
        "select id from seg.convite_fila where usuario_id = %s and estado = 'fila'", (usuario_id,)
    ).fetchone()
    assert linha is not None
    return int(str(linha[0]))


@pytest.mark.usefixtures("sem_carla")
def test_convite_pela_gestao_da_fila_ao_login(
    client: TestClient, admin_conn: psycopg.Connection[tuple[object, ...]]
) -> None:
    novo = {
        "email": " Carla@Alfa.example ",
        "nome": "Carla Alfa",
        "perfil": "cliente_usuario",
        "clientes": [SLUG_A],
    }
    cabecalho = auth(OID_ADMIN)

    # só o admin convida
    assert client.post("/api/admin/usuarios", json=novo, headers=auth(OID_A)).status_code == 403

    r = client.post("/api/admin/usuarios", json=novo, headers=cabecalho)
    assert r.status_code == 202
    usuario_id = r.json()["id"]
    assert r.json()["mensagem"].startswith("Convite na fila")

    # a pessoa já aparece na lista, sem login ainda
    carla = _carla(client)
    assert (carla["id"], carla["situacao"]["id"], carla["convite"]) == (usuario_id, "fila", None)
    assert [c["slug"] for c in carla["clientes"]] == [SLUG_A]
    assert admin_conn.execute(
        "select id_autenticacao, convidado_em, perfil from seg.usuario where id = %s",
        (usuario_id,),
    ).fetchone() == (None, None, "cliente_usuario")
    assert admin_conn.execute(
        "select count(*) from seg.log_alteracao where tabela = 'seg.usuario' and registro_id = %s",
        (usuario_id,),
    ).fetchone() == (1,)

    # o e-mail é o login: um por pessoa; e um pedido pendente por vez
    repetido = client.post("/api/admin/usuarios", json=novo, headers=cabecalho)
    assert (repetido.status_code, repetido.json()) == (
        422,
        {"detail": "Este e-mail já tem cadastro no Overnex."},
    )
    de_novo = client.post(f"/api/admin/usuarios/{usuario_id}/convite", headers=cabecalho)
    assert (de_novo.status_code, de_novo.json()) == (
        422,
        {"detail": "O convite desta pessoa já está na fila."},
    )

    # o job não conseguiu mandar: a situação mostra o motivo e oferece tentar de novo
    admin_conn.execute(
        "select seg.concluir_convite(%s, null, 'O limite de e-mails do Supabase foi atingido.')",
        (_convite_na_fila(admin_conn, usuario_id),),
    )
    carla = _carla(client)
    assert (carla["situacao"]["id"], carla["situacao"]["detalhe"], carla["convite"]) == (
        "nao-enviado",
        "O limite de e-mails do Supabase foi atingido.",
        {"rotulo": "Tentar de novo"},
    )

    # tenta de novo; desta vez o job manda e grava o id do Supabase Auth
    assert (
        client.post(f"/api/admin/usuarios/{usuario_id}/convite", headers=cabecalho).status_code
        == 202
    )
    assert _carla(client)["situacao"]["id"] == "fila"
    admin_conn.execute(
        "select seg.concluir_convite(%s, %s, null)",
        (_convite_na_fila(admin_conn, usuario_id), ID_AUTH_NOVO),
    )
    carla = _carla(client)
    assert (carla["situacao"]["id"], carla["convite"]) == (
        "convite",
        {"rotulo": "Reenviar convite"},
    )

    # com o login ligado ao cadastro, a pessoa entra e abre o cliente dela
    sessao = _sessao(client, ID_AUTH_NOVO)
    assert sessao.status_code == 200
    assert sessao.json()["cliente"]["slug"] == SLUG_A


@pytest.mark.usefixtures("sem_carla")
def test_convite_sem_empresa_fica_sem_empresa_depois_de_enviado(
    client: TestClient, admin_conn: psycopg.Connection[tuple[object, ...]]
) -> None:
    r = client.post(
        "/api/admin/usuarios",
        json={"email": EMAIL_NOVO, "nome": "Carla Alfa", "perfil": "consultor", "clientes": []},
        headers=auth(OID_ADMIN),
    )
    assert r.status_code == 202
    usuario_id = r.json()["id"]
    assert admin_conn.execute(
        "select tipo from seg.usuario where id = %s", (usuario_id,)
    ).fetchone() == ("lann",)

    admin_conn.execute(
        "select seg.concluir_convite(%s, %s, null)",
        (_convite_na_fila(admin_conn, usuario_id), ID_AUTH_NOVO),
    )

    carla = _carla(client)
    # enviado, mas falta marcar a empresa; o botão de reenviar continua
    assert (carla["situacao"]["id"], carla["aoEntrar"]["rotulo"], carla["convite"]) == (
        "sem-empresa",
        "Não vê nada",
        {"rotulo": "Reenviar convite"},
    )


@pytest.mark.usefixtures("sem_carla")
def test_convite_recusa_o_que_a_edicao_recusa(client: TestClient) -> None:
    base = {"email": EMAIL_NOVO, "nome": "Carla Alfa", "perfil": "cliente_usuario"}
    casos = [
        ({"perfil": "admin"}, "Admin BORD só com e-mail da BORD."),
        ({"sessaoMinutos": 1440}, "Mais de 12 h, só Admin BORD."),
        ({"clientes": ["nao-existe"]}, "Empresa não encontrada: nao-existe."),
        ({"email": "ANA@alfa.example"}, "Este e-mail já tem cadastro no Overnex."),
        ({"email": "sem-arroba"}, "E-mail inválido."),
    ]
    for mudanca, detalhe in casos:
        r = client.post("/api/admin/usuarios", json=base | mudanca, headers=auth(OID_ADMIN))
        assert (r.status_code, r.json()) == (422, {"detail": detalhe})
    pessoas = client.get("/api/admin/usuarios", headers=auth(OID_ADMIN)).json()["usuarios"]
    assert EMAIL_NOVO not in {p["email"] for p in pessoas}


# ---------- empresa nova pela Gestão (migrations 30 e 31) ----------


@pytest.fixture
def beta_de_volta(admin_conn: psycopg.Connection[tuple[object, ...]]) -> Iterator[None]:
    yield
    admin_conn.execute(
        "update cfg.cliente set nome = 'Padrão LANN Beta', segmento = null where id = %s",
        (CLIENTE_B,),
    )
    admin_conn.execute(
        "delete from seg.log_alteracao where tabela = 'cfg.cliente' and registro_id = %s",
        (CLIENTE_B,),
    )


@pytest.mark.usefixtures("beta_de_volta")
def test_admin_troca_nome_e_setor_de_uma_empresa_que_ja_existe(
    client: TestClient, admin_conn: psycopg.Connection[tuple[object, ...]]
) -> None:
    rota = f"/api/admin/clientes/{SLUG_B}"
    cabecalho = auth(OID_ADMIN)

    r = client.put(
        rota, json={"nome": " Beta  Distribuidora ", "segmento": "Distribuidora"}, headers=cabecalho
    )
    assert (r.status_code, r.content) == (204, b"")

    assert admin_conn.execute(
        "select nome, segmento, slug from cfg.cliente where id = %s", (CLIENTE_B,)
    ).fetchone() == ("Beta Distribuidora", "Distribuidora", SLUG_B)
    assert admin_conn.execute(
        "select antes ->> 'nome', depois ->> 'segmento' from seg.log_alteracao"
        " where tabela = 'cfg.cliente' and registro_id = %s",
        (CLIENTE_B,),
    ).fetchall() == [("Padrão LANN Beta", "Distribuidora")]
    # o nome novo aparece para quem é do cliente
    assert _sessao(client, OID_B).json()["cliente"]["nome"] == "Beta Distribuidora"

    # setor em branco volta a "não informado"; nome em branco não passa
    assert (
        client.put(rota, json={"nome": "Beta", "segmento": " "}, headers=cabecalho).status_code
        == 204
    )
    assert admin_conn.execute(
        "select nome, segmento from cfg.cliente where id = %s", (CLIENTE_B,)
    ).fetchone() == ("Beta", None)
    vazio = client.put(rota, json={"nome": "  "}, headers=cabecalho)
    assert (vazio.status_code, vazio.json()) == (422, {"detail": "Informe o nome da empresa."})

    # 404 só para slug que não existe; e quem não é admin, 403
    fora = client.put("/api/admin/clientes/nao-existe", json={"nome": "X"}, headers=cabecalho)
    assert (fora.status_code, fora.json()) == (404, {"detail": "Empresa não encontrada."})
    assert client.put(rota, json={"nome": "X"}, headers=auth(OID_B)).status_code == 403


SLUG_NOVO = "nova-gama"


@pytest.fixture
def sem_empresa_nova(admin_conn: psycopg.Connection[tuple[object, ...]]) -> Iterator[None]:
    yield
    admin_conn.execute("delete from seg.usuario where lower(email) = %s", (EMAIL_NOVO,))
    for tabela in ("cfg.empresa", "cfg.perfil_acesso"):
        admin_conn.execute(
            f"delete from {tabela} where cliente_id in"  # noqa: S608
            " (select id from cfg.cliente where slug = %s)",
            (SLUG_NOVO,),
        )
    admin_conn.execute("delete from cfg.cliente where slug = %s", (SLUG_NOVO,))
    admin_conn.execute(
        "delete from seg.log_alteracao where tabela = 'cfg.cliente'"
        " or depois ->> 'convite' = 'fila'"
    )


@pytest.mark.usefixtures("sem_empresa_nova")
def test_empresa_nova_pela_gestao_do_cadastro_ao_primeiro_cnpj(
    client: TestClient, admin_conn: psycopg.Connection[tuple[object, ...]]
) -> None:
    cabecalho = auth(OID_ADMIN)
    nova = {"nome": "Nova Gama", "segmento": "Distribuição de alimentos"}

    # só o admin cria empresa
    assert client.post("/api/admin/clientes", json=nova, headers=auth(OID_A)).status_code == 403

    r = client.post("/api/admin/clientes", json=nova, headers=cabecalho)
    assert r.status_code == 201
    assert (r.json()["slug"], r.json()["nome"]) == (SLUG_NOVO, "Nova Gama")

    # aparece na barra lateral, com o setor e tudo desligado, e abre
    gestao = client.get("/api/admin/gestao", headers=cabecalho).json()
    gama = next(c for c in gestao["clientes"] if c["slug"] == SLUG_NOVO)
    assert gama["segmento"] == "Distribuição de alimentos"
    assert {c["estado"] for c in gama["camadas"]} == {"desligada"}
    aberta = client.get(f"/api/admin/clientes/{SLUG_NOVO}", headers=cabecalho).json()
    assert aberta["cliente"]["segmento"] == "Distribuição de alimentos"
    assert (aberta["usuarios"], aberta["fontes"]) == ([], [])
    assert len(aberta["acesso"]["perfis"]) == 4

    # nome e setor mudam pela tela; o slug fica
    mudou = client.put(
        f"/api/admin/clientes/{SLUG_NOVO}",
        json={"nome": "Nova Gama Ltda", "segmento": "Atacado"},
        headers=cabecalho,
    )
    assert mudou.status_code == 204
    aberta = client.get(f"/api/admin/clientes/{SLUG_NOVO}", headers=cabecalho).json()
    assert aberta["cliente"] == {
        "slug": SLUG_NOVO,
        "nome": "Nova Gama Ltda",
        "segmento": "Atacado",
        "logoUrl": None,
    }
    assert (
        client.put(
            f"/api/admin/clientes/{SLUG_NOVO}", json={"nome": "X"}, headers=auth(OID_A)
        ).status_code
        == 403
    )

    # o mesmo identificador não se repete
    repetida = client.post("/api/admin/clientes", json=nova, headers=cabecalho)
    assert (repetida.status_code, repetida.json()) == (
        422,
        {"detail": "Já existe uma empresa com o identificador nova-gama."},
    )

    # já dá para convidar alguém para ela, antes de existir CNPJ
    convite = client.post(
        "/api/admin/usuarios",
        json={
            "email": EMAIL_NOVO,
            "nome": "Carla Alfa",
            "perfil": "cliente_principal",
            "clientes": [SLUG_NOVO],
        },
        headers=cabecalho,
    )
    assert convite.status_code == 202
    usuario_id = convite.json()["id"]
    admin_conn.execute(
        "select seg.concluir_convite(%s, %s, null)",
        (_convite_na_fila(admin_conn, usuario_id), ID_AUTH_NOVO),
    )
    antes = _sessao(client, ID_AUTH_NOVO).json()
    assert (antes["cliente"]["slug"], antes["empresas"]) == (SLUG_NOVO, [])

    # o CNPJ chega depois, por script: entra sozinho no vínculo de quem já foi convidado
    admin_conn.execute(
        "insert into cfg.empresa (cliente_id, nome)"
        " select id, 'Nova Gama Matriz' from cfg.cliente where slug = %s",
        (SLUG_NOVO,),
    )
    depois = _sessao(client, ID_AUTH_NOVO).json()
    assert [e["nome"] for e in depois["empresas"]] == ["Nova Gama Matriz"]

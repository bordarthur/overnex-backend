"""Rotas da Gestão (/api/admin/*) — sem banco.

A sessão é falsa: a checagem de acesso lê os fatos de uma conexão que só responde a
seg.contexto_acesso; as leituras (uma ida ao banco) são trocadas por linhas, e a confirmação
da gravação por um registro.
"""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.admin import clientes, convites, textos, usuarios
from app.admin import router as admin_router
from app.admin.usuarios import NaoEncontrado, RegraInvalida
from app.core.config import get_settings
from app.core.db import Sessao, get_sessao
from app.main import create_app
from app.seguranca.auth import UsuarioToken
from app.telas.paineis import router as paineis_router
from app.telas.visao_geral import preferencias
from app.telas.visao_geral import router as visao_geral_router

ADMIN = UUID("d1000000-0000-4000-8000-0000000000ad")
ANA = UUID("d1000000-0000-4000-8000-00000000000a")
BRUNO = UUID("d1000000-0000-4000-8000-00000000000b")
CLIENTE_A = UUID("c1000000-0000-4000-8000-00000000000a")
CLIENTE_B = UUID("c1000000-0000-4000-8000-00000000000b")
AGORA = datetime(2026, 11, 10, 14, 30, tzinfo=textos.FORTALEZA)
SLUG = "padrao-lann-alfa"


class Resultado:
    def __init__(self, linha: dict[str, Any] | None) -> None:
        self.linha = linha

    def fetchone(self) -> dict[str, Any] | None:
        return self.linha


class ConexaoFalsa:
    """Responde aos fatos de acesso e guarda o resto (as gravações)."""

    def __init__(self, estado: dict[str, Any]) -> None:
        self.estado = estado

    def execute(self, consulta: str, params: Any = None) -> Resultado:
        if "seg.contexto_acesso" in consulta:
            self.estado["slug_pedido"] = params[2]
            return Resultado(self.estado["fatos"])
        self.estado["gravacoes"].append((consulta, params))
        return Resultado(self.estado.get("devolve"))


def _fatos(**mudancas: Any) -> dict[str, Any]:
    """Dora (Admin BORD), sem cliente escolhido."""
    base: dict[str, Any] = {
        "usuario_id": ADMIN,
        "ativo": True,
        "vinculos": [],
        "perfis_tela": {},
        "empresas": [],
        "perfil": "admin",
        "cliente_id": None,
    }
    return base | mudancas


def _pessoa(id_: UUID, nome: str, perfil: str, slugs: list[str], **mudancas: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": id_,
        "nome": nome,
        "email": f"{nome.split()[0].lower()}@teste.example",
        "perfil": perfil,
        "ativo": True,
        "sessao_minutos": 1440 if perfil == "admin" else 240,
        "convidado_em": None,
        "primeiro_acesso_em": AGORA - timedelta(days=20),
        "desativado_em": None,
        "clientes": [{"slug": s, "nome": s.title()} for s in slugs],
        "entradas_30d": 3,
        "ultimo_acesso": AGORA - timedelta(hours=1),
        "convite_estado": None,
        "convite_em": None,
        "convite_motivo": None,
    }
    return base | mudancas


def _linhas() -> dict[str, list[dict[str, Any]]]:
    carga_inicio = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    return {
        # barra lateral
        "clientes": [
            {"id": CLIENTE_A, "nome": "Padrão LANN Alfa", "slug": SLUG, "segmento": "Atacado"},
            {
                "id": CLIENTE_B,
                "nome": "Padrão LANN Beta",
                "slug": "padrao-lann-beta",
                "segmento": None,
            },
        ],
        "ligadas": [
            {"cliente_id": CLIENTE_A, "codigo_tela": c}
            for c in ("VG-01", "COM-01", "COM-02", "COM-03", "FIN-03")
        ],
        "com_carga": [
            {"cliente_id": CLIENTE_A, "tabela_destino": "f_vendas_alfa"},
            {"cliente_id": CLIENTE_B, "tabela_destino": "f_vendas_beta"},
        ],
        "perfis": [
            {"perfil": "admin", "minutos": 1440, "pessoas": 1},
            {"perfil": "consultor", "minutos": 480, "pessoas": 0},
            {"perfil": "cliente_principal", "minutos": 240, "pessoas": 1},
            {"perfil": "cliente_usuario", "minutos": 240, "pessoas": 1},
        ],
        # abas de uma empresa
        "cliente": [
            {
                "id": CLIENTE_A,
                "nome": "Padrão LANN Alfa",
                "slug": SLUG,
                "logo_url": None,
                "segmento": "Atacado",
            }
        ],
        "telas": [
            {"codigo_tela": "VG-01", "ligada": True},
            {"codigo_tela": "COM-01", "ligada": True},
            {"codigo_tela": "PES-01", "ligada": True},
            {"codigo_tela": "FIN-03", "ligada": False},
        ],
        "fontes": [
            {
                "tabela": "f_folha_alfa",
                "tipo": "tabela",
                "ativo": True,
                "ultimo_status": "erro",
                "ultima_mensagem": "arquivo não encontrado",
                "data_snapshot": None,
                "linhas_gravadas": None,
            },
            {
                "tabela": "f_vendas_alfa",
                "tipo": "tabela",
                "ativo": True,
                "ultimo_status": "ok",
                "ultima_mensagem": None,
                "data_snapshot": date(2026, 11, 9),
                "linhas_gravadas": 412380,
            },
        ],
        "matriz": [
            {"perfil": "cliente_principal", "codigo_tela": "VG-01"},
            {"perfil": "cliente_principal", "codigo_tela": "COM-01"},
            {"perfil": "cliente_principal", "codigo_tela": "PES-01"},
            {"perfil": "cliente_usuario", "codigo_tela": "VG-01"},
        ],
        "feriados": [{"data": date(2026, 8, 15), "nome": "Padroeira"}],
        "cargas": [
            {
                "carga_id": 9,
                "inicio": carga_inicio,
                "fim": carga_inicio + timedelta(seconds=124),
                "fontes": 2,
                "erros": 1,
                "rodando": 0,
                "mensagem": "arquivo não encontrado",
            }
        ],
        "usuarios": [
            _pessoa(ANA, "Ana Alfa", "cliente_principal", [SLUG]),
            _pessoa(BRUNO, "Bruno Beta", "cliente_usuario", [SLUG, "padrao-lann-beta"]),
            _pessoa(ADMIN, "Dora Admin", "admin", []),
        ],
        "admins": [{"total": 1}],
        "total_clientes": [{"total": 2}],
    }


@pytest.fixture
def estado(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    e: dict[str, Any] = {
        "fatos": _fatos(),
        "linhas": _linhas(),
        "gravacoes": [],
        "confirmadas": 0,
        "apagadas": [],
    }

    def em_uma_ida(conn: Any, consultas: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        return {nome: e["linhas"][nome] for nome in consultas}

    def confirmar(sessao: Sessao) -> None:
        e["confirmadas"] += 1

    monkeypatch.setattr(clientes, "em_uma_ida", em_uma_ida)
    monkeypatch.setattr(admin_router, "em_uma_ida", em_uma_ida)
    monkeypatch.setattr(admin_router, "confirmar_agora", confirmar)
    monkeypatch.setattr(preferencias, "valor", lambda conn, cliente, id_: "auto")
    monkeypatch.setattr(textos, "agora", lambda: AGORA)
    for modulo, nome in ((visao_geral_router, "VG"), (paineis_router, "paineis")):
        monkeypatch.setattr(
            modulo,
            "apagar_respostas_do_cliente",
            lambda cliente, nome=nome: e["apagadas"].append((nome, cliente)),
        )
    return e


@pytest.fixture
def client(estado: dict[str, Any]) -> Iterator[TestClient]:
    app = create_app()

    def sessao() -> Sessao:
        s = Sessao(
            conn=ConexaoFalsa(estado),  # type: ignore[arg-type]
            usuario=UsuarioToken(id_autenticacao=UUID(int=1)),
        )
        estado.setdefault("sessoes", []).append(s)
        return s

    app.dependency_overrides[get_sessao] = sessao
    return TestClient(app)


def _no_cliente(estado: dict[str, Any]) -> None:
    """O banco achou o cliente do slug da rota."""
    estado["fatos"] = _fatos(cliente_id=CLIENTE_A)


# ---------- só o admin ----------

ROTAS = [
    ("GET", "/api/admin/gestao", None),
    ("POST", "/api/admin/clientes", {"nome": "Nova Empresa"}),
    ("GET", f"/api/admin/clientes/{SLUG}", None),
    ("PUT", f"/api/admin/clientes/{SLUG}", {"nome": "Alfa", "segmento": "Atacado"}),
    ("PUT", f"/api/admin/clientes/{SLUG}/paineis/VG-01", {"ligado": False}),
    ("PUT", f"/api/admin/clientes/{SLUG}/perfis/consultor/paineis/VG-01", {"ve": True}),
    ("PUT", f"/api/admin/clientes/{SLUG}/preferencias/linhas-custo", {"valor": "3"}),
    ("PUT", f"/api/admin/clientes/{SLUG}/feriados/2026-08-15", {"nome": "Padroeira"}),
    ("DELETE", f"/api/admin/clientes/{SLUG}/feriados/2026-08-15", None),
    ("GET", "/api/admin/usuarios", None),
    (
        "POST",
        "/api/admin/usuarios",
        {"email": "carla@alfa.example", "nome": "Carla", "perfil": "cliente_usuario"},
    ),
    ("POST", f"/api/admin/usuarios/{BRUNO}/convite", None),
    (
        "PUT",
        f"/api/admin/usuarios/{BRUNO}",
        {"nome": "Bruno", "perfil": "cliente_usuario", "clientes": [SLUG], "sessaoMinutos": 240},
    ),
    ("PUT", f"/api/admin/usuarios/{BRUNO}/sessao", {"minutos": 480}),
    ("PUT", f"/api/admin/usuarios/{BRUNO}/acesso", {"ativo": False}),
]


@pytest.mark.parametrize(("metodo", "rota", "corpo"), ROTAS)
def test_quem_nao_e_admin_recebe_403_generico(
    client: TestClient, estado: dict[str, Any], metodo: str, rota: str, corpo: Any
) -> None:
    # Cliente principal com o cliente dele escolhido: nem assim
    estado["fatos"] = _fatos(
        usuario_id=ANA,
        perfil="cliente_principal",
        vinculos=[{"cliente_id": str(CLIENTE_A), "papel": "gestor"}],
        cliente_id=CLIENTE_A,
    )

    r = client.request(metodo, rota, json=corpo)

    assert r.status_code == 403
    assert r.json() == {"detail": "Acesso negado."}
    (log,) = estado["sessoes"][0].logs
    assert log[4].startswith("admin:")
    assert log[5:8] == ("negado", 3, "perfil não é admin")
    assert estado["gravacoes"] == []
    assert estado["confirmadas"] == 0


def test_cliente_que_nao_existe_404(client: TestClient, estado: dict[str, Any]) -> None:
    r = client.get("/api/admin/clientes/nao-existe")
    assert r.status_code == 404
    assert r.json() == {"detail": "Empresa não encontrada."}
    assert estado["slug_pedido"] == "nao-existe"


# ---------- leituras ----------


def test_gestao_lista_as_empresas_com_as_camadas_os_perfis_e_as_sessoes(
    client: TestClient, estado: dict[str, Any]
) -> None:
    r = client.get("/api/admin/gestao")

    assert r.status_code == 200
    corpo = r.json()
    # sem cliente escolhido: a barra lateral lê todos
    assert estado["slug_pedido"] is None
    alfa, beta = corpo["clientes"]
    assert (alfa["slug"], alfa["nome"]) == (SLUG, "Padrão LANN Alfa")
    assert (alfa["segmento"], beta["segmento"]) == ("Atacado", None)
    assert [(c["id"], c["estado"], c["estadoRotulo"], c["status"]) for c in alfa["camadas"]] == [
        ("comercial", "ativa", "Ativa", "saudavel"),
        ("pessoas", "desligada", "Desligada", "neutro"),
        # ligado sem carga de títulos
        ("receber-pagar", "implantacao", "Em implantação", "atencao"),
        ("completo", "desligada", "Desligada", "neutro"),
    ]
    assert {c["estado"] for c in beta["camadas"]} == {"desligada"}
    assert corpo["perfis"][0] == {
        "id": "admin",
        "rotulo": "Admin BORD",
        "descricao": "Equipe BORD que configura o Overnex. Vê todas as empresas e todos os "
        "painéis.",
        "clientes": "Todas, sem vínculo",
        "sessaoPadraoMinutos": 1440,
        "sessaoPadraoRotulo": "24 h",
        "pessoas": 1,
    }
    assert [p["id"] for p in corpo["perfis"]] == [
        "admin",
        "consultor",
        "cliente_principal",
        "cliente_usuario",
    ]
    assert corpo["sessoes"][-2:] == [
        {"minutos": 720, "rotulo": "12 h", "soAdmin": False},
        {"minutos": 1440, "rotulo": "24 h", "soAdmin": True},
    ]


def test_cliente_devolve_todas_as_abas(client: TestClient, estado: dict[str, Any]) -> None:
    _no_cliente(estado)

    r = client.get(f"/api/admin/clientes/{SLUG}")

    assert r.status_code == 200
    corpo = r.json()
    # o cliente da rota vira o cliente escolhido da transação (RLS)
    assert estado["slug_pedido"] == SLUG
    assert corpo["cliente"] == {
        "slug": SLUG,
        "nome": "Padrão LANN Alfa",
        "segmento": "Atacado",
        "logoUrl": None,
    }
    assert corpo["resumo"] == [
        {"rotulo": "0 de 4 camadas ativas", "status": "atencao"},
        {"rotulo": "2 usuários", "status": "neutro"},
        {"rotulo": "Última carga: hoje, 9h00", "status": "critico"},
    ]

    # prontidão dos dados
    folha, vendas = corpo["fontes"]
    assert folha == {
        "tabela": "f_folha_alfa",
        "usadaPor": ["Pessoas"],
        "linhas": None,
        "ultimaData": None,
        "status": "critico",
        "statusRotulo": "erro",
        "observacao": "arquivo não encontrado",
    }
    assert (vendas["linhas"], vendas["ultimaData"], vendas["statusRotulo"]) == (
        412380,
        "09/11/2026",
        "ok",
    )

    # painéis: Pessoas está ligado, mas sem dado; Receber e pagar, desligado e sem dado
    paineis = {p["codigo"]: p for p in corpo["paineis"]}
    assert len(paineis) == 8
    assert (paineis["VG-01"]["ligado"], paineis["VG-01"]["podeLigar"]) == (True, True)
    assert (paineis["PES-01"]["ligado"], paineis["PES-01"]["podeLigar"]) == (True, False)
    assert paineis["PES-01"]["nota"] == "Sem carga de f_folha_* nesta empresa."
    assert paineis["FIN-03"]["ligado"] is False
    assert paineis["FIN-02"]["grupo"] == "Universal Completo"

    # matriz: só os painéis ligados; o admin vê todos e não muda
    assert corpo["acesso"]["perfis"] == [
        {"id": "admin", "rotulo": "Admin BORD", "fixo": True},
        {"id": "consultor", "rotulo": "Consultor por carteira", "fixo": False},
        {"id": "cliente_principal", "rotulo": "Cliente principal", "fixo": False},
        {"id": "cliente_usuario", "rotulo": "Cliente usuário", "fixo": False},
    ]
    assert corpo["acesso"]["linhas"] == [
        {
            "codigo": "VG-01",
            "nome": "Visão geral",
            "ve": {
                "admin": True,
                "consultor": False,
                "cliente_principal": True,
                "cliente_usuario": True,
            },
        },
        {
            "codigo": "COM-01",
            "nome": "Vendas",
            "ve": {
                "admin": True,
                "consultor": False,
                "cliente_principal": True,
                "cliente_usuario": False,
            },
        },
        {
            "codigo": "PES-01",
            "nome": "Pessoas",
            "ve": {
                "admin": True,
                "consultor": False,
                "cliente_principal": True,
                "cliente_usuario": False,
            },
        },
    ]

    # pessoas desta empresa: o admin não aparece
    ana, bruno = corpo["usuarios"]
    assert (ana["nome"], ana["paineisQueVe"], ana["outrosClientes"]) == ("Ana Alfa", "3 de 3", [])
    assert (bruno["paineisQueVe"], bruno["outrosClientes"]) == (
        "1 de 3",
        [{"slug": "padrao-lann-beta", "nome": "Padrao-Lann-Beta"}],
    )
    assert bruno["aoEntrar"] == {"tipo": "lista", "rotulo": "Escolhe entre 2 empresas"}

    assert corpo["preferencias"][0]["id"] == "linhas-custo"
    assert (corpo["preferencias"][0]["valor"], corpo["preferencias"][0]["editavel"]) == (
        "auto",
        True,
    )
    assert corpo["feriados"] == [{"data": "2026-08-15", "nome": "Padroeira"}]
    assert corpo["cargas"] == [
        {
            "data": "10/11",
            "inicio": "9h00",
            "duracao": "2 min 04 s",
            "tabelas": 2,
            "status": "critico",
            "statusRotulo": "parcial",
            "observacao": "1 fonte com erro: arquivo não encontrado",
        }
    ]
    # menu do Cliente principal: Pessoas está na matriz, mas sem dado não aparece
    assert corpo["menu"] == [
        {"grupo": "Universal Básico", "itens": [{"nome": "Visão geral"}, {"nome": "Vendas"}]}
    ]


def test_usuarios_lista_todos_com_os_filtros(client: TestClient, estado: dict[str, Any]) -> None:
    r = client.get("/api/admin/usuarios")

    assert r.status_code == 200
    corpo = r.json()
    ana, bruno, dora = corpo["usuarios"]
    assert ana == {
        "id": str(ANA),
        "nome": "Ana Alfa",
        "email": "ana@teste.example",
        "perfil": {"id": "cliente_principal", "rotulo": "Cliente principal"},
        "todosOsClientes": False,
        "clientes": [{"slug": SLUG, "nome": "Padrao-Lann-Alfa"}],
        "aoEntrar": {"tipo": "direto", "rotulo": "Abre direto em Padrao-Lann-Alfa"},
        "sessaoMinutos": 240,
        "situacao": {
            "id": "ativo",
            "rotulo": "Ativo",
            "quando": "último acesso hoje, 13h30",
            "status": "saudavel",
            "detalhe": None,
        },
        "acessos": {"entradas30d": 3, "ultimo": "hoje, 13h30"},
        "podeDesativar": True,
        # já entrou: sem botão de convite
        "convite": None,
    }
    # a única admin ativa não pode ser desativada
    assert (dora["todosOsClientes"], dora["podeDesativar"]) == (True, False)
    assert dora["aoEntrar"]["rotulo"] == "Abre a Gestão e escolhe entre as 2 empresas"
    assert [(f["id"], f["total"]) for f in corpo["filtros"]] == [
        ("todos", 3),
        ("admin", 1),
        ("consultor", 0),
        ("cliente_principal", 1),
        ("cliente_usuario", 1),
        ("pendentes", 0),
        ("sem-empresa", 0),
    ]
    assert corpo["notaConvite"].startswith("A pessoa recebe o convite por e-mail")
    assert bruno["clientes"][1]["slug"] == "padrao-lann-beta"


def test_usuarios_mostra_o_convite_que_nao_saiu(client: TestClient, estado: dict[str, Any]) -> None:
    estado["linhas"]["usuarios"] = [
        _pessoa(
            BRUNO,
            "Bruno Beta",
            "cliente_usuario",
            [SLUG],
            primeiro_acesso_em=None,
            ultimo_acesso=None,
            convite_estado="falhou",
            convite_em=AGORA - timedelta(minutes=5),
            convite_motivo="O limite de e-mails do Supabase foi atingido.",
        )
    ]

    (bruno,) = client.get("/api/admin/usuarios").json()["usuarios"]

    assert bruno["situacao"] == {
        "id": "nao-enviado",
        "rotulo": "Convite não enviado",
        "quando": "tentativa hoje, 14h25",
        "status": "critico",
        "detalhe": "O limite de e-mails do Supabase foi atingido.",
    }
    assert bruno["convite"] == {"rotulo": "Tentar de novo"}


# ---------- gravações ----------


def test_ligar_painel_grava_e_confirma(client: TestClient, estado: dict[str, Any]) -> None:
    _no_cliente(estado)

    r = client.put(f"/api/admin/clientes/{SLUG}/paineis/COM-02", json={"ligado": True})

    assert r.status_code == 204
    assert [g[0] for g in estado["gravacoes"]] == [
        clientes.SQL_LIGAR,
        clientes.SQL_LIBERAR,
        usuarios.SQL_LOG,
    ]
    assert estado["gravacoes"][0][1] == {"cliente": CLIENTE_A, "codigo": "COM-02", "ligada": True}
    # quem gravou é a admin da sessão
    assert estado["gravacoes"][2][1]["autor"] == ADMIN
    assert estado["confirmadas"] == 1


@pytest.mark.parametrize(
    ("rota", "corpo", "status", "detalhe"),
    [
        # sem carga da folha
        ("paineis/FIN-02", {"ligado": True}, 422, "Este painel ainda não tem dado nesta empresa."),
        ("paineis/XX-99", {"ligado": True}, 404, "Painel não encontrado."),
        (
            "perfis/admin/paineis/VG-01",
            {"ve": False},
            422,
            "Admin BORD vê tudo e não pode ser alterado.",
        ),
        ("perfis/Financeiro/paineis/VG-01", {"ve": True}, 404, "Perfil não encontrado."),
        ("preferencias/outra", {"valor": "3"}, 404, "Preferência não encontrada."),
        ("preferencias/linhas-custo", {"valor": "8"}, 422, "Opção inválida."),
    ],
)
def test_gravacao_recusada_nao_confirma(
    client: TestClient, estado: dict[str, Any], rota: str, corpo: Any, status: int, detalhe: str
) -> None:
    _no_cliente(estado)

    r = client.put(f"/api/admin/clientes/{SLUG}/{rota}", json=corpo)

    assert (r.status_code, r.json()) == (status, {"detail": detalhe})
    assert estado["gravacoes"] == []
    assert estado["confirmadas"] == 0


@pytest.mark.parametrize(
    ("rota", "corpo"),
    [
        ("paineis/vg-01", {"ligado": True}),
        ("paineis/VG-01", {"ligado": "talvez"}),
        ("paineis/VG-01", {}),
        ("feriados/15-08-2026", {"nome": "Padroeira"}),
        ("feriados/2026-08-15", {"nome": ""}),
    ],
)
def test_pedido_fora_do_formato_422(
    client: TestClient, estado: dict[str, Any], rota: str, corpo: Any
) -> None:
    _no_cliente(estado)
    assert client.put(f"/api/admin/clientes/{SLUG}/{rota}", json=corpo).status_code == 422
    assert estado["confirmadas"] == 0


def test_matriz_grava_no_perfil_fixo(client: TestClient, estado: dict[str, Any]) -> None:
    _no_cliente(estado)

    r = client.put(
        f"/api/admin/clientes/{SLUG}/perfis/cliente_usuario/paineis/COM-01", json={"ve": True}
    )

    assert r.status_code == 204
    assert estado["gravacoes"][0] == (
        clientes.SQL_LIBERAR,
        {
            "cliente": CLIENTE_A,
            "codigo": "COM-01",
            "perfil": "cliente_usuario",
            "nominal": False,
            "perfis": ["cliente_usuario"],
        },
    )
    assert estado["confirmadas"] == 1
    # a matriz não muda o conteúdo das telas: as respostas prontas ficam
    assert estado["apagadas"] == []


def test_preferencia_e_feriado_apagam_as_respostas_prontas_do_cliente(
    client: TestClient, estado: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_cliente(estado)
    gravadas: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        preferencias,
        "gravar",
        lambda conn, cliente, autor, p, valor, hoje: gravadas.append((cliente, autor, p.id, valor)),
    )

    r = client.put(f"/api/admin/clientes/{SLUG}/preferencias/linhas-custo", json={"valor": "3"})

    assert r.status_code == 204
    assert gravadas == [(CLIENTE_A, ADMIN, "linhas-custo", "3")]
    assert estado["apagadas"] == [("VG", CLIENTE_A), ("paineis", CLIENTE_A)]

    r = client.put(f"/api/admin/clientes/{SLUG}/feriados/2026-08-15", json={"nome": " Padroeira "})

    assert r.status_code == 204
    assert estado["gravacoes"][0] == (
        clientes.SQL_GRAVAR_FERIADO,
        {"cliente": CLIENTE_A, "data": date(2026, 8, 15), "nome": "Padroeira"},
    )
    assert len(estado["apagadas"]) == 4
    assert estado["confirmadas"] == 2


def test_apagar_feriado(client: TestClient, estado: dict[str, Any]) -> None:
    _no_cliente(estado)

    r = client.delete(f"/api/admin/clientes/{SLUG}/feriados/2026-08-15")
    assert (r.status_code, r.json()) == (404, {"detail": "Feriado não encontrado."})
    assert estado["confirmadas"] == 0

    estado["gravacoes"].clear()
    estado["devolve"] = {"nome": "Padroeira"}
    r = client.delete(f"/api/admin/clientes/{SLUG}/feriados/2026-08-15")
    assert r.status_code == 204
    assert [g[0] for g in estado["gravacoes"]] == [clientes.SQL_APAGAR_FERIADO, usuarios.SQL_LOG]
    assert estado["confirmadas"] == 1


def test_mudar_usuario_repassa_o_pedido_e_o_dominio_do_admin(
    client: TestClient, estado: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    pedidos: list[Any] = []
    monkeypatch.setattr(usuarios, "mudar", lambda conn, *a, **k: pedidos.append((a, k)))

    r = client.put(
        f"/api/admin/usuarios/{BRUNO}",
        json={"nome": "Bruno B.", "perfil": "consultor", "clientes": [SLUG], "sessaoMinutos": 480},
    )

    assert r.status_code == 204
    assert pedidos == [
        (
            (ADMIN, BRUNO),
            {
                "nome": "Bruno B.",
                "perfil": "consultor",
                "clientes": [SLUG],
                "sessao_minutos": 480,
                "admin_dominios": get_settings().admin_dominios,
            },
        )
    ]
    assert estado["confirmadas"] == 1


@pytest.mark.parametrize(
    ("erro", "status"),
    [
        (RegraInvalida("Admin BORD só com e-mail da BORD."), 422),
        (NaoEncontrado("Pessoa não encontrada."), 404),
    ],
)
def test_mudanca_de_usuario_recusada(
    client: TestClient,
    estado: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    erro: Exception,
    status: int,
) -> None:
    def recusar(*a: Any, **k: Any) -> None:
        raise erro

    for nome in ("mudar", "mudar_sessao", "mudar_acesso"):
        monkeypatch.setattr(usuarios, nome, recusar)
    corpos = {
        "": {"nome": "Bruno", "perfil": "admin", "clientes": []},
        "/sessao": {"minutos": 480},
        "/acesso": {"ativo": False},
    }

    for sufixo, corpo in corpos.items():
        r = client.put(f"/api/admin/usuarios/{BRUNO}{sufixo}", json=corpo)
        assert (r.status_code, r.json()) == (status, {"detail": str(erro)})
    assert estado["confirmadas"] == 0


def test_sessao_e_acesso_do_usuario(
    client: TestClient, estado: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    chamadas: list[tuple[Any, ...]] = []
    monkeypatch.setattr(usuarios, "mudar_sessao", lambda conn, *a: chamadas.append(("sessao", *a)))
    monkeypatch.setattr(usuarios, "mudar_acesso", lambda conn, *a: chamadas.append(("acesso", *a)))

    assert (
        client.put(f"/api/admin/usuarios/{BRUNO}/sessao", json={"minutos": 720}).status_code == 204
    )
    assert (
        client.put(f"/api/admin/usuarios/{BRUNO}/acesso", json={"ativo": False}).status_code == 204
    )

    assert chamadas == [("sessao", ADMIN, BRUNO, 720), ("acesso", ADMIN, BRUNO, False)]
    assert estado["confirmadas"] == 2


def test_criar_empresa_monta_o_slug_grava_e_confirma(
    client: TestClient, estado: dict[str, Any]
) -> None:
    r = client.post(
        "/api/admin/clientes",
        json={"nome": "  Nilo  Maia Distribuição ", "segmento": " Distribuição de alimentos "},
    )

    assert r.status_code == 201
    assert r.json() == {
        "slug": "nilo-maia-distribuicao",
        "nome": "Nilo Maia Distribuição",
        "mensagem": clientes.MSG_CRIADA,
    }
    assert [g[0] for g in estado["gravacoes"]] == [clientes.SQL_CRIAR, usuarios.SQL_LOG]
    assert estado["gravacoes"][0][1] == {
        "nome": "Nilo Maia Distribuição",
        "slug": "nilo-maia-distribuicao",
        "segmento": "Distribuição de alimentos",
    }
    assert estado["gravacoes"][1][1]["autor"] == ADMIN
    assert estado["confirmadas"] == 1


def test_criar_empresa_com_o_slug_escolhido_e_sem_setor(
    client: TestClient, estado: dict[str, Any]
) -> None:
    r = client.post(
        "/api/admin/clientes", json={"nome": "PJ Cortes", "slug": "pj", "segmento": "  "}
    )

    assert (r.status_code, r.json()["slug"]) == (201, "pj")
    assert estado["gravacoes"][0][1] == {"nome": "PJ Cortes", "slug": "pj", "segmento": None}


@pytest.mark.parametrize(
    ("corpo", "detalhe"),
    [
        ({"nome": "   "}, "Informe o nome da empresa."),
        ({"nome": "PJ Cortes", "slug": "PJ Cortes"}, clientes.MSG_SLUG),
        ({"nome": "PJ Cortes", "slug": "pj--cortes"}, clientes.MSG_SLUG),
        # nome só com símbolos não dá identificador
        ({"nome": "***"}, clientes.MSG_SLUG),
    ],
)
def test_criar_empresa_recusada_nao_grava(
    client: TestClient, estado: dict[str, Any], corpo: dict[str, Any], detalhe: str
) -> None:
    r = client.post("/api/admin/clientes", json=corpo)

    assert (r.status_code, r.json()) == (422, {"detail": detalhe})
    assert estado["gravacoes"] == []
    assert estado["confirmadas"] == 0


def test_mudar_empresa_troca_nome_e_setor_e_apaga_as_respostas_prontas(
    client: TestClient, estado: dict[str, Any]
) -> None:
    _no_cliente(estado)
    estado["devolve"] = {"nome_antes": "Padrão LANN Alfa", "segmento_antes": None}

    r = client.put(
        f"/api/admin/clientes/{SLUG}", json={"nome": " PJ  Cortes ", "segmento": " Atacado "}
    )

    assert r.status_code == 204
    assert [g[0] for g in estado["gravacoes"]] == [clientes.SQL_MUDAR_EMPRESA, usuarios.SQL_LOG]
    assert estado["gravacoes"][0][1] == {
        "cliente": CLIENTE_A,
        "nome": "PJ Cortes",
        "segmento": "Atacado",
    }
    log = estado["gravacoes"][1][1]
    assert (log["autor"], log["operacao"]) == (ADMIN, "update")
    assert '"segmento": null' in log["antes"]
    assert estado["confirmadas"] == 1
    # o nome aparece nas telas
    assert estado["apagadas"] == [("VG", CLIENTE_A), ("paineis", CLIENTE_A)]


def test_mudar_empresa_sem_setor_e_nome_em_branco(
    client: TestClient, estado: dict[str, Any]
) -> None:
    _no_cliente(estado)
    estado["devolve"] = {"nome_antes": "Padrão LANN Alfa", "segmento_antes": "Atacado"}

    assert client.put(f"/api/admin/clientes/{SLUG}", json={"nome": "Alfa"}).status_code == 204
    assert estado["gravacoes"][0][1]["segmento"] is None

    estado["gravacoes"].clear()
    r = client.put(f"/api/admin/clientes/{SLUG}", json={"nome": "   "})
    assert (r.status_code, r.json()) == (422, {"detail": "Informe o nome da empresa."})
    assert estado["gravacoes"] == []
    assert estado["confirmadas"] == 1


def test_mudar_empresa_barrado_pelo_banco_e_erro_de_servidor(
    estado: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    # sem a migration 31, o RLS não deixa o update achar a linha: não é "não encontrada"
    _no_cliente(estado)
    app = create_app()
    app.dependency_overrides[get_sessao] = lambda: Sessao(
        conn=ConexaoFalsa(estado),  # type: ignore[arg-type]
        usuario=UsuarioToken(id_autenticacao=UUID(int=1)),
    )
    cliente_http = TestClient(app, raise_server_exceptions=False)

    r = cliente_http.put(f"/api/admin/clientes/{SLUG}", json={"nome": "PJ Cortes"})

    assert (r.status_code, r.json()) == (500, {"detail": "Erro interno."})
    assert "migration 31" in caplog.text
    assert estado["confirmadas"] == 0
    # só o update tentado: nada no log de alterações
    assert [g[0] for g in estado["gravacoes"]] == [clientes.SQL_MUDAR_EMPRESA]


def test_slug_do_nome() -> None:
    assert clientes.slug_do_nome("Açaí & Cia. Ltda") == "acai-cia-ltda"
    assert clientes.slug_do_nome("  PJ   Cortes  ") == "pj-cortes"
    assert clientes.slug_do_nome("***") == ""


NOVA = UUID("d1000000-0000-4000-8000-0000000000c1")


def test_convidar_grava_o_pedido_confirma_e_aciona_o_job(
    client: TestClient, estado: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    pedidos: list[Any] = []
    ordem: list[str] = []

    def pedir(conn: Any, *a: Any, **k: Any) -> UUID:
        pedidos.append((a, k))
        return NOVA

    def confirmar(sessao: Any) -> None:
        ordem.append("commit")

    monkeypatch.setattr(convites, "pedir", pedir)
    monkeypatch.setattr(admin_router, "confirmar_agora", confirmar)
    monkeypatch.setattr(convites, "acionar_envio", lambda settings: ordem.append("job"))

    r = client.post(
        "/api/admin/usuarios",
        json={
            "email": "carla@alfa.example",
            "nome": "Carla Alfa",
            "perfil": "cliente_usuario",
            "clientes": [SLUG],
            "sessaoMinutos": 480,
        },
    )

    assert r.status_code == 202
    # sem o token do GitHub (ambiente de teste), o texto diz que espera a rodada
    assert r.json() == {
        "id": str(NOVA),
        "mensagem": "Convite na fila: sai na próxima rodada de envio, em até uma hora.",
    }
    assert pedidos == [
        (
            (ADMIN,),
            {
                "email": "carla@alfa.example",
                "nome": "Carla Alfa",
                "perfil": "cliente_usuario",
                "clientes": [SLUG],
                "sessao_minutos": 480,
                "admin_dominios": get_settings().admin_dominios,
            },
        )
    ]
    # o job só é acionado depois do commit: antes, ele não acharia o pedido
    assert ordem == ["commit", "job"]


def test_convidar_sem_empresa_e_sem_sessao(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pedidos: list[Any] = []

    def pedir(conn: Any, *a: Any, **k: Any) -> UUID:
        pedidos.append(k)
        return NOVA

    monkeypatch.setattr(convites, "pedir", pedir)

    r = client.post(
        "/api/admin/usuarios",
        json={"email": "carla@alfa.example", "nome": "Carla", "perfil": "cliente_principal"},
    )

    assert r.status_code == 202
    assert (pedidos[0]["clientes"], pedidos[0]["sessao_minutos"]) == ([], None)


def test_reenviar_convite(
    client: TestClient, estado: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    chamadas: list[tuple[Any, ...]] = []
    acionados: list[Any] = []
    monkeypatch.setattr(convites, "repedir", lambda conn, *a: chamadas.append(a))
    monkeypatch.setattr(convites, "acionar_envio", acionados.append)

    r = client.post(f"/api/admin/usuarios/{BRUNO}/convite")

    assert r.status_code == 202
    assert r.json()["id"] == str(BRUNO)
    assert chamadas == [(ADMIN, BRUNO)]
    assert estado["confirmadas"] == 1
    assert len(acionados) == 1


@pytest.mark.parametrize(
    ("erro", "status"),
    [
        (RegraInvalida("Este e-mail já tem cadastro no Overnex."), 422),
        (NaoEncontrado("Pessoa não encontrada."), 404),
    ],
)
def test_convite_recusado_nao_confirma_nem_aciona_o_job(
    client: TestClient,
    estado: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    erro: Exception,
    status: int,
) -> None:
    def recusar(*a: Any, **k: Any) -> None:
        raise erro

    acionados: list[Any] = []
    monkeypatch.setattr(convites, "pedir", recusar)
    monkeypatch.setattr(convites, "repedir", recusar)
    monkeypatch.setattr(convites, "acionar_envio", acionados.append)
    novo = {"email": "carla@alfa.example", "nome": "Carla", "perfil": "cliente_usuario"}

    for rota, corpo in (("", novo), (f"/{BRUNO}/convite", None)):
        r = client.post(f"/api/admin/usuarios{rota}", json=corpo)
        assert (r.status_code, r.json()) == (status, {"detail": str(erro)})
    assert estado["confirmadas"] == 0
    assert acionados == []


@pytest.mark.parametrize(
    "corpo",
    [
        {"nome": "Carla", "perfil": "cliente_usuario"},
        {"email": "carla@alfa.example", "perfil": "cliente_usuario"},
        {"email": "carla@alfa.example", "nome": "", "perfil": "cliente_usuario"},
        {"email": "carla@alfa.example", "nome": "Carla"},
        {"email": "carla@alfa.example", "nome": "Carla", "perfil": "admin", "clientes": "alfa"},
    ],
)
def test_convite_fora_do_formato_422(
    client: TestClient, estado: dict[str, Any], corpo: dict[str, Any]
) -> None:
    assert client.post("/api/admin/usuarios", json=corpo).status_code == 422
    assert estado["gravacoes"] == []
    assert estado["confirmadas"] == 0


def test_openapi_tem_as_rotas_da_gestao(client: TestClient) -> None:
    esquema = client.app.openapi()  # type: ignore[attr-defined]
    rotas = {
        (metodo.upper(), caminho)
        for caminho, metodos in esquema["paths"].items()
        if caminho.startswith("/api/admin")
        for metodo in metodos
    }
    assert rotas == {
        ("GET", "/api/admin/gestao"),
        ("POST", "/api/admin/clientes"),
        ("GET", "/api/admin/clientes/{slug}"),
        ("PUT", "/api/admin/clientes/{slug}"),
        ("PUT", "/api/admin/clientes/{slug}/paineis/{codigo}"),
        ("PUT", "/api/admin/clientes/{slug}/perfis/{perfil}/paineis/{codigo}"),
        ("PUT", "/api/admin/clientes/{slug}/preferencias/{id}"),
        ("PUT", "/api/admin/clientes/{slug}/feriados/{data}"),
        ("DELETE", "/api/admin/clientes/{slug}/feriados/{data}"),
        ("GET", "/api/admin/usuarios"),
        ("POST", "/api/admin/usuarios"),
        ("POST", "/api/admin/usuarios/{id}/convite"),
        ("PUT", "/api/admin/usuarios/{id}"),
        ("PUT", "/api/admin/usuarios/{id}/sessao"),
        ("PUT", "/api/admin/usuarios/{id}/acesso"),
    }
    for nome in ("GestaoPayload", "ClientePayload", "UsuariosPayload", "ConvitePedido"):
        assert nome in esquema["components"]["schemas"]

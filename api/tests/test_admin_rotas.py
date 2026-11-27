"""Rotas da Gestão (/api/admin/*) — sem banco.

A sessão é falsa: a checagem de acesso lê os fatos de uma conexão que só responde a
seg.contexto_acesso; as leituras (uma ida ao banco) são trocadas por linhas, e a confirmação
da gravação por um registro.
"""

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from psycopg import errors

from app.admin import clientes, convites, textos, usuarios
from app.admin import router as admin_router
from app.admin.usuarios import NaoEncontrado, RegraInvalida
from app.core.config import get_settings
from app.core.db import Sessao, get_sessao
from app.main import create_app
from app.seguranca import setores
from app.seguranca.auth import UsuarioToken
from app.telas.paineis import router as paineis_router
from app.telas.visao_geral import preferencias
from app.telas.visao_geral import router as visao_geral_router

ADMIN = UUID("d1000000-0000-4000-8000-0000000000ad")
ANA = UUID("d1000000-0000-4000-8000-00000000000a")
BRUNO = UUID("d1000000-0000-4000-8000-00000000000b")
CLIENTE_A = UUID("c1000000-0000-4000-8000-00000000000a")
EMPRESA_A1 = UUID("e1000000-0000-4000-8000-0000000000a1")
EMPRESA_A2 = UUID("e1000000-0000-4000-8000-0000000000a2")
CLIENTE_B = UUID("c1000000-0000-4000-8000-00000000000b")
AGORA = datetime(2026, 11, 10, 14, 30, tzinfo=textos.FORTALEZA)
SLUG = "padrao-lann-alfa"
# os níveis do catálogo, na ordem (o front dá a cor da etiqueta pela posição)
NIVEIS = [
    {"id": "basico", "rotulo": "Universal Básico"},
    {"id": "completo", "rotulo": "Universal Completo"},
    {"id": "distribuidora", "rotulo": "Módulo Distribuidora"},
]


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
        # o banco recusa a gravação (ex.: chave estrangeira): nada é gravado
        erro = self.estado.pop("erro", None)
        if erro is not None:
            raise erro
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
            {"id": CLIENTE_A, "nome": "Padrão LANN Alfa", "slug": SLUG},
            {"id": CLIENTE_B, "nome": "Padrão LANN Beta", "slug": "padrao-lann-beta"},
        ],
        # setores das unidades: a Alfa tem unidades da Distribuidora; a Beta, nenhuma com setor
        "setores_dos_clientes": [{"cliente_id": CLIENTE_A, "setor": "distribuidora"}],
        "setores": [
            {"id": "distribuidora", "rotulo": "Distribuidora"},
            {"id": "industria", "rotulo": "Indústria"},
            {"id": "varejo", "rotulo": "Varejo"},
            {"id": "servicos", "rotulo": "Serviços"},
        ],
        "setores_do_cliente": [{"setor": "distribuidora"}],
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
        "cliente": [{"id": CLIENTE_A, "nome": "Padrão LANN Alfa", "slug": SLUG, "logo_url": None}],
        # as unidades da empresa, fora de ordem
        "unidades": [
            {
                "id": EMPRESA_A1,
                "nome": "Alfa Indústria",
                "setor": "distribuidora",
                "segmento": "Alimentos",
                "grupo": "Fábrica",
                "uf": "CE",
                "cidade": "Fortaleza",
                "bairro": None,
                "endereco": None,
            },
            {
                "id": EMPRESA_A2,
                "nome": "Alfa Comércio",
                "setor": "distribuidora",
                "segmento": "Alimentos",
                "grupo": "Atacado",
                "uf": None,
                "cidade": None,
                "bairro": None,
                "endereco": None,
            },
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
            {"perfil": "cliente_principal", "codigo_tela": "VG-01", "ve_dado_nominal": False},
            {"perfil": "cliente_principal", "codigo_tela": "COM-01", "ve_dado_nominal": False},
            {"perfil": "cliente_principal", "codigo_tela": "PES-01", "ve_dado_nominal": True},
            {"perfil": "cliente_usuario", "codigo_tela": "VG-01", "ve_dado_nominal": False},
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
    ("PUT", f"/api/admin/clientes/{SLUG}", {"nome": "Alfa"}),
    (
        "PUT",
        f"/api/admin/clientes/{SLUG}/unidades/{EMPRESA_A1}",
        {
            "nome": "Alfa 1",
            "setor": None,
            "segmento": None,
            "grupo": None,
            "uf": None,
            "cidade": None,
            "bairro": None,
            "endereco": None,
        },
    ),
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
    ("DELETE", f"/api/admin/usuarios/{BRUNO}", None),
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
    motivo = "perfil sem acesso à Gestão" if (metodo, rota) in LEITURAS else "perfil não é admin"
    assert log[5:8] == ("negado", 3, motivo)
    assert estado["gravacoes"] == []
    assert estado["confirmadas"] == 0


# ---------- consultor: só as duas leituras, da carteira dele ----------

# as rotas que o Consultor por carteira também lê
LEITURAS = {("GET", "/api/admin/gestao"), ("GET", f"/api/admin/clientes/{SLUG}")}
CAIO = UUID("d1000000-0000-4000-8000-0000000000c0")


def _consultor(**mudancas: Any) -> dict[str, Any]:
    """Caio, consultor com A e B na carteira."""
    return _fatos(
        usuario_id=CAIO,
        perfil="consultor",
        vinculos=[
            {"cliente_id": str(CLIENTE_A), "papel": "gestor"},
            {"cliente_id": str(CLIENTE_B), "papel": "gestor"},
        ],
        **mudancas,
    )


@pytest.mark.parametrize(
    ("metodo", "rota", "corpo"), [r for r in ROTAS if (r[0], r[1]) not in LEITURAS]
)
def test_consultor_nao_grava_nem_ve_pessoas(
    client: TestClient, estado: dict[str, Any], metodo: str, rota: str, corpo: Any
) -> None:
    estado["fatos"] = _consultor(cliente_id=CLIENTE_A)

    r = client.request(metodo, rota, json=corpo)

    assert r.status_code == 403
    (log,) = estado["sessoes"][0].logs
    assert log[5:8] == ("negado", 3, "perfil não é admin")
    assert estado["gravacoes"] == []


def test_consultor_ve_a_barra_so_de_leitura(client: TestClient, estado: dict[str, Any]) -> None:
    estado["fatos"] = _consultor()

    r = client.get("/api/admin/gestao")

    assert r.status_code == 200
    corpo = r.json()
    # as empresas são as que o RLS devolve (a carteira, migration 33)
    assert [c["slug"] for c in corpo["clientes"]] == [SLUG, "padrao-lann-beta"]
    assert corpo["perfis"] == []
    assert corpo["sessoes"] == []
    assert corpo["permissoes"] == {"criarEmpresa": False, "usuarios": False}
    (log,) = estado["sessoes"][0].logs
    assert log[5] == "permitido"


def test_consultor_ve_so_implantacao_unidades_e_prontidao(
    client: TestClient, estado: dict[str, Any]
) -> None:
    estado["fatos"] = _consultor(cliente_id=CLIENTE_A)

    r = client.get(f"/api/admin/clientes/{SLUG}")

    assert r.status_code == 200
    corpo = r.json()
    assert [a["id"] for a in corpo["abas"]] == ["implantacao", "unidades", "dados"]
    assert [a["rotulo"] for a in corpo["abas"]] == [
        "Implantação",
        "Unidades",
        "Prontidão dos dados",
    ]
    # as unidades da carteira dele (o RLS recorta), na ordem da árvore
    assert [u["nome"] for u in corpo["unidades"]] == ["Alfa Comércio", "Alfa Indústria"]
    assert corpo["cliente"]["setores"] == [{"id": "distribuidora", "rotulo": "Distribuidora"}]
    assert corpo["permissoes"] == {"editar": False, "convidar": False, "unidades": False}
    assert corpo["opcoesSetor"] == []
    # a aba Implantação: as seções com os painéis; os níveis iguais aos do admin
    assert [k["id"] for k in corpo["camadas"]] == ["comercial", "financeiro", "pessoas", "estoque"]
    assert all(k["paineis"] for k in corpo["camadas"])
    assert corpo["niveis"] == NIVEIS
    assert [f["tabela"] for f in corpo["fontes"]] == ["f_folha_alfa", "f_vendas_alfa"]
    # nada das outras abas, nem a contagem de usuários
    for campo in ("paineis", "usuarios", "preferencias", "feriados", "cargas", "menu"):
        assert corpo[campo] == [], campo
    assert corpo["acesso"] == {"perfis": [], "linhas": []}
    rotulos = [c["rotulo"] for c in corpo["resumo"]]
    assert len(rotulos) == 2
    assert rotulos[1].startswith("Última carga")
    assert estado["slug_pedido"] == SLUG


def test_consultor_nao_abre_empresa_fora_da_carteira(
    client: TestClient, estado: dict[str, Any]
) -> None:
    # o banco não escolhe cliente fora do vínculo: o slug pedido fica sem cliente
    estado["fatos"] = _consultor()

    r = client.get("/api/admin/clientes/gama-fora")

    assert r.status_code == 403
    assert r.json() == {"detail": "Acesso negado."}
    (log,) = estado["sessoes"][0].logs
    assert log[5:7] == ("negado", 2)


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
    # sem o "Ramo" (migration 37): setor e segmento são das unidades
    assert "segmento" not in alfa
    assert "segmento" not in beta
    assert [(c["id"], c["estado"], c["estadoRotulo"], c["status"]) for c in alfa["camadas"]] == [
        ("comercial", "ativa", "Ativa", "saudavel"),
        # o Receber e pagar ligado (sem carga de títulos); Resultado e Fluxo de caixa não
        ("financeiro", "implantacao", "Em implantação", "atencao"),
        ("pessoas", "desligada", "Desligada", "neutro"),
        # a Alfa tem unidade da Distribuidora: tem a seção Estoque
        ("estoque", "desligada", "Desligada", "neutro"),
    ]
    assert {c["estado"] for c in beta["camadas"]} == {"desligada"}
    # a Beta, sem unidade com setor, não
    assert [c["id"] for c in beta["camadas"]] == ["comercial", "financeiro", "pessoas"]
    # os setores das unidades de cada empresa
    assert alfa["setores"] == [{"id": "distribuidora", "rotulo": "Distribuidora"}]
    assert beta["setores"] == []
    # sem as opções de setor: o setor é da unidade, cadastrado por SQL
    assert "setores" not in corpo
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
    assert corpo["permissoes"] == {"criarEmpresa": True, "usuarios": True}


def test_admin_ve_as_sete_abas_e_grava(client: TestClient, estado: dict[str, Any]) -> None:
    _no_cliente(estado)
    corpo = client.get(f"/api/admin/clientes/{SLUG}").json()
    assert [a["id"] for a in corpo["abas"]] == [
        "implantacao",
        "unidades",
        "dados",
        "paineis",
        "preferencias",
        "acesso",
        "cargas",
    ]
    assert corpo["permissoes"] == {"editar": True, "convidar": True, "unidades": True}
    assert [o["id"] for o in corpo["opcoesSetor"]] == [
        "distribuidora",
        "industria",
        "varejo",
        "servicos",
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
        "setores": [{"id": "distribuidora", "rotulo": "Distribuidora"}],
        "logoUrl": None,
    }
    # aba Unidades: na ordem da árvore (setor, grupo, nome)
    assert corpo["unidades"] == [
        {
            "id": str(EMPRESA_A2),
            "nome": "Alfa Comércio",
            "setor": {"id": "distribuidora", "rotulo": "Distribuidora"},
            "segmento": "Alimentos",
            "grupo": "Atacado",
            "uf": None,
            "cidade": None,
            "bairro": None,
            "endereco": None,
        },
        {
            "id": str(EMPRESA_A1),
            "nome": "Alfa Indústria",
            "setor": {"id": "distribuidora", "rotulo": "Distribuidora"},
            "segmento": "Alimentos",
            "grupo": "Fábrica",
            "uf": "CE",
            "cidade": "Fortaleza",
            "bairro": None,
            "endereco": None,
        },
    ]
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
    # implantação: as seções do menu, cada uma com os painéis e o nível
    assert [
        (k["id"], k["rotulo"], k["estado"], [(p["codigo"], p["nivel"]) for p in k["paineis"]])
        for k in corpo["camadas"]
    ] == [
        (
            "comercial",
            "Comercial",
            "implantacao",
            [("VG-01", "basico"), ("COM-01", "basico"), ("COM-02", "basico"), ("COM-03", "basico")],
        ),
        (
            "financeiro",
            "Financeiro",
            "desligada",
            [("FIN-02", "completo"), ("FIN-03", "basico"), ("FIN-04", "completo")],
        ),
        ("pessoas", "Pessoas", "implantacao", [("PES-01", "basico")]),
        ("estoque", "Estoque", "desligada", [("EST-01", "distribuidora")]),
    ]
    assert corpo["camadas"][2]["paineis"] == [
        {"codigo": "PES-01", "nome": "Pessoas", "nivel": "basico"}
    ]
    assert "grupo" not in corpo["camadas"][0]
    # os níveis do catálogo, inteiros e na ordem (a cor da etiqueta vai pela posição)
    assert corpo["niveis"] == NIVEIS

    # painéis: por seção e na ordem do menu
    assert [(p["codigo"], p["secao"], p["nivel"]) for p in corpo["paineis"]] == [
        ("VG-01", "Comercial", "basico"),
        ("COM-01", "Comercial", "basico"),
        ("COM-02", "Comercial", "basico"),
        ("COM-03", "Comercial", "basico"),
        ("FIN-02", "Financeiro", "completo"),
        ("FIN-03", "Financeiro", "basico"),
        ("FIN-04", "Financeiro", "completo"),
        ("PES-01", "Pessoas", "basico"),
        ("EST-01", "Estoque", "distribuidora"),
    ]
    paineis = {p["codigo"]: p for p in corpo["paineis"]}
    assert "grupo" not in paineis["VG-01"]
    # o Estoque (a Alfa é Distribuidora) liga quando houver carga de estoque
    assert paineis["EST-01"]["nota"] == "Sem carga de f_estoque_* nesta empresa."
    assert (paineis["VG-01"]["ligado"], paineis["VG-01"]["podeLigar"]) == (True, True)
    assert (paineis["PES-01"]["ligado"], paineis["PES-01"]["podeLigar"]) == (True, False)
    assert paineis["PES-01"]["nota"] == "Sem carga de f_folha_* nesta empresa."
    assert paineis["FIN-03"]["ligado"] is False

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
            "nomes": None,
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
            "nomes": None,
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
            # Pessoas abre sem nomes: a chave "vê nomes" por perfil
            "nomes": {
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
    # menu do Cliente principal, nos grupos do menu do app: Pessoas está na matriz, mas sem
    # dado não aparece
    assert corpo["menu"] == [
        {"grupo": "Visão", "itens": [{"nome": "Visão geral"}]},
        {"grupo": "Comercial", "itens": [{"nome": "Vendas"}]},
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
        # só quem já está desativado
        "podeApagar": False,
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
        (
            "perfis/admin/paineis/PES-01/nomes",
            {"veNomes": False},
            422,
            "Admin BORD vê tudo e não pode ser alterado.",
        ),
        (
            "perfis/Financeiro/paineis/PES-01/nomes",
            {"veNomes": True},
            404,
            "Perfil não encontrado.",
        ),
        ("perfis/consultor/paineis/XX-99/nomes", {"veNomes": True}, 404, "Painel não encontrado."),
        # Equipe comercial: ver o painel já é ver os nomes
        (
            "perfis/consultor/paineis/COM-02/nomes",
            {"veNomes": True},
            422,
            "Este painel não tem nomes para liberar.",
        ),
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


def test_ve_nomes_troca_a_marca_da_linha(client: TestClient, estado: dict[str, Any]) -> None:
    _no_cliente(estado)
    rota = f"/api/admin/clientes/{SLUG}/perfis/cliente_principal/paineis/PES-01/nomes"

    # o perfil não tem a PES-01: nada a trocar, nada confirmado
    r = client.put(rota, json={"veNomes": True})
    assert (r.status_code, r.json()) == (422, {"detail": "Este perfil não vê o painel."})
    assert estado["confirmadas"] == 0

    estado["gravacoes"].clear()
    perfil_id = UUID("f2000000-0000-4000-8000-0000000000a1")
    estado["devolve"] = {"perfil_id": perfil_id, "ve_dado_nominal": False}
    r = client.put(rota, json={"veNomes": True})

    assert r.status_code == 204
    assert [g[0] for g in estado["gravacoes"]] == [
        clientes.SQL_TIRAR_PARA_TROCAR,
        clientes.SQL_POR_DE_VOLTA,
        usuarios.SQL_LOG,
    ]
    assert estado["gravacoes"][0][1] == {
        "cliente": CLIENTE_A,
        "perfil": "cliente_principal",
        "codigo": "PES-01",
    }
    assert estado["gravacoes"][1][1] == {
        "perfil_id": perfil_id,
        "codigo": "PES-01",
        "nominal": True,
    }
    log = estado["gravacoes"][2][1]
    assert (log["autor"], log["operacao"]) == (ADMIN, "update")
    assert '"ve_nomes": false' in log["antes"]
    assert estado["confirmadas"] == 1
    # o cache dos painéis já separa quem vê nomes: as respostas prontas ficam
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
    r = client.post("/api/admin/clientes", json={"nome": "  Nilo  Maia Distribuição "})

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
    }
    log = estado["gravacoes"][1][1]
    assert log["autor"] == ADMIN
    assert json.loads(log["depois"]) == {
        "nome": "Nilo Maia Distribuição",
        "slug": "nilo-maia-distribuicao",
    }
    assert estado["confirmadas"] == 1


def test_criar_empresa_com_o_slug_escolhido(client: TestClient, estado: dict[str, Any]) -> None:
    r = client.post("/api/admin/clientes", json={"nome": "PJ Cortes", "slug": "pj"})

    assert (r.status_code, r.json()["slug"]) == (201, "pj")
    assert estado["gravacoes"][0][1] == {"nome": "PJ Cortes", "slug": "pj"}


@pytest.mark.parametrize("metodo", ["post", "put"])
@pytest.mark.parametrize(
    "extra",
    [
        # o setor é da unidade (migration 35)
        {"setor": "varejo"},
        # o "Ramo" saiu (migration 37): o segmento também é só da unidade
        {"segmento": "Frigorífico"},
        {"segmento": None},
    ],
)
def test_empresa_nao_tem_setor_nem_segmento(
    client: TestClient, estado: dict[str, Any], metodo: str, extra: dict[str, Any]
) -> None:
    # o corpo com o campo é recusado no formato, antes de chegar ao banco
    _no_cliente(estado)
    url = "/api/admin/clientes" if metodo == "post" else f"/api/admin/clientes/{SLUG}"
    r = getattr(client, metodo)(url, json={"nome": "Alfa", **extra})
    assert r.status_code == 422
    assert [e["loc"] for e in r.json()["detail"]] == [["body", *extra]]
    assert estado["gravacoes"] == []
    assert estado["confirmadas"] == 0


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


def test_mudar_empresa_troca_o_nome_e_apaga_as_respostas_prontas(
    client: TestClient, estado: dict[str, Any]
) -> None:
    _no_cliente(estado)
    estado["devolve"] = {"nome_antes": "Padrão LANN Alfa"}

    r = client.put(f"/api/admin/clientes/{SLUG}", json={"nome": " PJ  Cortes "})

    assert r.status_code == 204
    assert [g[0] for g in estado["gravacoes"]] == [clientes.SQL_MUDAR_EMPRESA, usuarios.SQL_LOG]
    assert estado["gravacoes"][0][1] == {"cliente": CLIENTE_A, "nome": "PJ Cortes"}
    log = estado["gravacoes"][1][1]
    assert (log["autor"], log["operacao"]) == (ADMIN, "update")
    # a auditoria guarda só o nome, antes e depois
    assert json.loads(log["antes"]) == {"nome": "Padrão LANN Alfa"}
    assert json.loads(log["depois"]) == {"nome": "PJ Cortes"}
    assert estado["confirmadas"] == 1
    # o nome aparece nas telas
    assert estado["apagadas"] == [("VG", CLIENTE_A), ("paineis", CLIENTE_A)]


def test_mudar_empresa_com_nome_em_branco(client: TestClient, estado: dict[str, Any]) -> None:
    _no_cliente(estado)
    r = client.put(f"/api/admin/clientes/{SLUG}", json={"nome": "   "})
    assert (r.status_code, r.json()) == (422, {"detail": "Informe o nome da empresa."})
    assert estado["gravacoes"] == []
    assert estado["confirmadas"] == 0


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


# ---------- setores das unidades (migrations 34 e 35) ----------


def test_gestao_traz_os_setores_das_unidades_de_cada_empresa_na_ordem(
    client: TestClient, estado: dict[str, Any]
) -> None:
    estado["linhas"]["setores_dos_clientes"] = [
        {"cliente_id": CLIENTE_A, "setor": "varejo"},
        {"cliente_id": CLIENTE_A, "setor": "distribuidora"},
    ]
    corpo = client.get("/api/admin/gestao").json()
    alfa, beta = corpo["clientes"]
    # na ordem de cfg.setor
    assert alfa["setores"] == [
        {"id": "distribuidora", "rotulo": "Distribuidora"},
        {"id": "varejo", "rotulo": "Varejo"},
    ]
    assert beta["setores"] == []


def test_consultor_ve_os_setores_das_empresas_da_carteira(
    client: TestClient, estado: dict[str, Any]
) -> None:
    estado["fatos"] = _fatos(perfil="consultor")
    corpo = client.get("/api/admin/gestao").json()
    assert corpo["clientes"][0]["setores"] == [{"id": "distribuidora", "rotulo": "Distribuidora"}]


def test_painel_de_outro_setor_fica_travado_e_sai_da_matriz_do_menu_e_das_camadas(
    client: TestClient, estado: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pessoas como se fosse só do Varejo; a Alfa é Distribuidora (e o painel está ligado)
    monkeypatch.setattr(setores, "SETORES_DA_TELA", {"PES-01": frozenset({"varejo"})})
    estado["linhas"]["fontes"][0]["data_snapshot"] = date(2026, 11, 9)  # a folha tem dado
    _no_cliente(estado)

    corpo = client.get(f"/api/admin/clientes/{SLUG}").json()

    pessoas = {p["codigo"]: p for p in corpo["paineis"]}["PES-01"]
    assert pessoas == {
        "codigo": "PES-01",
        "nome": "Pessoas",
        "secao": "Pessoas",
        "nivel": "basico",
        "ligado": True,
        "podeLigar": False,
        "status": "neutro",
        "statusRotulo": "indisponível",
        "nota": "Só para empresas com unidade do setor Varejo.",
    }
    assert "PES-01" not in [linha["codigo"] for linha in corpo["acesso"]["linhas"]]
    assert all(i["nome"] != "Pessoas" for g in corpo["menu"] for i in g["itens"])
    # a seção Pessoas só tem esse painel: some, e o resumo conta 3 seções (com a Estoque)
    assert [k["id"] for k in corpo["camadas"]] == ["comercial", "financeiro", "estoque"]
    assert corpo["resumo"][0]["rotulo"] == "0 de 3 camadas ativas"
    # a Ana via 3 painéis no ar; sem Pessoas, 2
    assert corpo["usuarios"][0]["paineisQueVe"] == "2 de 2"

    # na barra lateral, a Alfa também perde a seção; a Beta (sem unidade com setor) também,
    # e não tem a Estoque
    lista = client.get("/api/admin/gestao").json()["clientes"]
    assert [[k["id"] for k in c["camadas"]] for c in lista] == [
        ["comercial", "financeiro", "estoque"],
        ["comercial", "financeiro"],
    ]


def test_painel_do_setor_da_empresa_segue_normal(
    client: TestClient, estado: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setores, "SETORES_DA_TELA", {"PES-01": frozenset({"distribuidora"})})
    _no_cliente(estado)

    corpo = client.get(f"/api/admin/clientes/{SLUG}").json()

    pessoas = {p["codigo"]: p for p in corpo["paineis"]}["PES-01"]
    # sem dado de folha: a nota é a de sempre
    assert pessoas["nota"] == "Sem carga de f_folha_* nesta empresa."
    assert "PES-01" in [linha["codigo"] for linha in corpo["acesso"]["linhas"]]
    assert "pessoas" in [k["id"] for k in corpo["camadas"]]


def test_ligar_painel_de_outro_setor_e_422_e_desligar_pode(
    client: TestClient, estado: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setores, "SETORES_DA_TELA", {"COM-01": frozenset({"varejo", "industria"})})
    _no_cliente(estado)

    r = client.put(f"/api/admin/clientes/{SLUG}/paineis/COM-01", json={"ligado": True})
    assert (r.status_code, r.json()) == (
        422,
        {"detail": "Este painel é só para empresas com unidade dos setores Indústria e Varejo."},
    )
    assert estado["gravacoes"] == []

    # desligar não confere o setor (o COM-01 está ligado nas linhas do teste)
    r = client.put(f"/api/admin/clientes/{SLUG}/paineis/COM-01", json={"ligado": False})
    assert r.status_code == 204


def test_ligar_painel_numa_empresa_sem_unidade_com_setor_e_422(
    client: TestClient, estado: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setores, "SETORES_DA_TELA", {"FIN-03": frozenset({"distribuidora"})})
    estado["linhas"]["setores_do_cliente"] = []
    _no_cliente(estado)

    r = client.put(f"/api/admin/clientes/{SLUG}/paineis/FIN-03", json={"ligado": True})
    assert (r.status_code, r.json()) == (
        422,
        {"detail": "Este painel é só para empresas com unidade do setor Distribuidora."},
    )


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


def test_apagar_usuario_confirma_e_aciona_o_job(
    client: TestClient, estado: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    chamadas: list[tuple[Any, ...]] = []
    acionados: list[Any] = []
    monkeypatch.setattr(usuarios, "apagar", lambda conn, *a: chamadas.append(a))
    monkeypatch.setattr(convites, "acionar_envio", acionados.append)

    r = client.delete(f"/api/admin/usuarios/{BRUNO}")

    assert (r.status_code, r.content) == (204, b"")
    assert chamadas == [(ADMIN, BRUNO)]
    assert estado["confirmadas"] == 1
    # o job apaga o login no Supabase Auth
    assert len(acionados) == 1


@pytest.mark.parametrize(
    ("erro", "status"),
    [
        (RegraInvalida("Desative o acesso antes de apagar."), 422),
        (NaoEncontrado("Pessoa não encontrada."), 404),
    ],
)
def test_apagar_usuario_recusado(
    client: TestClient,
    estado: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    erro: Exception,
    status: int,
) -> None:
    def recusar(*a: Any, **k: Any) -> None:
        raise erro

    acionados: list[Any] = []
    monkeypatch.setattr(usuarios, "apagar", recusar)
    monkeypatch.setattr(convites, "acionar_envio", acionados.append)

    r = client.delete(f"/api/admin/usuarios/{BRUNO}")

    assert (r.status_code, r.json()) == (status, {"detail": str(erro)})
    assert (estado["confirmadas"], acionados) == (0, [])


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
        ("PUT", "/api/admin/clientes/{slug}/unidades/{id}"),
        ("PUT", "/api/admin/clientes/{slug}/paineis/{codigo}"),
        ("PUT", "/api/admin/clientes/{slug}/perfis/{perfil}/paineis/{codigo}"),
        ("PUT", "/api/admin/clientes/{slug}/perfis/{perfil}/paineis/{codigo}/nomes"),
        ("PUT", "/api/admin/clientes/{slug}/preferencias/{id}"),
        ("PUT", "/api/admin/clientes/{slug}/feriados/{data}"),
        ("DELETE", "/api/admin/clientes/{slug}/feriados/{data}"),
        ("GET", "/api/admin/usuarios"),
        ("POST", "/api/admin/usuarios"),
        ("POST", "/api/admin/usuarios/{id}/convite"),
        ("PUT", "/api/admin/usuarios/{id}"),
        ("PUT", "/api/admin/usuarios/{id}/sessao"),
        ("PUT", "/api/admin/usuarios/{id}/acesso"),
        ("DELETE", "/api/admin/usuarios/{id}"),
    }
    for nome in ("GestaoPayload", "ClientePayload", "UsuariosPayload", "ConvitePedido"):
        assert nome in esquema["components"]["schemas"]


# ---------- editar a unidade (migration 36) ----------

UNIDADE = f"/api/admin/clientes/{SLUG}/unidades/{EMPRESA_A1}"
CORPO_UNIDADE: dict[str, Any] = {
    "nome": "  Alfa   Indústria 2 ",
    "setor": "industria",
    "segmento": " Alimentos ",
    "grupo": "  ",
    "uf": "ce",
    "cidade": "Fortaleza",
    "bairro": None,
    "endereco": "Av. Central, 100",
}


def _antes() -> dict[str, Any]:
    return {
        "antes": {
            "nome": "Alfa Indústria",
            "setor": "distribuidora",
            "segmento": "Alimentos",
            "grupo": "Fábrica",
            "uf": "CE",
            "cidade": "Fortaleza",
            "bairro": None,
            "endereco": None,
        },
        "conflito": False,
        "mudou": True,
    }


def test_editar_unidade_grava_limpa_registra_e_apaga_as_respostas(
    client: TestClient, estado: dict[str, Any]
) -> None:
    _no_cliente(estado)
    estado["devolve"] = _antes()

    r = client.put(UNIDADE, json=CORPO_UNIDADE)

    assert (r.status_code, r.content) == (204, b"")
    assert [g[0] for g in estado["gravacoes"]] == [clientes.SQL_MUDAR_UNIDADE, usuarios.SQL_LOG]
    assert estado["gravacoes"][0][1] == {
        "nome": "Alfa Indústria 2",
        "setor": "industria",
        "segmento": "Alimentos",
        "grupo": None,
        "uf": "CE",
        "cidade": "Fortaleza",
        "bairro": None,
        "endereco": "Av. Central, 100",
        "id": EMPRESA_A1,
        "cliente": CLIENTE_A,
    }
    log = estado["gravacoes"][1][1]
    assert (log["tabela"], log["registro"], log["operacao"]) == (
        "cfg.empresa",
        str(EMPRESA_A1),
        "update",
    )
    assert (json.loads(log["antes"])["grupo"], json.loads(log["depois"])["grupo"]) == (
        "Fábrica",
        None,
    )
    assert estado["confirmadas"] == 1
    # setor e grupo mudam o escopo das telas do cliente
    assert estado["apagadas"] == [("VG", CLIENTE_A), ("paineis", CLIENTE_A)]


@pytest.mark.parametrize(
    ("mudanca", "detalhe"),
    [
        ({"nome": "   "}, "Informe o nome da unidade."),
        ({"uf": "XX"}, "UF inválida."),
    ],
)
def test_editar_unidade_recusada_antes_do_banco(
    client: TestClient, estado: dict[str, Any], mudanca: dict[str, Any], detalhe: str
) -> None:
    _no_cliente(estado)
    r = client.put(UNIDADE, json=CORPO_UNIDADE | mudanca)
    assert (r.status_code, r.json()) == (422, {"detail": detalhe})
    assert estado["gravacoes"] == []
    assert estado["confirmadas"] == 0


def test_editar_unidade_nome_repetido_e_setor_invalido(
    client: TestClient, estado: dict[str, Any]
) -> None:
    _no_cliente(estado)
    estado["devolve"] = {**_antes(), "conflito": True, "mudou": False}
    r = client.put(UNIDADE, json=CORPO_UNIDADE)
    assert (r.status_code, r.json()) == (
        422,
        {"detail": "Já existe uma unidade com esse nome nesta empresa."},
    )

    # quem confere o setor é a chave estrangeira de cfg.empresa.setor
    estado["erro"] = errors.ForeignKeyViolation("setor")
    r = client.put(UNIDADE, json=CORPO_UNIDADE | {"setor": "padaria"})
    assert (r.status_code, r.json()) == (422, {"detail": "Setor inválido."})
    assert estado["confirmadas"] == 0


def test_editar_unidade_de_outra_empresa_404(client: TestClient, estado: dict[str, Any]) -> None:
    _no_cliente(estado)
    estado["devolve"] = {"antes": None, "conflito": False, "mudou": False}
    r = client.put(UNIDADE, json=CORPO_UNIDADE)
    assert (r.status_code, r.json()) == (404, {"detail": "Unidade não encontrada."})
    assert estado["confirmadas"] == 0


def test_editar_unidade_campo_a_mais_ou_faltando_422(
    client: TestClient, estado: dict[str, Any]
) -> None:
    _no_cliente(estado)
    assert client.put(UNIDADE, json=CORPO_UNIDADE | {"cliente": "outro"}).status_code == 422
    sem_bairro = {k: v for k, v in CORPO_UNIDADE.items() if k != "bairro"}
    assert client.put(UNIDADE, json=sem_bairro).status_code == 422
    assert client.put(UNIDADE, json=CORPO_UNIDADE | {"nome": "x" * 121}).status_code == 422
    assert estado["gravacoes"] == []


def test_editar_unidade_barrada_pelo_banco_e_erro_de_servidor(
    estado: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    # sem a migration 36, o RLS não deixa o update achar a linha
    _no_cliente(estado)
    estado["devolve"] = {**_antes(), "mudou": False}
    app = create_app()
    app.dependency_overrides[get_sessao] = lambda: Sessao(
        conn=ConexaoFalsa(estado),  # type: ignore[arg-type]
        usuario=UsuarioToken(id_autenticacao=UUID(int=1)),
    )
    r = TestClient(app, raise_server_exceptions=False).put(UNIDADE, json=CORPO_UNIDADE)
    assert (r.status_code, r.json()) == (500, {"detail": "Erro interno."})
    assert "migration 36" in caplog.text
    assert estado["confirmadas"] == 0

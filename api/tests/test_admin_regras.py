"""Regras da Gestão (app/admin): situação das pessoas, camadas e painéis, e as mudanças de
perfil, sessão, acesso, painel ligado e matriz — sem banco (conexão falsa)."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from app.admin import catalogo, clientes, textos, usuarios
from app.admin.contrato import ItemCliente
from app.admin.usuarios import NaoEncontrado, Pessoa, RegraInvalida

AGORA = datetime(2026, 11, 10, 14, 30, tzinfo=textos.FORTALEZA)
ADMIN = UUID("d1000000-0000-4000-8000-0000000000ad")
BRUNO = UUID("d1000000-0000-4000-8000-00000000000b")
CLIENTE_A = UUID("c1000000-0000-4000-8000-00000000000a")
CLIENTE_B = UUID("c1000000-0000-4000-8000-00000000000b")
ALFA = ItemCliente(slug="alfa", nome="Alfa")
BETA = ItemCliente(slug="beta", nome="Beta")
DOMINIOS = "bordgestao.com.br"


def _pessoa(**mudancas: Any) -> Pessoa:
    base: dict[str, Any] = {
        "id": BRUNO,
        "nome": "Bruno Beta",
        "email": "bruno@beta.example",
        "perfil": "cliente_usuario",
        "ativo": True,
        "sessao_minutos": 240,
        "convidado_em": None,
        "primeiro_acesso_em": AGORA - timedelta(days=30),
        "desativado_em": None,
        "clientes": (ALFA,),
        "entradas_30d": 7,
        "ultimo_acesso": AGORA - timedelta(hours=5, minutes=16),
    }
    return Pessoa(**(base | mudancas))


# quem foi convidado e ainda não entrou
NOVO: dict[str, Any] = {"primeiro_acesso_em": None, "ultimo_acesso": None}


# ---------- textos ----------


@pytest.mark.parametrize(
    ("momento", "texto"),
    [
        (AGORA - timedelta(hours=5, minutes=16), "hoje, 9h14"),
        (AGORA - timedelta(days=1, hours=-4, minutes=-10), "ontem, 18h40"),
        (datetime(2026, 11, 4, 10, 22, tzinfo=textos.FORTALEZA), "04/11, 10h22"),
        (datetime(2025, 12, 31, 8, 5, tzinfo=textos.FORTALEZA), "31/12/2025, 8h05"),
        # o banco devolve em UTC: 12h14 UTC são 9h14 em Fortaleza
        (datetime(2026, 11, 10, 12, 14, tzinfo=UTC), "hoje, 9h14"),
    ],
)
def test_quando(momento: datetime, texto: str) -> None:
    assert textos.quando(momento, AGORA) == texto


def test_duracao_e_sessao() -> None:
    inicio = datetime(2026, 10, 13, 9, 0, tzinfo=textos.FORTALEZA)
    assert textos.duracao(inicio, inicio + timedelta(seconds=124)) == "2 min 04 s"
    assert textos.duracao(inicio, inicio + timedelta(seconds=58)) == "58 s"
    assert textos.duracao(inicio, inicio + timedelta(minutes=65)) == "1 h 05 min"
    assert textos.duracao(inicio, None) is None
    assert [textos.sessao(m) for m in usuarios.SESSOES] == [
        "30 min",
        "1 h",
        "4 h",
        "8 h",
        "12 h",
        "24 h",
    ]


# ---------- situação e o que a pessoa vê ao entrar ----------


@pytest.mark.parametrize(
    ("mudancas", "esperado"),
    [
        ({}, ("ativo", "Ativo", "último acesso hoje, 9h14", "saudavel")),
        (
            {"primeiro_acesso_em": None, "ultimo_acesso": None},
            ("ativo", "Ativo", "ainda não entrou", "saudavel"),
        ),
        (
            {
                "primeiro_acesso_em": None,
                "ultimo_acesso": None,
                "convidado_em": AGORA - timedelta(hours=22, minutes=20),
            },
            ("convite", "Convite enviado", "enviado ontem, 16h10", "atencao"),
        ),
        (
            {
                "primeiro_acesso_em": None,
                "ultimo_acesso": None,
                "convidado_em": datetime(2026, 11, 2, 9, 0, tzinfo=textos.FORTALEZA),
            },
            ("vencido", "Convite vencido", "venceu 03/11, 9h00", "critico"),
        ),
        (
            {"ativo": False, "desativado_em": AGORA - timedelta(minutes=30)},
            ("inativo", "Desativado", "desativado hoje, 14h00", "neutro"),
        ),
        # desativado ganha de convite pendente e de sem empresa
        (
            {"ativo": False, "primeiro_acesso_em": None, "convidado_em": AGORA, "clientes": ()},
            ("inativo", "Desativado", "", "neutro"),
        ),
        ({"clientes": ()}, ("sem-empresa", "Sem empresa", "último acesso hoje, 9h14", "critico")),
        # o admin não precisa de vínculo
        (
            {"perfil": "admin", "clientes": ()},
            ("ativo", "Ativo", "último acesso hoje, 9h14", "saudavel"),
        ),
        # convidado pela Gestão: o pedido ainda está na fila
        (
            NOVO | {"convite_estado": "fila", "convite_em": AGORA - timedelta(minutes=1)},
            ("fila", "Convite na fila", "pedido hoje, 14h29", "atencao"),
        ),
        # o job mandou: vale a data do convite
        (
            NOVO
            | {
                "convite_estado": "enviado",
                "convite_em": AGORA - timedelta(minutes=1),
                "convidado_em": AGORA - timedelta(minutes=1),
            },
            ("convite", "Convite enviado", "enviado hoje, 14h29", "atencao"),
        ),
        (
            NOVO
            | {
                "convite_estado": "falhou",
                "convite_em": AGORA - timedelta(minutes=1),
                "convite_motivo": "O limite de e-mails do Supabase foi atingido.",
            },
            ("nao-enviado", "Convite não enviado", "tentativa hoje, 14h29", "critico"),
        ),
        # o envio falhou, mas o comando mandou o convite depois: vale o convite
        (
            NOVO
            | {
                "convite_estado": "falhou",
                "convite_em": AGORA - timedelta(hours=2),
                "convidado_em": AGORA - timedelta(hours=1),
            },
            ("convite", "Convite enviado", "enviado hoje, 13h30", "atencao"),
        ),
        # convite enviado a quem não tem empresa: falta marcar a empresa
        (
            NOVO | {"convidado_em": AGORA - timedelta(hours=1), "clientes": ()},
            ("sem-empresa", "Sem empresa", "ainda não entrou", "critico"),
        ),
        # mas o convite que precisa de alguém (fila, não enviado, vencido) vem antes
        (
            NOVO
            | {
                "clientes": (),
                "convite_estado": "fila",
                "convite_em": AGORA - timedelta(minutes=1),
            },
            ("fila", "Convite na fila", "pedido hoje, 14h29", "atencao"),
        ),
        (
            NOVO
            | {
                "clientes": (),
                "convidado_em": datetime(2026, 11, 2, 9, 0, tzinfo=textos.FORTALEZA),
            },
            ("vencido", "Convite vencido", "venceu 03/11, 9h00", "critico"),
        ),
        # quem já entrou não volta para a fila
        (
            {"convite_estado": "fila", "convite_em": AGORA},
            ("ativo", "Ativo", "último acesso hoje, 9h14", "saudavel"),
        ),
    ],
)
def test_situacao(mudancas: dict[str, Any], esperado: tuple[str, str, str, str]) -> None:
    s = usuarios.situacao(_pessoa(**mudancas), AGORA, 24)
    assert (s.id, s.rotulo, s.quando, s.status) == esperado


def test_situacao_so_traz_o_motivo_quando_o_convite_nao_saiu() -> None:
    falhou = NOVO | {"convite_estado": "falhou", "convite_em": AGORA, "convite_motivo": "Motivo."}
    assert usuarios.situacao(_pessoa(**falhou), AGORA, 24).detalhe == "Motivo."
    assert usuarios.situacao(_pessoa(), AGORA, 24).detalhe is None


@pytest.mark.parametrize(
    ("mudancas", "rotulo"),
    [
        # já entrou: o caminho é "Esqueci a senha"
        ({}, None),
        (NOVO | {"convidado_em": AGORA}, "Reenviar convite"),
        (NOVO | {"convidado_em": AGORA - timedelta(days=3)}, "Reenviar convite"),
        (NOVO | {"convite_estado": "falhou", "convite_em": AGORA}, "Tentar de novo"),
        # já na fila: sem botão, para não pedir duas vezes
        (NOVO | {"convite_estado": "fila", "convite_em": AGORA}, None),
        (NOVO | {"convidado_em": AGORA, "ativo": False}, None),
    ],
)
def test_botao_de_mandar_o_convite_de_novo(mudancas: dict[str, Any], rotulo: str | None) -> None:
    acao = usuarios.usuario(_pessoa(**mudancas), AGORA, 24, 5, 2).convite
    assert (acao.rotulo if acao else None) == rotulo


@pytest.mark.parametrize(
    ("mudancas", "esperado"),
    [
        ({}, ("direto", "Abre direto em Alfa")),
        ({"clientes": (ALFA, BETA)}, ("lista", "Escolhe entre 2 empresas")),
        ({"clientes": ()}, ("nada", "Não vê nada")),
        (
            {"perfil": "admin", "clientes": ()},
            ("lista", "Abre a Gestão e escolhe entre as 5 empresas"),
        ),
        ({"ativo": False}, ("nada", "Não entra: acesso desativado")),
    ],
)
def test_ao_entrar(mudancas: dict[str, Any], esperado: tuple[str, str]) -> None:
    a = usuarios.ao_entrar(_pessoa(**mudancas), 5)
    assert (a.tipo, a.rotulo) == esperado


def test_usuario_e_filtros() -> None:
    def feito(admins: int = 2, **mudancas: Any) -> Any:
        return usuarios.usuario(_pessoa(**mudancas), AGORA, 24, 5, admins)

    bruno = feito()
    assert (bruno.perfil.rotulo, bruno.todos_os_clientes, bruno.pode_desativar) == (
        "Cliente usuário",
        False,
        True,
    )
    assert (bruno.acessos.entradas30d, bruno.acessos.ultimo) == (7, "hoje, 9h14")
    # sem nome cadastrado: a parte do e-mail antes do @
    assert feito(nome=None).nome == "bruno"

    admin = feito(perfil="admin", clientes=(ALFA,), sessao_minutos=1440)
    # o admin vê todas: os vínculos que sobraram não aparecem
    assert (admin.todos_os_clientes, admin.clientes, admin.pode_desativar) == (True, [], True)
    # o último admin ativo não oferece "Desativar acesso"; quem já está desativado também não
    assert feito(admins=1, perfil="admin").pode_desativar is False
    assert feito(ativo=False).pode_desativar is False

    lista = [
        bruno,
        admin,
        feito(clientes=()),
        feito(primeiro_acesso_em=None, convidado_em=AGORA),
        feito(primeiro_acesso_em=None, convidado_em=AGORA - timedelta(days=3)),
        # pela Gestão: na fila e não enviado também são convites pendentes
        feito(primeiro_acesso_em=None, convite_estado="fila", convite_em=AGORA),
        feito(primeiro_acesso_em=None, convite_estado="falhou", convite_em=AGORA),
    ]
    assert [(f.id, f.total, f.atencao) for f in usuarios.filtros(lista)] == [
        ("todos", 7, False),
        ("admin", 1, False),
        ("consultor", 0, False),
        ("cliente_principal", 0, False),
        ("cliente_usuario", 6, False),
        ("pendentes", 4, True),
        ("sem-empresa", 1, True),
    ]


# ---------- catálogo, camadas e painéis ----------

COMERCIAL = frozenset({"VG-01", "COM-01", "COM-02", "COM-03"})


def test_catalogo_tem_os_paineis_que_existem_e_a_matriz_padrao() -> None:
    assert [p.codigo for p in catalogo.PAINEIS] == [
        "VG-01",
        "COM-01",
        "COM-02",
        "COM-03",
        "FIN-03",
        "PES-01",
        "FIN-02",
        "FIN-04",
    ]
    padrao = {p.codigo: p.padrao for p in catalogo.PAINEIS}
    # Cliente usuário não vê Pessoas, Resultado nem Fluxo de caixa; Consultor não vê Pessoas
    assert {c for c, perfis in padrao.items() if "cliente_usuario" not in perfis} == {
        "PES-01",
        "FIN-02",
        "FIN-04",
    }
    assert {c for c, perfis in padrao.items() if "consultor" not in perfis} == {"PES-01"}
    assert all("cliente_principal" in perfis for perfis in padrao.values())
    # só as telas que mostram nomes de pessoas nascem com dado nominal na matriz
    assert {p.codigo for p in catalogo.PAINEIS if p.nominal} == {"COM-02", "FIN-03"}
    assert catalogo.usada_por("f_folha_pj_cortes") == ["Pessoas"]
    assert "Vendas" in catalogo.usada_por("f_vendas_pj")
    assert catalogo.usada_por("f_estoque_nilo_maia") == []


def test_camadas_ativa_em_implantacao_e_desligada() -> None:
    com_carga = frozenset({"f_vendas_pj", "f_receitas_pj"})
    ligadas = COMERCIAL | {"PES-01"}

    lista = {c.id: c for c in clientes.camadas(ligadas, com_carga)}

    assert [(c.id, c.estado, c.status) for c in lista.values()] == [
        ("comercial", "ativa", "saudavel"),
        # ligado, mas sem carga da folha
        ("pessoas", "implantacao", "atencao"),
        # tem dado, mas ninguém ligou
        ("receber-pagar", "desligada", "neutro"),
        ("completo", "desligada", "neutro"),
    ]
    assert lista["comercial"].pendencias == []
    assert lista["pessoas"].pendencias == ["Pessoas: sem dado"]
    assert lista["receber-pagar"].pendencias == ["Receber e pagar: desligado"]
    assert lista["completo"].pendencias == ["Resultado: sem dado", "Fluxo de caixa: sem dado"]
    assert (lista["comercial"].grupo, lista["completo"].grupo) == (
        "Universal Básico",
        "Universal Completo",
    )


def test_paineis_disponivel_so_com_carga_da_fonte() -> None:
    lista = {p.codigo: p for p in clientes.paineis(COMERCIAL, frozenset({"f_vendas_pj"}))}

    assert (lista["COM-01"].ligado, lista["COM-01"].pode_ligar, lista["COM-01"].nota) == (
        True,
        True,
        None,
    )
    pessoas = lista["PES-01"]
    assert (pessoas.ligado, pessoas.pode_ligar, pessoas.status_rotulo) == (
        False,
        False,
        "indisponível",
    )
    assert pessoas.nota == "Sem carga de f_folha_* nesta empresa."
    assert lista["FIN-03"].nota == "Sem carga de f_receitas_* ou f_despesas_* nesta empresa."


# ---------- gravações: pessoas ----------


class Resultado:
    def __init__(self, linha: dict[str, Any] | None) -> None:
        self.linha = linha

    def fetchone(self) -> dict[str, Any] | None:
        return self.linha


class ConexaoFalsa:
    """Responde à leitura da pessoa (SQL_ATUAL) e guarda as gravações."""

    def __init__(self, atual: dict[str, Any] | None) -> None:
        self.atual = atual
        self.gravacoes: list[tuple[str, dict[str, Any]]] = []

    def execute(self, consulta: str, params: Any = None) -> Resultado:
        if consulta == usuarios.SQL_ATUAL:
            self.lido = params
            return Resultado(self.atual)
        self.gravacoes.append((consulta, params))
        return Resultado(None)


def _atual(**mudancas: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": BRUNO,
        "nome": "Bruno Beta",
        "email": "bruno@beta.example",
        "perfil": "cliente_usuario",
        "ativo": True,
        "sessao_minutos": None,
        "admins": 2,
        "padrao": 240,
        "pedidos": [{"id": str(CLIENTE_A), "slug": "alfa"}, {"id": str(CLIENTE_B), "slug": "beta"}],
        "clientes": ["alfa"],
    }
    return base | mudancas


def _mudar(conn: ConexaoFalsa, autor: UUID = ADMIN, **mudancas: Any) -> None:
    pedido: dict[str, Any] = {
        "nome": " Bruno B. ",
        "perfil": "cliente_usuario",
        "clientes": ["alfa", "beta", "alfa"],
        "sessao_minutos": 480,
    }
    usuarios.mudar(conn, autor, BRUNO, admin_dominios=DOMINIOS, **(pedido | mudancas))  # type: ignore[arg-type]


def test_mudar_grava_a_pessoa_os_vinculos_e_o_log() -> None:
    conn = ConexaoFalsa(_atual())

    _mudar(conn)

    assert conn.lido == {"id": BRUNO, "perfil": "cliente_usuario", "slugs": ["alfa", "beta"]}
    (mudou, vinculos, log) = conn.gravacoes
    assert mudou == (
        usuarios.SQL_MUDAR,
        {"id": BRUNO, "nome": "Bruno B.", "perfil": "cliente_usuario", "sessao": 480},
    )
    assert vinculos == (
        usuarios.SQL_VINCULOS,
        {"id": BRUNO, "clientes": [str(CLIENTE_A), str(CLIENTE_B)]},
    )
    assert log[0] == usuarios.SQL_LOG
    assert (log[1]["autor"], log[1]["tabela"], log[1]["registro"]) == (
        ADMIN,
        "seg.usuario",
        str(BRUNO),
    )
    assert '"clientes": ["alfa"]' in log[1]["antes"]
    assert '"clientes": ["alfa", "beta"]' in log[1]["depois"]


def test_sessao_igual_ao_padrao_do_perfil_fica_sem_valor_proprio() -> None:
    conn = ConexaoFalsa(_atual())
    _mudar(conn, sessao_minutos=240)
    assert conn.gravacoes[0][1]["sessao"] is None

    conn = ConexaoFalsa(_atual())
    _mudar(conn, sessao_minutos=None)
    assert conn.gravacoes[0][1]["sessao"] is None


def test_virar_admin_tira_os_vinculos_e_exige_email_da_bord() -> None:
    conn = ConexaoFalsa(_atual(email="fulano@bordgestao.com.br", padrao=1440))

    _mudar(conn, perfil="admin", sessao_minutos=1440)

    # os slugs pedidos são ignorados: o admin vê todas, sem vínculo
    assert conn.lido["slugs"] == []
    assert conn.gravacoes[1][1]["clientes"] == []
    assert conn.gravacoes[0][1]["sessao"] is None


@pytest.mark.parametrize(
    ("atual", "mudancas", "mensagem"),
    [
        ({}, {"perfil": "dono"}, "Perfil inválido."),
        ({}, {"perfil": "admin"}, "Admin BORD só com e-mail da BORD."),
        ({}, {"sessao_minutos": 45}, "Tempo de sessão inválido."),
        ({}, {"sessao_minutos": 1440}, "Mais de 12 h, só Admin BORD."),
        ({}, {"clientes": ["alfa", "gama"]}, "Empresa não encontrada: gama."),
        (
            {"perfil": "admin", "admins": 1},
            {"perfil": "consultor"},
            "O Overnex precisa de pelo menos um Admin BORD ativo.",
        ),
    ],
)
def test_mudar_recusa_regra_quebrada(
    atual: dict[str, Any], mudancas: dict[str, Any], mensagem: str
) -> None:
    conn = ConexaoFalsa(_atual(**atual))
    with pytest.raises(RegraInvalida) as exc:
        _mudar(conn, **mudancas)
    assert str(exc.value) == mensagem
    assert conn.gravacoes == []


def test_ninguem_tira_o_proprio_perfil_de_admin() -> None:
    conn = ConexaoFalsa(_atual(perfil="admin"))
    with pytest.raises(RegraInvalida, match="próprio acesso de Admin BORD"):
        _mudar(conn, autor=BRUNO, perfil="consultor")
    assert conn.gravacoes == []
    # o próprio admin troca o nome e a sessão de si mesmo
    conn = ConexaoFalsa(_atual(perfil="admin", padrao=1440))
    _mudar(conn, autor=BRUNO, perfil="admin", sessao_minutos=720)
    assert conn.gravacoes[0][1]["sessao"] == 720


def test_pessoa_que_nao_existe() -> None:
    with pytest.raises(NaoEncontrado, match="Pessoa não encontrada"):
        _mudar(ConexaoFalsa(None))


def test_mudar_sessao() -> None:
    conn = ConexaoFalsa(_atual(sessao_minutos=240))
    usuarios.mudar_sessao(conn, ADMIN, BRUNO, 720)  # type: ignore[arg-type]
    # sem perfil pedido: o padrão é o do perfil atual da pessoa
    assert conn.lido == {"id": BRUNO, "perfil": None, "slugs": []}
    assert conn.gravacoes[0] == (usuarios.SQL_SESSAO, {"id": BRUNO, "sessao": 720})

    conn = ConexaoFalsa(_atual())
    with pytest.raises(RegraInvalida, match="Mais de 12 h, só Admin BORD"):
        usuarios.mudar_sessao(conn, ADMIN, BRUNO, 1440)  # type: ignore[arg-type]
    assert conn.gravacoes == []

    conn = ConexaoFalsa(_atual(perfil="admin", padrao=1440))
    usuarios.mudar_sessao(conn, ADMIN, BRUNO, 1440)  # type: ignore[arg-type]
    assert conn.gravacoes[0][1]["sessao"] is None


def test_desativar_encerra_as_sessoes_e_reativar_nao() -> None:
    conn = ConexaoFalsa(_atual())
    usuarios.mudar_acesso(conn, ADMIN, BRUNO, False)  # type: ignore[arg-type]
    assert [g[0] for g in conn.gravacoes] == [
        usuarios.SQL_ACESSO,
        usuarios.SQL_ENCERRAR,
        usuarios.SQL_LOG,
    ]
    assert conn.gravacoes[0][1] == {"id": BRUNO, "ativo": False}

    conn = ConexaoFalsa(_atual(ativo=False))
    usuarios.mudar_acesso(conn, ADMIN, BRUNO, True)  # type: ignore[arg-type]
    assert [g[0] for g in conn.gravacoes] == [usuarios.SQL_ACESSO, usuarios.SQL_LOG]

    # já está como pedido: nada a gravar
    conn = ConexaoFalsa(_atual())
    usuarios.mudar_acesso(conn, ADMIN, BRUNO, True)  # type: ignore[arg-type]
    assert conn.gravacoes == []


@pytest.mark.parametrize(
    ("atual", "autor", "mensagem"),
    [
        ({"perfil": "admin", "admins": 1}, ADMIN, "pelo menos um Admin BORD ativo"),
        ({"perfil": "admin"}, BRUNO, "próprio acesso de Admin BORD"),
    ],
)
def test_desativar_recusado(atual: dict[str, Any], autor: UUID, mensagem: str) -> None:
    conn = ConexaoFalsa(_atual(**atual))
    with pytest.raises(RegraInvalida, match=mensagem):
        usuarios.mudar_acesso(conn, autor, BRUNO, False)  # type: ignore[arg-type]
    assert conn.gravacoes == []


# ---------- gravações: painel ligado e matriz ----------


class ConexaoDoCliente:
    """Só guarda as gravações; as leituras (uma ida ao banco) são trocadas no teste."""

    def __init__(self) -> None:
        self.gravacoes: list[tuple[str, dict[str, Any]]] = []

    def execute(self, consulta: str, params: Any = None) -> Resultado:
        self.gravacoes.append((consulta, params))
        return Resultado(None)


def _fonte(tabela: str, ok: bool = True, ativo: bool = True) -> dict[str, Any]:
    return {
        "tabela": tabela,
        "tipo": "tabela",
        "ativo": ativo,
        "data_snapshot": AGORA.date() if ok else None,
    }


@pytest.fixture
def lido(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[dict[str, Any]]]:
    linhas: dict[str, list[dict[str, Any]]] = {
        "telas": [
            {"codigo_tela": "VG-01", "ligada": True},
            {"codigo_tela": "COM-01", "ligada": False},
        ],
        "fontes": [_fonte("f_vendas_pj"), _fonte("f_folha_pj", ok=False)],
    }
    monkeypatch.setattr(clientes, "em_uma_ida", lambda conn, consultas: linhas)
    return linhas


def test_ligar_pela_primeira_vez_aplica_a_matriz_padrao(lido: Any) -> None:
    conn = ConexaoDoCliente()

    clientes.ligar_painel(conn, ADMIN, CLIENTE_A, "COM-02", True)  # type: ignore[arg-type]

    (ligou, liberou, log) = conn.gravacoes
    assert ligou == (
        clientes.SQL_LIGAR,
        {"cliente": CLIENTE_A, "codigo": "COM-02", "ligada": True},
    )
    # Equipe comercial mostra nomes de vendedores: a linha nasce com dado nominal
    assert liberou == (
        clientes.SQL_LIBERAR,
        {
            "cliente": CLIENTE_A,
            "codigo": "COM-02",
            "nominal": True,
            "perfis": ["cliente_principal", "cliente_usuario", "consultor"],
        },
    )
    assert (log[1]["tabela"], log[1]["operacao"], log[1]["antes"]) == (
        "cfg.cliente_tela",
        "insert",
        None,
    )


def test_religar_nao_mexe_na_matriz_e_desligar_so_troca_a_chave(lido: Any) -> None:
    conn = ConexaoDoCliente()
    clientes.ligar_painel(conn, ADMIN, CLIENTE_A, "COM-01", True)  # type: ignore[arg-type]
    assert [g[0] for g in conn.gravacoes] == [clientes.SQL_LIGAR, usuarios.SQL_LOG]
    assert conn.gravacoes[1][1]["operacao"] == "update"

    conn = ConexaoDoCliente()
    clientes.ligar_painel(conn, ADMIN, CLIENTE_A, "VG-01", False)  # type: ignore[arg-type]
    assert conn.gravacoes[0][1] == {"cliente": CLIENTE_A, "codigo": "VG-01", "ligada": False}

    # já está como pedido: nada a gravar
    conn = ConexaoDoCliente()
    clientes.ligar_painel(conn, ADMIN, CLIENTE_A, "VG-01", True)  # type: ignore[arg-type]
    assert conn.gravacoes == []


def test_painel_sem_dado_nao_liga_mas_desliga(lido: Any) -> None:
    conn = ConexaoDoCliente()
    with pytest.raises(RegraInvalida, match="Este painel ainda não tem dado nesta empresa"):
        clientes.ligar_painel(conn, ADMIN, CLIENTE_A, "PES-01", True)  # type: ignore[arg-type]
    assert conn.gravacoes == []

    with pytest.raises(NaoEncontrado, match="Painel não encontrado"):
        clientes.ligar_painel(conn, ADMIN, CLIENTE_A, "XX-99", True)  # type: ignore[arg-type]

    # desligar não depende de dado
    lido["telas"].append({"codigo_tela": "PES-01", "ligada": True})
    clientes.ligar_painel(conn, ADMIN, CLIENTE_A, "PES-01", False)  # type: ignore[arg-type]
    assert conn.gravacoes[0][0] == clientes.SQL_LIGAR


def test_matriz_poe_e_tira_o_painel_do_perfil_fixo() -> None:
    conn = ConexaoDoCliente()
    clientes.mudar_matriz(conn, ADMIN, CLIENTE_A, "cliente_usuario", "FIN-03", True)  # type: ignore[arg-type]
    assert conn.gravacoes[0] == (
        clientes.SQL_LIBERAR,
        {
            "cliente": CLIENTE_A,
            "codigo": "FIN-03",
            "perfil": "cliente_usuario",
            "nominal": True,
            "perfis": ["cliente_usuario"],
        },
    )

    conn = ConexaoDoCliente()
    clientes.mudar_matriz(conn, ADMIN, CLIENTE_A, "consultor", "PES-01", False)  # type: ignore[arg-type]
    assert conn.gravacoes[0] == (
        clientes.SQL_TIRAR,
        {"cliente": CLIENTE_A, "codigo": "PES-01", "perfil": "consultor"},
    )
    assert conn.gravacoes[1][1]["operacao"] == "delete"


def test_matriz_nao_muda_o_admin_nem_perfil_ou_painel_desconhecido() -> None:
    conn = ConexaoDoCliente()
    with pytest.raises(RegraInvalida, match="Admin BORD vê tudo e não pode ser alterado"):
        clientes.mudar_matriz(conn, ADMIN, CLIENTE_A, "admin", "VG-01", False)  # type: ignore[arg-type]
    with pytest.raises(NaoEncontrado, match="Perfil não encontrado"):
        clientes.mudar_matriz(conn, ADMIN, CLIENTE_A, "Financeiro", "VG-01", True)  # type: ignore[arg-type]
    with pytest.raises(NaoEncontrado, match="Painel não encontrado"):
        clientes.mudar_matriz(conn, ADMIN, CLIENTE_A, "consultor", "FIN-01", True)  # type: ignore[arg-type]
    assert conn.gravacoes == []

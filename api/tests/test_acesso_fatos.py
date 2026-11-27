"""Checagem de acesso sobre os fatos de seg.contexto_acesso — sem banco (conexão falsa)."""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.core.db import Sessao
from app.core.erros import AcessoNegado
from app.seguranca.acesso import (
    ler_unidades,
    verificar_acesso,
    verificar_acesso_consolidado,
    verificar_acesso_tabela,
    verificar_admin,
    verificar_sessao,
)
from app.seguranca.auth import UsuarioToken

ID_AUTH = UUID("5b000000-0000-4000-8000-00000000000a")
USUARIO = UUID("d1000000-0000-4000-8000-00000000000a")
CLIENTE = UUID("c1000000-0000-4000-8000-00000000000a")
OUTRO_CLIENTE = UUID("c1000000-0000-4000-8000-00000000000b")
EMPRESA_1 = UUID("e1000000-0000-4000-8000-0000000000a1")
EMPRESA_2 = UUID("e1000000-0000-4000-8000-0000000000a2")
EMPRESA_OUTRO = UUID("e1000000-0000-4000-8000-0000000000b1")
SQL_FATOS = "select * from seg.contexto_acesso(%s, %s, %s)"


def _fatos(**mudancas: Any) -> dict[str, Any]:
    """Linha como o psycopg devolve: uuid como UUID, jsonb como dict/list de texto. Padrão:
    Cliente principal com um vínculo (o banco já escolheu o cliente dele)."""
    base: dict[str, Any] = {
        "usuario_id": USUARIO,
        "ativo": True,
        "vinculos": [{"cliente_id": str(CLIENTE), "papel": "gestor"}],
        "perfis_tela": {str(CLIENTE): False},
        "empresas": [
            {"cliente_id": str(CLIENTE), "empresa_id": str(EMPRESA_2)},
            {"cliente_id": str(CLIENTE), "empresa_id": str(EMPRESA_1)},
        ],
        "perfil": "cliente_principal",
        "cliente_id": CLIENTE,
    }
    return base | mudancas


def _admin(**mudancas: Any) -> dict[str, Any]:
    """Admin BORD com um cliente escolhido: sem vínculos, a tela ligada vem com dado
    nominal e as empresas são todas as do cliente."""
    return _fatos(vinculos=[], perfil="admin", perfis_tela={str(CLIENTE): True}) | mudancas


# a Alfa com uma unidade de cada setor (migration 35: o setor vem em cada item)
COM_SETOR = [
    {"cliente_id": str(CLIENTE), "empresa_id": str(EMPRESA_2), "setor": "distribuidora"},
    {"cliente_id": str(CLIENTE), "empresa_id": str(EMPRESA_1), "setor": "industria"},
]

DOIS_VINCULOS = [
    {"cliente_id": str(CLIENTE), "papel": "gestor"},
    {"cliente_id": str(OUTRO_CLIENTE), "papel": "leitor"},
]
SEM_USUARIO: dict[str, Any] = {
    "vinculos": [],
    "perfis_tela": {},
    "empresas": [],
    "perfil": None,
    "cliente_id": None,
}


@dataclass
class _Resultado:
    linha: dict[str, Any] | None

    def fetchone(self) -> dict[str, Any] | None:
        return self.linha


@dataclass
class ConexaoFalsa:
    linha: dict[str, Any]
    # resposta das consultas que não são a dos fatos (ex.: a tabela registrada)
    outras: list[dict[str, Any] | None] = field(default_factory=list)
    consultas: list[tuple[str, Any]] = field(default_factory=list)

    def execute(self, sql: str, params: Any = None) -> _Resultado:
        self.consultas.append((sql, params))
        if "seg.contexto_acesso" in sql:
            return _Resultado(self.linha)
        return _Resultado(self.outras.pop(0))


def _sessao(fatos: dict[str, Any] | None = None, **mudancas: Any) -> tuple[Sessao, ConexaoFalsa]:
    conn = ConexaoFalsa(fatos if fatos is not None else _fatos(**mudancas))
    return Sessao(conn=conn, usuario=UsuarioToken(id_autenticacao=ID_AUTH)), conn  # type: ignore[arg-type]


def _negado(sessao: Sessao, chamada: Any) -> AcessoNegado:
    with pytest.raises(AcessoNegado) as exc:
        chamada()
    (log,) = sessao.logs
    assert log[5] == "negado"
    assert log[6] == exc.value.etapa
    assert log[7] == exc.value.motivo
    return exc.value


# --- uma consulta só, e o log fica pendente -----------------------------------------------


def test_sessao_permitida_com_uma_consulta() -> None:
    sessao, conn = _sessao()

    ctx = verificar_sessao(sessao)

    assert (ctx.usuario_id, ctx.cliente_id, ctx.papel) == (USUARIO, CLIENTE, "gestor")
    assert ctx.perfil == "cliente_principal"
    assert conn.consultas == [(SQL_FATOS, (ID_AUTH, None, None))]
    # o log não vai ao banco aqui: sai com o commit (app.core.db.get_sessao)
    assert sessao.logs == [(USUARIO, ID_AUTH, CLIENTE, None, "sessao", "permitido", None, None)]


def test_tela_manda_o_codigo_e_o_slug_para_a_funcao() -> None:
    sessao, conn = _sessao()

    ctx = verificar_acesso(sessao, "FIN-01", EMPRESA_1, cliente="padrao-lann-alfa")

    assert conn.consultas[0][1] == (ID_AUTH, "FIN-01", "padrao-lann-alfa")
    assert len(conn.consultas) == 1
    assert ctx.empresa_id == EMPRESA_1
    assert sessao.logs[0][2:6] == (CLIENTE, EMPRESA_1, "FIN-01", "permitido")


def test_consolidado_soma_as_empresas_do_cliente_na_ordem_da_funcao() -> None:
    outra = {"cliente_id": str(OUTRO_CLIENTE), "empresa_id": str(EMPRESA_OUTRO)}
    sessao, _ = _sessao(empresas=[*_fatos()["empresas"], outra])

    ctx = verificar_acesso_consolidado(sessao, "VG-01", None)

    assert ctx.empresas == (EMPRESA_2, EMPRESA_1)
    assert ctx.ve_dado_nominal is False


# --- etapas negadas ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fatos",
    [
        # inexistente: a função devolve usuario_id nulo e listas vazias
        {"usuario_id": None, "ativo": False, **SEM_USUARIO},
        {"ativo": False, **SEM_USUARIO},
    ],
)
def test_etapa_1_usuario_inexistente_ou_inativo(fatos: dict[str, Any]) -> None:
    sessao, _ = _sessao(**fatos)
    erro = _negado(sessao, lambda: verificar_sessao(sessao))
    assert (erro.etapa, erro.motivo) == (1, "usuário inexistente ou inativo")
    assert sessao.logs[0][0] == fatos.get("usuario_id", USUARIO)


@pytest.mark.parametrize(
    ("fatos", "slug", "motivo"),
    [
        (_fatos(vinculos=[], cliente_id=None), None, "sem vínculo com cliente ativo"),
        (
            _fatos(vinculos=DOIS_VINCULOS, cliente_id=None),
            None,
            "mais de um vínculo sem cliente escolhido",
        ),
        (
            _fatos(vinculos=DOIS_VINCULOS, cliente_id=None),
            "outro-cliente",
            "cliente pedido fora do alcance do usuário",
        ),
        (_fatos(cliente_id=None), "padrao-lann-beta", "cliente pedido fora do alcance do usuário"),
        (_admin(cliente_id=None), None, "admin sem cliente escolhido"),
        (_admin(cliente_id=None), "nao-existe", "cliente pedido fora do alcance do usuário"),
    ],
)
def test_etapa_2_cliente_escolhido(fatos: dict[str, Any], slug: str | None, motivo: str) -> None:
    sessao, _ = _sessao(fatos)
    erro = _negado(
        sessao, lambda: verificar_acesso_consolidado(sessao, "VG-01", None, cliente=slug)
    )
    assert (erro.etapa, erro.motivo) == (2, motivo)


def test_consultor_escolhe_um_dos_clientes_do_vinculo() -> None:
    sessao, conn = _sessao(
        vinculos=DOIS_VINCULOS,
        cliente_id=OUTRO_CLIENTE,
        perfil="consultor",
        perfis_tela={str(OUTRO_CLIENTE): False},
        empresas=[{"cliente_id": str(OUTRO_CLIENTE), "empresa_id": str(EMPRESA_OUTRO)}],
    )

    ctx = verificar_acesso_consolidado(sessao, "VG-01", None, cliente="padrao-lann-beta")

    # o papel é o do vínculo com o cliente escolhido
    assert (ctx.cliente_id, ctx.papel, ctx.empresas) == (OUTRO_CLIENTE, "leitor", (EMPRESA_OUTRO,))
    assert conn.consultas[0][1] == (ID_AUTH, "VG-01", "padrao-lann-beta")
    assert sessao.logs[0][2] == OUTRO_CLIENTE


def test_etapa_3_papel() -> None:
    sessao, _ = _sessao(vinculos=[{"cliente_id": str(CLIENTE), "papel": "leitor"}])
    erro = _negado(
        sessao, lambda: verificar_acesso(sessao, "FIN-01", EMPRESA_1, papeis=frozenset({"admin"}))
    )
    assert erro.etapa == 3
    assert sessao.logs[0][2] == CLIENTE


@pytest.mark.parametrize("perfis", [{}, {str(OUTRO_CLIENTE): True}])
def test_etapa_4_tela_desligada_ou_fora_do_perfil(perfis: dict[str, bool]) -> None:
    sessao, _ = _sessao(perfis_tela=perfis)
    erro = _negado(sessao, lambda: verificar_acesso_consolidado(sessao, "VG-01", None))
    assert (erro.etapa, erro.motivo) == (
        4,
        "tela desligada no cliente ou fora dos perfis do usuário",
    )


@pytest.mark.parametrize(
    "fatos",
    [
        # Indústria, ou sem setor (a coluna vem vazia, ou não existe sem a migration 34)
        _fatos(setores=["industria"]),
        _fatos(setores=[]),
        _fatos(),
        _admin(setores=["industria"]),
    ],
)
def test_etapa_4_tela_de_outro_setor(fatos: dict[str, Any]) -> None:
    # o Estoque (EST-01) só vale na Distribuidora, mesmo ligado e liberado ao perfil
    sessao, _ = _sessao(fatos)
    erro = _negado(sessao, lambda: verificar_acesso_consolidado(sessao, "EST-01", None))
    assert (erro.etapa, erro.motivo) == (4, "tela fora do setor do cliente")


def test_etapa_4_tela_do_setor_do_cliente() -> None:
    sessao, _ = _sessao(setores=["distribuidora"], empresas=COM_SETOR)
    ctx = verificar_acesso_consolidado(sessao, "EST-01", None)
    assert ctx.codigo_tela == "EST-01"


def test_tela_sem_setor_vale_em_qualquer_setor() -> None:
    sessao, _ = _sessao(setores=["industria"])
    assert verificar_acesso_consolidado(sessao, "VG-01", None).codigo_tela == "VG-01"


def test_sessao_leva_os_setores_do_cliente_escolhido() -> None:
    sessao, _ = _sessao(setores=["distribuidora"])
    assert verificar_sessao(sessao).setores == frozenset({"distribuidora"})
    # sem cliente escolhido, nenhum
    sessao, _ = _sessao(vinculos=DOIS_VINCULOS, cliente_id=None, setores=["distribuidora"])
    assert verificar_sessao(sessao).setores == frozenset()


def test_etapa_5_empresa_de_outro_cliente() -> None:
    outra = {"cliente_id": str(OUTRO_CLIENTE), "empresa_id": str(EMPRESA_OUTRO)}
    sessao, _ = _sessao(empresas=[*_fatos()["empresas"], outra])
    erro = _negado(sessao, lambda: verificar_acesso(sessao, "FIN-01", EMPRESA_OUTRO))
    assert (erro.etapa, erro.motivo) == (5, "empresa fora do vínculo do usuário")


def test_etapa_5_consolidado_sem_empresas() -> None:
    sessao, _ = _sessao(empresas=[])
    erro = _negado(sessao, lambda: verificar_acesso_consolidado(sessao, "VG-01", None))
    assert (erro.etapa, erro.motivo) == (5, "nenhuma empresa no vínculo do usuário")


def test_etapa_6_dado_nominal() -> None:
    sessao, _ = _sessao()
    erro = _negado(
        sessao,
        lambda: verificar_acesso_consolidado(sessao, "VG-01", None, exige_dado_nominal=True),
    )
    assert erro.etapa == 6


def test_etapa_6_passa_com_perfil_nominal() -> None:
    sessao, _ = _sessao(perfis_tela={str(CLIENTE): True})
    ctx = verificar_acesso_consolidado(sessao, "VG-01", (EMPRESA_1,), exige_dado_nominal=True)
    assert ctx.ve_dado_nominal is True
    assert (ctx.unidades, ctx.empresas) == ((EMPRESA_1,), (EMPRESA_1,))


# --- Admin BORD ----------------------------------------------------------------------------


def test_admin_abre_a_tela_ligada_do_cliente_escolhido_com_dado_nominal() -> None:
    sessao, _ = _sessao(_admin())

    ctx = verificar_acesso_consolidado(
        sessao, "COM-02", None, exige_dado_nominal=True, cliente="padrao-lann-alfa"
    )

    assert (ctx.cliente_id, ctx.papel, ctx.ve_dado_nominal) == (CLIENTE, "admin", True)
    assert ctx.empresas == (EMPRESA_2, EMPRESA_1)
    assert sessao.logs[0][2:6] == (CLIENTE, None, "COM-02", "permitido")


def test_admin_nao_abre_tela_desligada_no_cliente() -> None:
    sessao, _ = _sessao(_admin(perfis_tela={}))
    erro = _negado(sessao, lambda: verificar_acesso_consolidado(sessao, "PES-01", None))
    assert (erro.etapa, erro.motivo) == (4, "tela desligada no cliente")


def test_admin_le_tabela_registrada_sem_perfil() -> None:
    sessao, conn = _sessao(_admin())
    conn.outras = [{"contem_dado_nominal": True}]

    ctx = verificar_acesso_tabela(sessao, "f_vendas_pj", None, cliente="padrao-lann-alfa")

    assert (ctx.cliente_id, ctx.papel, ctx.ve_dado_nominal) == (CLIENTE, "admin", True)
    # fatos e o registro da tabela: o perfil de tabela não é consultado
    assert len(conn.consultas) == 2


# --- /api/sessao sem cliente escolhido ----------------------------------------------------


@pytest.mark.parametrize(
    ("fatos", "perfil"),
    [
        (_admin(cliente_id=None, perfis_tela={}, empresas=[]), "admin"),
        (_fatos(vinculos=DOIS_VINCULOS, cliente_id=None, perfil="consultor"), "consultor"),
        (_fatos(vinculos=[], cliente_id=None, perfis_tela={}, empresas=[]), "cliente_principal"),
    ],
)
def test_sessao_passa_sem_cliente_escolhido(fatos: dict[str, Any], perfil: str) -> None:
    sessao, _ = _sessao(fatos)

    ctx = verificar_sessao(sessao)

    assert (ctx.perfil, ctx.cliente_id, ctx.papel) == (perfil, None, None)
    assert sessao.logs == [(USUARIO, ID_AUTH, None, None, "sessao", "permitido", None, None)]


def test_sessao_do_admin_com_cliente_escolhido() -> None:
    sessao, conn = _sessao(_admin())

    ctx = verificar_sessao(sessao, "padrao-lann-alfa")

    assert (ctx.perfil, ctx.cliente_id, ctx.papel) == ("admin", CLIENTE, "admin")
    assert conn.consultas[0][1] == (ID_AUTH, None, "padrao-lann-alfa")


def test_sessao_com_slug_fora_do_alcance_e_negada() -> None:
    sessao, _ = _sessao(cliente_id=None)
    erro = _negado(sessao, lambda: verificar_sessao(sessao, "padrao-lann-beta"))
    assert (erro.etapa, erro.motivo) == (2, "cliente pedido fora do alcance do usuário")


# --- /api/admin ----------------------------------------------------------------------------


def test_gestao_so_para_o_admin() -> None:
    sessao, _ = _sessao(perfil="cliente_principal")
    erro = _negado(sessao, lambda: verificar_admin(sessao, "gestao"))
    assert (erro.etapa, erro.motivo) == (3, "perfil não é admin")
    assert sessao.logs[0][4] == "admin:gestao"


def test_gestao_do_admin_sem_cliente() -> None:
    sessao, conn = _sessao(_admin(cliente_id=None, perfis_tela={}, empresas=[]))

    ctx = verificar_admin(sessao, "usuarios")

    assert (ctx.usuario_id, ctx.cliente_id) == (USUARIO, None)
    assert conn.consultas == [(SQL_FATOS, (ID_AUTH, None, None))]
    assert sessao.logs == [
        (USUARIO, ID_AUTH, None, None, "admin:usuarios", "permitido", None, None)
    ]


def test_gestao_de_um_cliente_escolhe_o_cliente_da_rota() -> None:
    sessao, conn = _sessao(_admin())

    ctx = verificar_admin(sessao, "cliente", "padrao-lann-alfa")

    assert ctx.cliente_id == CLIENTE
    assert conn.consultas[0][1] == (ID_AUTH, None, "padrao-lann-alfa")
    assert sessao.logs[0][2] == CLIENTE


def test_gestao_de_cliente_que_nao_existe_404() -> None:
    sessao, _ = _sessao(_admin(cliente_id=None))
    with pytest.raises(HTTPException) as exc:
        verificar_admin(sessao, "cliente", "nao-existe")
    assert (exc.value.status_code, exc.value.detail) == (404, "Empresa não encontrada.")


def test_gestao_de_cliente_que_nao_existe_e_403_para_quem_nao_e_admin() -> None:
    """Quem não é admin não fica sabendo se o cliente existe."""
    sessao, _ = _sessao(cliente_id=None)
    erro = _negado(sessao, lambda: verificar_admin(sessao, "cliente", "nao-existe"))
    assert erro.etapa == 3


# --- unidades pedidas (?unidades=) e painel de setor ---------------------------------------


def test_unidades_pedidas_somam_so_elas() -> None:
    sessao, _ = _sessao()
    ctx = verificar_acesso_consolidado(sessao, "VG-01", (EMPRESA_1,))
    assert (ctx.unidades, ctx.empresas) == ((EMPRESA_1,), (EMPRESA_1,))
    # a unidade vai na coluna de empresa do log
    assert sessao.logs[0][2:6] == (CLIENTE, EMPRESA_1, "VG-01", "permitido")


def test_todas_as_unidades_pedidas_e_o_mesmo_que_nenhuma() -> None:
    sessao, _ = _sessao()
    ctx = verificar_acesso_consolidado(sessao, "VG-01", (EMPRESA_1, EMPRESA_2, EMPRESA_1))
    # None: as linhas sem unidade também entram; na ordem da função
    assert (ctx.unidades, ctx.empresas) == (None, (EMPRESA_2, EMPRESA_1))
    # mais de uma: a contagem vai no motivo, sem a coluna de empresa
    assert sessao.logs[0][3] is None
    assert sessao.logs[0][7] == "2 unidades pedidas"


def test_unidade_fora_do_vinculo_e_negada_na_etapa_5() -> None:
    sessao, _ = _sessao()
    erro = _negado(sessao, lambda: verificar_acesso_consolidado(sessao, "VG-01", (EMPRESA_OUTRO,)))
    assert (erro.etapa, erro.motivo) == (5, "unidade fora do vínculo do usuário")
    # uma de fora no meio de outras também
    sessao, _ = _sessao()
    with pytest.raises(AcessoNegado) as exc:
        verificar_acesso_consolidado(sessao, "VG-01", (EMPRESA_1, EMPRESA_OUTRO))
    assert exc.value.etapa == 5


def test_painel_de_setor_soma_so_as_unidades_do_setor() -> None:
    sessao, _ = _sessao(setores=["distribuidora", "industria"], empresas=COM_SETOR)
    ctx = verificar_acesso_consolidado(sessao, "EST-01", None)
    assert ctx.escopo == frozenset({"distribuidora"})
    assert (ctx.unidades, ctx.empresas) == ((EMPRESA_2,), (EMPRESA_2,))


def test_painel_de_setor_sem_unidade_pedida_do_setor_fica_vazio_sem_negar() -> None:
    sessao, _ = _sessao(setores=["distribuidora", "industria"], empresas=COM_SETOR)
    ctx = verificar_acesso_consolidado(sessao, "EST-01", (EMPRESA_1,))
    assert (ctx.unidades, ctx.empresas) == ((), ())
    assert sessao.logs[0][5] == "permitido"


def test_painel_de_setor_com_todas_as_unidades_do_setor_e_o_de_todas() -> None:
    so_dist = [{**e, "setor": "distribuidora"} for e in COM_SETOR]
    sessao, _ = _sessao(setores=["distribuidora"], empresas=so_dist)
    ctx = verificar_acesso_consolidado(sessao, "EST-01", None)
    assert (ctx.unidades, ctx.empresas) == (None, (EMPRESA_2, EMPRESA_1))


def test_painel_de_todos_os_setores_nao_tem_escopo() -> None:
    sessao, _ = _sessao(setores=["distribuidora", "industria"], empresas=COM_SETOR)
    ctx = verificar_acesso_consolidado(sessao, "VG-01", None)
    assert ctx.escopo is None
    assert (ctx.unidades, ctx.empresas) == (None, (EMPRESA_2, EMPRESA_1))


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        (None, None),
        (str(EMPRESA_1), (EMPRESA_1,)),
        (f"{EMPRESA_2},{EMPRESA_1},{EMPRESA_2}", (EMPRESA_2, EMPRESA_1)),
        (f" {EMPRESA_1} , {EMPRESA_2}", (EMPRESA_1, EMPRESA_2)),
    ],
)
def test_ler_unidades(texto: str | None, esperado: tuple[UUID, ...] | None) -> None:
    assert ler_unidades(texto) == esperado


@pytest.mark.parametrize(
    "texto",
    ["", ",", "abc", f"{EMPRESA_1},", f"{EMPRESA_1},x", EMPRESA_1.hex, f"{{{EMPRESA_1}}}"],
)
def test_ler_unidades_invalidas_422(texto: str) -> None:
    with pytest.raises(HTTPException) as exc:
        ler_unidades(texto)
    assert (exc.value.status_code, exc.value.detail) == (422, "Filtro inválido.")

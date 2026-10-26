"""Checagem de acesso sobre os fatos de seg.contexto_acesso — sem banco (conexão falsa)."""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import pytest

from app.core.db import Sessao
from app.core.erros import AcessoNegado
from app.seguranca.acesso import (
    verificar_acesso,
    verificar_acesso_consolidado,
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


def _fatos(**mudancas: Any) -> dict[str, Any]:
    """Linha como o psycopg devolve: uuid como UUID, jsonb como dict/list de texto."""
    base: dict[str, Any] = {
        "usuario_id": USUARIO,
        "ativo": True,
        "vinculos": [{"cliente_id": str(CLIENTE), "papel": "gestor"}],
        "perfis_tela": {str(CLIENTE): False},
        "empresas": [
            {"cliente_id": str(CLIENTE), "empresa_id": str(EMPRESA_2)},
            {"cliente_id": str(CLIENTE), "empresa_id": str(EMPRESA_1)},
        ],
    }
    return base | mudancas


@dataclass
class _Resultado:
    linha: dict[str, Any]

    def fetchone(self) -> dict[str, Any]:
        return self.linha


@dataclass
class ConexaoFalsa:
    linha: dict[str, Any]
    consultas: list[tuple[str, Any]] = field(default_factory=list)

    def execute(self, sql: str, params: Any = None) -> _Resultado:
        self.consultas.append((sql, params))
        return _Resultado(self.linha)


def _sessao(**mudancas: Any) -> tuple[Sessao, ConexaoFalsa]:
    conn = ConexaoFalsa(_fatos(**mudancas))
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
    assert conn.consultas == [("select * from seg.contexto_acesso(%s, %s)", (ID_AUTH, None))]
    # o log não vai ao banco aqui: sai com o commit (app.core.db.get_sessao)
    assert sessao.logs == [(USUARIO, ID_AUTH, CLIENTE, None, "sessao", "permitido", None, None)]


def test_tela_manda_o_codigo_para_a_funcao() -> None:
    sessao, conn = _sessao()

    ctx = verificar_acesso(sessao, "FIN-01", EMPRESA_1)

    assert conn.consultas[0][1] == (ID_AUTH, "FIN-01")
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
        {"usuario_id": None, "ativo": False, "vinculos": [], "perfis_tela": {}, "empresas": []},
        {"ativo": False, "vinculos": [], "perfis_tela": {}, "empresas": []},
    ],
)
def test_etapa_1_usuario_inexistente_ou_inativo(fatos: dict[str, Any]) -> None:
    sessao, _ = _sessao(**fatos)
    erro = _negado(sessao, lambda: verificar_sessao(sessao))
    assert (erro.etapa, erro.motivo) == (1, "usuário inexistente ou inativo")
    assert sessao.logs[0][0] == fatos.get("usuario_id", USUARIO)


@pytest.mark.parametrize(
    ("vinculos", "motivo"),
    [
        ([], "sem vínculo com cliente ativo"),
        (
            [
                {"cliente_id": str(CLIENTE), "papel": "gestor"},
                {"cliente_id": str(OUTRO_CLIENTE), "papel": "leitor"},
            ],
            "mais de um vínculo de cliente (ainda não suportado)",
        ),
    ],
)
def test_etapa_2_vinculo(vinculos: list[dict[str, str]], motivo: str) -> None:
    sessao, _ = _sessao(vinculos=vinculos)
    erro = _negado(sessao, lambda: verificar_sessao(sessao))
    assert (erro.etapa, erro.motivo) == (2, motivo)


def test_etapa_3_papel() -> None:
    sessao, _ = _sessao(vinculos=[{"cliente_id": str(CLIENTE), "papel": "leitor"}])
    erro = _negado(
        sessao, lambda: verificar_acesso(sessao, "FIN-01", EMPRESA_1, papeis=frozenset({"admin"}))
    )
    assert erro.etapa == 3
    assert sessao.logs[0][2] == CLIENTE


@pytest.mark.parametrize("perfis", [{}, {str(OUTRO_CLIENTE): True}])
def test_etapa_4_nenhum_perfil_do_cliente_libera_a_tela(perfis: dict[str, bool]) -> None:
    sessao, _ = _sessao(perfis_tela=perfis)
    erro = _negado(sessao, lambda: verificar_acesso_consolidado(sessao, "VG-01", None))
    assert (erro.etapa, erro.motivo) == (4, "nenhum perfil do usuário libera a tela")


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
    ctx = verificar_acesso_consolidado(sessao, "VG-01", EMPRESA_1, exige_dado_nominal=True)
    assert ctx.ve_dado_nominal is True
    assert ctx.empresas == (EMPRESA_1,)

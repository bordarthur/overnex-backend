"""Checagem de acesso a uma tela, em 6 etapas.

1. usuário ativo
2. vínculo com cliente
3. papel
4. perfil libera a tela
5. empresa pedida pertence ao vínculo
6. dado nominal permitido

Cliente e empresas vêm do vínculo do usuário no banco, nunca da requisição: a
empresa da query só vale se estiver nesse vínculo. Toda decisão vai para
seg.log_acesso; a negação vira 403 genérico (sem dizer a etapa).
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query

from app.core.db import Sessao, get_sessao
from app.core.erros import AcessoNegado

TODOS_OS_PAPEIS: frozenset[str] = frozenset({"leitor", "gestor", "admin"})


@dataclass(frozen=True)
class ContextoAcesso:
    sessao: Sessao
    usuario_id: UUID
    cliente_id: UUID
    empresa_id: UUID
    papel: str
    codigo_tela: str
    ve_dado_nominal: bool


def _registrar(
    sessao: Sessao,
    codigo_tela: str,
    *,
    cliente_id: UUID | None,
    empresa_id: UUID | None,
    etapa_negada: int | None = None,
    motivo: str | None = None,
) -> None:
    sessao.conn.execute(
        """
        insert into seg.log_acesso
            (usuario_id, object_id_entra, cliente_id, empresa_id, codigo_tela,
             resultado, etapa_negada, motivo)
        values (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            sessao.usuario_id,
            sessao.usuario.object_id,
            cliente_id,
            empresa_id,
            codigo_tela,
            "negado" if etapa_negada else "permitido",
            etapa_negada,
            motivo,
        ),
    )


def verificar_acesso(
    sessao: Sessao,
    codigo_tela: str,
    empresa_id: UUID,
    *,
    papeis: frozenset[str] = TODOS_OS_PAPEIS,
    exige_dado_nominal: bool = False,
) -> ContextoAcesso:
    conn = sessao.conn
    cliente_id: UUID | None = None

    def negar(etapa: int, motivo: str) -> AcessoNegado:
        _registrar(
            sessao,
            codigo_tela,
            cliente_id=cliente_id,
            empresa_id=empresa_id,
            etapa_negada=etapa,
            motivo=motivo,
        )
        return AcessoNegado(etapa, motivo)

    # 1. usuário ativo
    usuario_id = sessao.usuario_id
    if usuario_id is None or not sessao.usuario_ativo:
        raise negar(1, "usuário inexistente ou inativo")

    # 2. vínculo com cliente (ativo). RLS já restringe ao próprio usuário.
    vinculos = conn.execute(
        """
        select uc.cliente_id, uc.papel
          from seg.usuario_cliente uc
          join cfg.cliente c on c.id = uc.cliente_id and c.ativo
         where uc.usuario_id = %s
        """,
        (usuario_id,),
    ).fetchall()
    if not vinculos:
        raise negar(2, "sem vínculo com cliente ativo")
    if len(vinculos) > 1:
        # TODO: usuário com mais de um cliente (consultor LANN) — escolher o cliente.
        raise negar(2, "mais de um vínculo de cliente (ainda não suportado)")
    cliente_id = vinculos[0]["cliente_id"]
    papel: str = vinculos[0]["papel"]

    # 3. papel
    if papel not in papeis:
        raise negar(3, f"papel '{papel}' não permitido na tela")

    # 4. perfil libera a tela
    perfil = conn.execute(
        """
        select bool_or(pt.ve_dado_nominal) as ve_dado_nominal
          from seg.usuario_perfil up
          join cfg.perfil_tela pt on pt.perfil_id = up.perfil_id
         where up.usuario_id = %s
           and up.cliente_id = %s
           and pt.codigo_tela = %s
        """,
        (usuario_id, cliente_id, codigo_tela),
    ).fetchone()
    if perfil is None or perfil["ve_dado_nominal"] is None:
        raise negar(4, "nenhum perfil do usuário libera a tela")
    ve_dado_nominal: bool = perfil["ve_dado_nominal"]

    # 5. empresa pedida pertence ao vínculo
    pertence = conn.execute(
        """
        select exists (
            select 1
              from seg.usuario_empresa ue
              join cfg.empresa e on e.id = ue.empresa_id and e.cliente_id = ue.cliente_id
             where ue.usuario_id = %s
               and ue.cliente_id = %s
               and ue.empresa_id = %s
        ) as ok
        """,
        (usuario_id, cliente_id, empresa_id),
    ).fetchone()
    if not (pertence and pertence["ok"]):
        raise negar(5, "empresa fora do vínculo do usuário")

    # 6. dado nominal permitido
    if exige_dado_nominal and not ve_dado_nominal:
        raise negar(6, "tela exige dado nominal e o perfil não permite")

    _registrar(sessao, codigo_tela, cliente_id=cliente_id, empresa_id=empresa_id)
    return ContextoAcesso(
        sessao=sessao,
        usuario_id=usuario_id,
        cliente_id=cliente_id,
        empresa_id=empresa_id,
        papel=papel,
        codigo_tela=codigo_tela,
        ve_dado_nominal=ve_dado_nominal,
    )


def exigir_tela(
    codigo_tela: str,
    *,
    papeis: frozenset[str] = TODOS_OS_PAPEIS,
    exige_dado_nominal: bool = False,
) -> Callable[..., ContextoAcesso]:
    """Dependência de tela: lê `empresa` da query e devolve o contexto, ou 403."""

    def dependencia(
        sessao: Annotated[Sessao, Depends(get_sessao)],
        empresa: Annotated[
            UUID, Query(description="Empresa (precisa estar no vínculo do usuário).")
        ],
    ) -> ContextoAcesso:
        return verificar_acesso(
            sessao,
            codigo_tela,
            empresa,
            papeis=papeis,
            exige_dado_nominal=exige_dado_nominal,
        )

    return dependencia

"""Checagem de acesso a uma tela ou a uma tabela de dados, em 6 etapas.

1. usuário ativo
2. vínculo com cliente
3. papel
4. perfil libera a tela (cfg.perfil_tela) ou a tabela (cfg.perfil_tabela, e a tabela
   precisa estar registrada para o cliente em cfg.fonte_carga)
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

from fastapi import Depends, Path, Query

from app.core.db import Sessao, get_sessao
from app.core.erros import AcessoNegado

TODOS_OS_PAPEIS: frozenset[str] = frozenset({"leitor", "gestor", "admin"})
PADRAO_TABELA = r"^[a-z][a-z0-9_]{0,62}$"


@dataclass(frozen=True)
class ContextoAcesso:
    sessao: Sessao
    usuario_id: UUID
    cliente_id: UUID
    empresa_id: UUID
    papel: str
    codigo_tela: str
    ve_dado_nominal: bool


@dataclass(frozen=True)
class ContextoTabela:
    sessao: Sessao
    usuario_id: UUID
    cliente_id: UUID
    # None = todas as empresas que o usuário vê (o RLS filtra)
    empresa_id: UUID | None
    papel: str
    tabela: str
    ve_dado_nominal: bool


@dataclass(frozen=True)
class _Vinculo:
    usuario_id: UUID
    cliente_id: UUID
    papel: str


class _Checagem:
    """Etapas comuns e o registro em seg.log_acesso de um pedido de acesso."""

    def __init__(self, sessao: Sessao, recurso: str, empresa_id: UUID | None) -> None:
        self.sessao = sessao
        self.recurso = recurso
        self.empresa_id = empresa_id
        self.cliente_id: UUID | None = None

    def _registrar(self, etapa_negada: int | None = None, motivo: str | None = None) -> None:
        self.sessao.conn.execute(
            """
            insert into seg.log_acesso
                (usuario_id, object_id_entra, cliente_id, empresa_id, codigo_tela,
                 resultado, etapa_negada, motivo)
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                self.sessao.usuario_id,
                self.sessao.usuario.object_id,
                self.cliente_id,
                self.empresa_id,
                self.recurso,
                "negado" if etapa_negada else "permitido",
                etapa_negada,
                motivo,
            ),
        )

    def negar(self, etapa: int, motivo: str) -> AcessoNegado:
        self._registrar(etapa, motivo)
        return AcessoNegado(etapa, motivo)

    def permitir(self) -> None:
        self._registrar()

    def vinculo(self, papeis: frozenset[str]) -> _Vinculo:
        """Etapas 1 a 3."""
        conn = self.sessao.conn

        # 1. usuário ativo
        usuario_id = self.sessao.usuario_id
        if usuario_id is None or not self.sessao.usuario_ativo:
            raise self.negar(1, "usuário inexistente ou inativo")

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
            raise self.negar(2, "sem vínculo com cliente ativo")
        if len(vinculos) > 1:
            # TODO: usuário com mais de um cliente (consultor LANN) — escolher o cliente.
            raise self.negar(2, "mais de um vínculo de cliente (ainda não suportado)")
        self.cliente_id = vinculos[0]["cliente_id"]
        papel: str = vinculos[0]["papel"]

        # 3. papel
        if papel not in papeis:
            raise self.negar(3, f"papel '{papel}' não permitido em {self.recurso}")

        assert self.cliente_id is not None  # noqa: S101 — definido acima
        return _Vinculo(usuario_id=usuario_id, cliente_id=self.cliente_id, papel=papel)

    def empresa_no_vinculo(self, vinculo: _Vinculo, empresa_id: UUID) -> None:
        """Etapa 5."""
        pertence = self.sessao.conn.execute(
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
            (vinculo.usuario_id, vinculo.cliente_id, empresa_id),
        ).fetchone()
        if not (pertence and pertence["ok"]):
            raise self.negar(5, "empresa fora do vínculo do usuário")


def verificar_acesso(
    sessao: Sessao,
    codigo_tela: str,
    empresa_id: UUID,
    *,
    papeis: frozenset[str] = TODOS_OS_PAPEIS,
    exige_dado_nominal: bool = False,
) -> ContextoAcesso:
    checagem = _Checagem(sessao, codigo_tela, empresa_id)
    vinculo = checagem.vinculo(papeis)

    # 4. perfil libera a tela
    perfil = sessao.conn.execute(
        """
        select bool_or(pt.ve_dado_nominal) as ve_dado_nominal
          from seg.usuario_perfil up
          join cfg.perfil_tela pt on pt.perfil_id = up.perfil_id
         where up.usuario_id = %s
           and up.cliente_id = %s
           and pt.codigo_tela = %s
        """,
        (vinculo.usuario_id, vinculo.cliente_id, codigo_tela),
    ).fetchone()
    if perfil is None or perfil["ve_dado_nominal"] is None:
        raise checagem.negar(4, "nenhum perfil do usuário libera a tela")
    ve_dado_nominal: bool = perfil["ve_dado_nominal"]

    # 5. empresa pedida pertence ao vínculo
    checagem.empresa_no_vinculo(vinculo, empresa_id)

    # 6. dado nominal permitido
    if exige_dado_nominal and not ve_dado_nominal:
        raise checagem.negar(6, "tela exige dado nominal e o perfil não permite")

    checagem.permitir()
    return ContextoAcesso(
        sessao=sessao,
        usuario_id=vinculo.usuario_id,
        cliente_id=vinculo.cliente_id,
        empresa_id=empresa_id,
        papel=vinculo.papel,
        codigo_tela=codigo_tela,
        ve_dado_nominal=ve_dado_nominal,
    )


def verificar_acesso_tabela(
    sessao: Sessao,
    tabela: str,
    empresa_id: UUID | None,
    *,
    papeis: frozenset[str] = TODOS_OS_PAPEIS,
) -> ContextoTabela:
    checagem = _Checagem(sessao, f"tabela:{tabela}", empresa_id)
    vinculo = checagem.vinculo(papeis)

    # 4. tabela registrada para o cliente e liberada a um perfil do usuário
    registro = sessao.conn.execute(
        """
        select contem_dado_nominal
          from cfg.fonte_carga
         where cliente_id = %s and tipo = 'tabela' and tabela_destino = %s
        """,
        (vinculo.cliente_id, tabela),
    ).fetchone()
    if registro is None:
        raise checagem.negar(4, "tabela não registrada para o cliente")
    perfil = sessao.conn.execute(
        """
        select bool_or(pt.ve_dado_nominal) as ve_dado_nominal
          from seg.usuario_perfil up
          join cfg.perfil_tabela pt on pt.perfil_id = up.perfil_id
         where up.usuario_id = %s
           and up.cliente_id = %s
           and pt.tabela = %s
        """,
        (vinculo.usuario_id, vinculo.cliente_id, tabela),
    ).fetchone()
    if perfil is None or perfil["ve_dado_nominal"] is None:
        raise checagem.negar(4, "nenhum perfil do usuário libera a tabela")
    ve_dado_nominal: bool = perfil["ve_dado_nominal"]

    # 5. empresa pedida (opcional) pertence ao vínculo
    if empresa_id is not None:
        checagem.empresa_no_vinculo(vinculo, empresa_id)

    # 6. dado nominal permitido
    if registro["contem_dado_nominal"] and not ve_dado_nominal:
        raise checagem.negar(6, "tabela tem dado nominal e o perfil não permite")

    checagem.permitir()
    return ContextoTabela(
        sessao=sessao,
        usuario_id=vinculo.usuario_id,
        cliente_id=vinculo.cliente_id,
        empresa_id=empresa_id,
        papel=vinculo.papel,
        tabela=tabela,
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


def exigir_tabela(
    sessao: Annotated[Sessao, Depends(get_sessao)],
    tabela: Annotated[str, Path(pattern=PADRAO_TABELA, description="Tabela de dados do mart.")],
    empresa: Annotated[
        UUID | None,
        Query(description="Opcional: só esta empresa (precisa estar no vínculo do usuário)."),
    ] = None,
) -> ContextoTabela:
    """Dependência de tabela de dados: devolve o contexto, ou 403."""
    return verificar_acesso_tabela(sessao, tabela, empresa)

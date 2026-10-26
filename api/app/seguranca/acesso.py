"""Checagem de acesso a uma tela ou a uma tabela de dados, em 6 etapas.

1. usuário ativo
2. vínculo com cliente
3. papel
4. perfil libera a tela (cfg.perfil_tela) ou a tabela (cfg.perfil_tabela, e a tabela
   precisa estar registrada para o cliente em cfg.fonte_carga)
5. empresa pedida pertence ao vínculo (nas telas consolidadas, sem empresa pedida: o
   vínculo precisa ter ao menos uma)
6. dado nominal permitido

`/api/sessao` roda só as etapas 1 a 3.

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
class ContextoConsolidado:
    """Contexto de uma tela que aceita a visão de todas as empresas ("Todas" no front)."""

    sessao: Sessao
    usuario_id: UUID
    cliente_id: UUID
    # None = todas as empresas do vínculo
    empresa_id: UUID | None
    # empresas que a tela soma: a pedida, ou todas as do vínculo
    empresas: tuple[UUID, ...]
    papel: str
    codigo_tela: str
    ve_dado_nominal: bool


@dataclass(frozen=True)
class ContextoSessao:
    """Usuário com vínculo válido (etapas 1 a 3), sem tela nem empresa."""

    sessao: Sessao
    usuario_id: UUID
    cliente_id: UUID
    papel: str


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


@dataclass(frozen=True)
class FatosAcesso:
    """O que seg.contexto_acesso devolve: tudo de que as etapas 1 a 5 precisam."""

    usuario_id: UUID | None
    ativo: bool
    # (cliente_id, papel) dos vínculos com cliente ativo
    vinculos: tuple[tuple[UUID, str], ...]
    # cliente_id → algum perfil que libera a tela vê dado nominal (só se algum libera)
    perfis_tela: dict[UUID, bool]
    # (cliente_id, empresa_id) liberadas, na ordem nome, id da empresa
    empresas: tuple[tuple[UUID, UUID], ...]


def ler_fatos(sessao: Sessao, codigo_tela: str | None) -> FatosAcesso:
    """Uma ida ao banco: define app.usuario_id na transação e lê os fatos da checagem."""
    linha = sessao.conn.execute(
        "select * from seg.contexto_acesso(%s, %s)",
        (sessao.usuario.id_autenticacao, codigo_tela),
    ).fetchone()
    if linha is None:  # a função sempre devolve uma linha
        raise LookupError("seg.contexto_acesso não devolveu linha")
    return FatosAcesso(
        usuario_id=linha["usuario_id"],
        ativo=bool(linha["ativo"]),
        vinculos=tuple((UUID(v["cliente_id"]), v["papel"]) for v in linha["vinculos"]),
        perfis_tela={UUID(c): bool(ve) for c, ve in linha["perfis_tela"].items()},
        empresas=tuple((UUID(e["cliente_id"]), UUID(e["empresa_id"])) for e in linha["empresas"]),
    )


class _Checagem:
    """Etapas comuns e o registro em seg.log_acesso de um pedido de acesso.

    Os fatos vêm de uma consulta só (seg.contexto_acesso); as decisões são feitas aqui. O
    log fica pendente na sessão e é gravado junto com o commit (app/core/db.py).
    """

    def __init__(
        self,
        sessao: Sessao,
        recurso: str,
        empresa_id: UUID | None,
        codigo_tela: str | None = None,
    ) -> None:
        self.sessao = sessao
        self.recurso = recurso
        self.empresa_id = empresa_id
        self.cliente_id: UUID | None = None
        self.fatos = ler_fatos(sessao, codigo_tela)

    def _registrar(self, etapa_negada: int | None = None, motivo: str | None = None) -> None:
        self.sessao.logs.append(
            (
                self.fatos.usuario_id,
                self.sessao.usuario.id_autenticacao,
                self.cliente_id,
                self.empresa_id,
                self.recurso,
                "negado" if etapa_negada else "permitido",
                etapa_negada,
                motivo,
            )
        )

    def negar(self, etapa: int, motivo: str) -> AcessoNegado:
        self._registrar(etapa, motivo)
        return AcessoNegado(etapa, motivo)

    def permitir(self) -> None:
        self._registrar()

    def vinculo(self, papeis: frozenset[str]) -> _Vinculo:
        """Etapas 1 a 3."""
        fatos = self.fatos

        # 1. usuário ativo
        if fatos.usuario_id is None or not fatos.ativo:
            raise self.negar(1, "usuário inexistente ou inativo")

        # 2. vínculo com cliente (ativo). RLS já restringe ao próprio usuário.
        if not fatos.vinculos:
            raise self.negar(2, "sem vínculo com cliente ativo")
        if len(fatos.vinculos) > 1:
            # TODO: usuário com mais de um cliente (consultor LANN) — escolher o cliente.
            raise self.negar(2, "mais de um vínculo de cliente (ainda não suportado)")
        cliente_id, papel = fatos.vinculos[0]
        self.cliente_id = cliente_id

        # 3. papel
        if papel not in papeis:
            raise self.negar(3, f"papel '{papel}' não permitido em {self.recurso}")

        return _Vinculo(usuario_id=fatos.usuario_id, cliente_id=cliente_id, papel=papel)

    def empresa_no_vinculo(self, vinculo: _Vinculo, empresa_id: UUID) -> None:
        """Etapa 5."""
        if (vinculo.cliente_id, empresa_id) not in self.fatos.empresas:
            raise self.negar(5, "empresa fora do vínculo do usuário")

    def empresas_do_vinculo(self, vinculo: _Vinculo) -> tuple[UUID, ...]:
        """Etapa 5 sem empresa pedida: todas as do vínculo; nenhuma = negado."""
        empresas = tuple(e for c, e in self.fatos.empresas if c == vinculo.cliente_id)
        if not empresas:
            raise self.negar(5, "nenhuma empresa no vínculo do usuário")
        return empresas


def _perfil_libera_tela(checagem: _Checagem, vinculo: _Vinculo) -> bool:
    """Etapa 4; devolve se algum perfil que libera a tela vê dado nominal."""
    ve_dado_nominal = checagem.fatos.perfis_tela.get(vinculo.cliente_id)
    if ve_dado_nominal is None:
        raise checagem.negar(4, "nenhum perfil do usuário libera a tela")
    return ve_dado_nominal


def verificar_sessao(sessao: Sessao) -> ContextoSessao:
    """Etapas 1 a 3, para o que não é tela nem tabela (ex.: GET /api/sessao)."""
    checagem = _Checagem(sessao, "sessao", None)
    vinculo = checagem.vinculo(TODOS_OS_PAPEIS)
    checagem.permitir()
    return ContextoSessao(
        sessao=sessao,
        usuario_id=vinculo.usuario_id,
        cliente_id=vinculo.cliente_id,
        papel=vinculo.papel,
    )


def verificar_acesso_consolidado(
    sessao: Sessao,
    codigo_tela: str,
    empresa_id: UUID | None,
    *,
    papeis: frozenset[str] = TODOS_OS_PAPEIS,
    exige_dado_nominal: bool = False,
) -> ContextoConsolidado:
    checagem = _Checagem(sessao, codigo_tela, empresa_id, codigo_tela)
    vinculo = checagem.vinculo(papeis)

    # 4. perfil libera a tela
    ve_dado_nominal = _perfil_libera_tela(checagem, vinculo)

    # 5. empresa pedida pertence ao vínculo; sem empresa, todas as do vínculo
    if empresa_id is not None:
        checagem.empresa_no_vinculo(vinculo, empresa_id)
        empresas: tuple[UUID, ...] = (empresa_id,)
    else:
        empresas = checagem.empresas_do_vinculo(vinculo)

    # 6. dado nominal permitido
    if exige_dado_nominal and not ve_dado_nominal:
        raise checagem.negar(6, "tela exige dado nominal e o perfil não permite")

    checagem.permitir()
    return ContextoConsolidado(
        sessao=sessao,
        usuario_id=vinculo.usuario_id,
        cliente_id=vinculo.cliente_id,
        empresa_id=empresa_id,
        empresas=empresas,
        papel=vinculo.papel,
        codigo_tela=codigo_tela,
        ve_dado_nominal=ve_dado_nominal,
    )


def verificar_acesso(
    sessao: Sessao,
    codigo_tela: str,
    empresa_id: UUID,
    *,
    papeis: frozenset[str] = TODOS_OS_PAPEIS,
    exige_dado_nominal: bool = False,
) -> ContextoAcesso:
    checagem = _Checagem(sessao, codigo_tela, empresa_id, codigo_tela)
    vinculo = checagem.vinculo(papeis)

    # 4. perfil libera a tela
    ve_dado_nominal = _perfil_libera_tela(checagem, vinculo)

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


def exigir_tela_consolidada(
    codigo_tela: str,
    *,
    papeis: frozenset[str] = TODOS_OS_PAPEIS,
    exige_dado_nominal: bool = False,
) -> Callable[..., ContextoConsolidado]:
    """Dependência de tela com `empresa` opcional: sem ela, todas as empresas do vínculo."""

    def dependencia(
        sessao: Annotated[Sessao, Depends(get_sessao)],
        empresa: Annotated[
            UUID | None,
            Query(description="Opcional: só esta empresa. Sem valor: todas as do vínculo."),
        ] = None,
    ) -> ContextoConsolidado:
        return verificar_acesso_consolidado(
            sessao,
            codigo_tela,
            empresa,
            papeis=papeis,
            exige_dado_nominal=exige_dado_nominal,
        )

    return dependencia


def exigir_sessao(sessao: Annotated[Sessao, Depends(get_sessao)]) -> ContextoSessao:
    """Dependência sem tela: usuário ativo com vínculo (etapas 1 a 3), ou 403."""
    return verificar_sessao(sessao)


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

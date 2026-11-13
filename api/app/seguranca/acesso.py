"""Checagem de acesso a uma tela ou a uma tabela de dados, em 6 etapas.

1. usuário ativo
2. cliente escolhido: o do `?cliente=<slug>`, se o usuário pode abri-lo (vínculo; para o
   Admin BORD, qualquer cliente ativo); sem o slug, o único vínculo de quem tem um só
3. papel (do vínculo; o admin tem o papel `admin` em todo cliente)
4. a tela está ligada no cliente (cfg.cliente_tela) e algum perfil do usuário a libera
   (cfg.perfil_tela); para o admin, basta estar ligada. Tabela: precisa estar registrada
   para o cliente em cfg.fonte_carga e liberada a um perfil (cfg.perfil_tabela)
5. empresa pedida pertence ao vínculo (nas telas consolidadas, sem empresa pedida: o
   vínculo precisa ter ao menos uma); o admin vê todas as empresas do cliente
6. dado nominal permitido (o admin vê)

`/api/sessao` roda só as etapas 1 a 3 e aceita quem ainda não tem cliente escolhido (o
admin, quem tem mais de um vínculo e quem não tem nenhum): a moldura do front lista os
clientes para a pessoa escolher. `/api/admin/*` exige o perfil admin.

O cliente e a empresa da requisição são só uma escolha entre o que o banco libera ao
usuário: o slug e a empresa da query só valem se estiverem no alcance dele. Os fatos vêm de
seg.contexto_acesso, que também define app.usuario_id e app.cliente_id para o RLS (só o
cliente escolhido aparece nas consultas). Toda decisão vai para seg.log_acesso; a negação
vira 403 genérico (sem dizer a etapa).

Desempenho: numa conexão do pool, a consulta dos fatos leva o `begin` na mesma ida ao
banco e, com `junto`, outras consultas de leitura que a rota vai usar (a pré-leitura das
telas, app/telas/preleitura.py). Elas rodam depois da função, na mesma transação, já com
o RLS do cliente escolhido; as linhas ficam em `Sessao.adiantadas`. Com o acesso negado,
ninguém as usa.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Path, Query, status

from app.core.db import Consulta, Sessao, em_uma_ida, get_sessao, sem_transacao
from app.core.erros import AcessoNegado

TODOS_OS_PAPEIS: frozenset[str] = frozenset({"leitor", "gestor", "admin"})
PADRAO_TABELA = r"^[a-z][a-z0-9_]{0,62}$"
# o mesmo de cfg.cliente.slug
PADRAO_SLUG = r"^[a-z0-9]+(-[a-z0-9]+)*$"
PERFIL_ADMIN = "admin"
PAPEL_ADMIN = "admin"
MSG_CLIENTE_INEXISTENTE = "Empresa não encontrada."

EmpresaDaQuery = Annotated[
    UUID | None,
    Query(description="Opcional: só esta empresa. Sem valor: todas as do vínculo."),
]
ClienteDaQuery = Annotated[
    str | None,
    Query(
        alias="cliente",
        pattern=PADRAO_SLUG,
        max_length=80,
        description=(
            "Opcional: slug do cliente (a empresa escolhida no front). Só vale entre os "
            "clientes que o usuário pode abrir. Sem valor: o único cliente de quem tem um só."
        ),
    ),
]


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
    """Usuário ativo (etapa 1), com o cliente escolhido se houver (etapas 2 e 3)."""

    sessao: Sessao
    usuario_id: UUID
    # seg.usuario.perfil
    perfil: str
    # None = sem cliente escolhido (admin, mais de um vínculo ou nenhum)
    cliente_id: UUID | None
    papel: str | None


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
class ContextoAdmin:
    """Admin BORD numa rota da Gestão; com o cliente da rota, se ela tem um."""

    sessao: Sessao
    usuario_id: UUID
    cliente_id: UUID | None


@dataclass(frozen=True)
class _Vinculo:
    usuario_id: UUID
    cliente_id: UUID
    papel: str
    admin: bool = False


@dataclass(frozen=True)
class FatosAcesso:
    """O que seg.contexto_acesso devolve: tudo de que as etapas 1 a 5 precisam."""

    usuario_id: UUID | None
    ativo: bool
    # (cliente_id, papel) dos vínculos com cliente ativo (admin: nenhum)
    vinculos: tuple[tuple[UUID, str], ...]
    # cliente escolhido → vê dado nominal, só se a tela está ligada nele e liberada
    perfis_tela: dict[UUID, bool]
    # (cliente_id, empresa_id) liberadas no cliente escolhido, na ordem nome, id da empresa
    empresas: tuple[tuple[UUID, UUID], ...]
    # seg.usuario.perfil; None se o usuário não existe ou está inativo
    perfil: str | None = None
    # cliente escolhido (ver seg.contexto_acesso); None = nenhum
    cliente_id: UUID | None = None


SQL_FATOS = "select * from seg.contexto_acesso(%s, %s, %s)"
# nome da consulta dos fatos na ida ao banco; `junto` não pode usar
FATOS = "fatos de acesso"


def ler_fatos(
    sessao: Sessao,
    codigo_tela: str | None,
    cliente: str | None = None,
    junto: Mapping[str, Consulta] | None = None,
) -> FatosAcesso:
    """Uma ida ao banco: abre a transação, define app.usuario_id e app.cliente_id nela e lê
    os fatos da checagem. `cliente`: o slug pedido na requisição, ou None. `junto`: consultas
    de leitura que vão na mesma ida, depois da função (as linhas ficam em
    `sessao.adiantadas`, por nome)."""
    conn = sessao.conn
    fatos: Consulta = (SQL_FATOS, (sessao.usuario.id_autenticacao, codigo_tela, cliente))
    if junto or sem_transacao(conn):
        linhas = em_uma_ida(conn, {FATOS: fatos, **(junto or {})})
        da_funcao = linhas.pop(FATOS)
        linha = da_funcao[0] if da_funcao else None
        sessao.adiantadas.update(linhas)
    else:
        # conexão que abre a transação sozinha (fora do pool), sem nada para ir junto
        linha = conn.execute(*fatos).fetchone()
    if linha is None:  # a função sempre devolve uma linha
        raise LookupError("seg.contexto_acesso não devolveu linha")
    return FatosAcesso(
        usuario_id=linha["usuario_id"],
        ativo=bool(linha["ativo"]),
        vinculos=tuple((UUID(v["cliente_id"]), v["papel"]) for v in linha["vinculos"]),
        perfis_tela={UUID(c): bool(ve) for c, ve in linha["perfis_tela"].items()},
        empresas=tuple((UUID(e["cliente_id"]), UUID(e["empresa_id"])) for e in linha["empresas"]),
        perfil=linha["perfil"],
        cliente_id=linha["cliente_id"],
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
        detalhe: str | None = None,
        cliente: str | None = None,
        junto: Mapping[str, Consulta] | None = None,
    ) -> None:
        self.sessao = sessao
        self.recurso = recurso
        self.empresa_id = empresa_id
        # vai no motivo do log, permitido ou negado (ex.: os filtros de um pedido de nomes)
        self.detalhe = detalhe
        # slug pedido na requisição
        self.cliente = cliente
        self.cliente_id: UUID | None = None
        self.fatos = ler_fatos(sessao, codigo_tela, cliente, junto)

    def _registrar(self, etapa_negada: int | None = None, motivo: str | None = None) -> None:
        if self.detalhe is not None:
            motivo = f"{motivo} ({self.detalhe})" if motivo else self.detalhe
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

    def usuario_ativo(self) -> UUID:
        """Etapa 1."""
        fatos = self.fatos
        if fatos.usuario_id is None or not fatos.ativo:
            raise self.negar(1, "usuário inexistente ou inativo")
        return fatos.usuario_id

    def eh_admin(self) -> bool:
        return self.fatos.perfil == PERFIL_ADMIN

    def cliente_escolhido(self) -> tuple[UUID, str] | None:
        """Etapa 2: (cliente, papel) do cliente escolhido; None se não há escolha a fazer
        (sem slug, e o usuário é admin ou não tem exatamente um vínculo). Slug fora do
        alcance do usuário: negado."""
        fatos = self.fatos
        if fatos.cliente_id is None:
            if self.cliente is not None:
                raise self.negar(2, "cliente pedido fora do alcance do usuário")
            return None
        if self.eh_admin():
            return fatos.cliente_id, PAPEL_ADMIN
        papel = dict(fatos.vinculos).get(fatos.cliente_id)
        if papel is None:  # o banco só escolhe cliente do vínculo
            raise self.negar(2, "cliente escolhido sem vínculo")
        return fatos.cliente_id, papel

    def vinculo(self, papeis: frozenset[str]) -> _Vinculo:
        """Etapas 1 a 3."""
        usuario_id = self.usuario_ativo()

        # 2. cliente escolhido (o RLS já restringe as consultas a ele)
        escolhido = self.cliente_escolhido()
        if escolhido is None:
            if self.eh_admin():
                raise self.negar(2, "admin sem cliente escolhido")
            if not self.fatos.vinculos:
                raise self.negar(2, "sem vínculo com cliente ativo")
            raise self.negar(2, "mais de um vínculo sem cliente escolhido")
        cliente_id, papel = escolhido
        self.cliente_id = cliente_id

        # 3. papel
        if papel not in papeis:
            raise self.negar(3, f"papel '{papel}' não permitido em {self.recurso}")

        return _Vinculo(
            usuario_id=usuario_id, cliente_id=cliente_id, papel=papel, admin=self.eh_admin()
        )

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
    """Etapa 4; devolve se algum perfil que libera a tela vê dado nominal (o admin vê)."""
    ve_dado_nominal = checagem.fatos.perfis_tela.get(vinculo.cliente_id)
    if ve_dado_nominal is None:
        if vinculo.admin:
            raise checagem.negar(4, "tela desligada no cliente")
        raise checagem.negar(4, "tela desligada no cliente ou fora dos perfis do usuário")
    return ve_dado_nominal


def verificar_sessao(sessao: Sessao, cliente: str | None = None) -> ContextoSessao:
    """Etapas 1 a 3, para o que não é tela nem tabela (GET /api/sessao). Sem cliente
    escolhido, passa só com a etapa 1: a moldura lista os clientes da pessoa."""
    checagem = _Checagem(sessao, "sessao", None, cliente=cliente)
    usuario_id = checagem.usuario_ativo()
    escolhido = checagem.cliente_escolhido()
    cliente_id, papel = escolhido if escolhido else (None, None)
    checagem.cliente_id = cliente_id
    checagem.permitir()
    return ContextoSessao(
        sessao=sessao,
        usuario_id=usuario_id,
        perfil=checagem.fatos.perfil or "",
        cliente_id=cliente_id,
        papel=papel,
    )


def verificar_admin(sessao: Sessao, recurso: str, cliente: str | None = None) -> ContextoAdmin:
    """Rota da Gestão: usuário ativo com o perfil admin, senão 403 (negado na etapa 3).
    `cliente`: o slug da rota, quando ela é de um cliente; se não existe, 404 (só o admin
    chega a saber)."""
    checagem = _Checagem(sessao, f"admin:{recurso}", None, cliente=cliente)
    usuario_id = checagem.usuario_ativo()
    if not checagem.eh_admin():
        raise checagem.negar(3, "perfil não é admin")
    checagem.cliente_id = checagem.fatos.cliente_id
    if cliente is not None and checagem.fatos.cliente_id is None:
        # sem log: o erro desfaz a transação da requisição (app/core/db.py)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=MSG_CLIENTE_INEXISTENTE)
    checagem.permitir()
    return ContextoAdmin(sessao=sessao, usuario_id=usuario_id, cliente_id=checagem.cliente_id)


def verificar_acesso_consolidado(
    sessao: Sessao,
    codigo_tela: str,
    empresa_id: UUID | None,
    *,
    papeis: frozenset[str] = TODOS_OS_PAPEIS,
    exige_dado_nominal: bool = False,
    recurso: str | None = None,
    detalhe: str | None = None,
    cliente: str | None = None,
    junto: Mapping[str, Consulta] | None = None,
) -> ContextoConsolidado:
    """`recurso`: o que vai no log no lugar do código da tela (ex.: "PES-01:<cartão>:nomes");
    `detalhe`: texto a mais no motivo do log; `cliente`: o slug pedido na requisição;
    `junto`: consultas de leitura na mesma ida ao banco da checagem (ver ler_fatos)."""
    checagem = _Checagem(
        sessao, recurso or codigo_tela, empresa_id, codigo_tela, detalhe, cliente, junto
    )
    vinculo = checagem.vinculo(papeis)

    # 4. tela ligada no cliente e liberada a um perfil
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
    cliente: str | None = None,
) -> ContextoAcesso:
    checagem = _Checagem(sessao, codigo_tela, empresa_id, codigo_tela, cliente=cliente)
    vinculo = checagem.vinculo(papeis)

    # 4. tela ligada no cliente e liberada a um perfil
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
    cliente: str | None = None,
) -> ContextoTabela:
    checagem = _Checagem(sessao, f"tabela:{tabela}", empresa_id, cliente=cliente)
    vinculo = checagem.vinculo(papeis)

    # 4. tabela registrada para o cliente e liberada a um perfil do usuário (o admin lê
    #    toda tabela registrada)
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
    if vinculo.admin:
        ve_dado_nominal = True
    else:
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
        ve_dado_nominal = bool(perfil["ve_dado_nominal"])

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
    """Dependência de tela: lê `empresa` e `cliente` da query e devolve o contexto, ou 403."""

    def dependencia(
        sessao: Annotated[Sessao, Depends(get_sessao)],
        empresa: Annotated[
            UUID, Query(description="Empresa (precisa estar no vínculo do usuário).")
        ],
        cliente: ClienteDaQuery = None,
    ) -> ContextoAcesso:
        return verificar_acesso(
            sessao,
            codigo_tela,
            empresa,
            papeis=papeis,
            exige_dado_nominal=exige_dado_nominal,
            cliente=cliente,
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
        empresa: EmpresaDaQuery = None,
        cliente: ClienteDaQuery = None,
    ) -> ContextoConsolidado:
        return verificar_acesso_consolidado(
            sessao,
            codigo_tela,
            empresa,
            papeis=papeis,
            exige_dado_nominal=exige_dado_nominal,
            cliente=cliente,
        )

    return dependencia


def exigir_sessao(
    sessao: Annotated[Sessao, Depends(get_sessao)],
    cliente: ClienteDaQuery = None,
) -> ContextoSessao:
    """Dependência sem tela: usuário ativo (e o cliente escolhido, se houver), ou 403."""
    return verificar_sessao(sessao, cliente)


def exigir_tabela(
    sessao: Annotated[Sessao, Depends(get_sessao)],
    tabela: Annotated[str, Path(pattern=PADRAO_TABELA, description="Tabela de dados do mart.")],
    empresa: Annotated[
        UUID | None,
        Query(description="Opcional: só esta empresa (precisa estar no vínculo do usuário)."),
    ] = None,
    cliente: ClienteDaQuery = None,
) -> ContextoTabela:
    """Dependência de tabela de dados: devolve o contexto, ou 403."""
    return verificar_acesso_tabela(sessao, tabela, empresa, cliente=cliente)


def exigir_admin(recurso: str) -> Callable[..., ContextoAdmin]:
    """Dependência de rota da Gestão sem cliente: só o Admin BORD, ou 403."""

    def dependencia(sessao: Annotated[Sessao, Depends(get_sessao)]) -> ContextoAdmin:
        return verificar_admin(sessao, recurso)

    return dependencia


def exigir_admin_no_cliente(recurso: str) -> Callable[..., ContextoAdmin]:
    """Dependência de rota da Gestão de um cliente (`{slug}` no caminho): só o Admin BORD
    (403), com o cliente escolhido para o RLS; slug que não existe, 404."""

    def dependencia(
        sessao: Annotated[Sessao, Depends(get_sessao)],
        slug: Annotated[
            str, Path(pattern=PADRAO_SLUG, max_length=80, description="Slug do cliente.")
        ],
    ) -> ContextoAdmin:
        return verificar_admin(sessao, recurso, slug)

    return dependencia

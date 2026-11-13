"""Pessoas na Gestão: a lista, a situação de cada uma e as mudanças de perfil, empresas,
sessão e acesso.

Quem cria o login é o CLI (app/cli/convidar_usuario.py): o convite precisa da chave secreta
do Supabase, que não entra no ambiente da API. Aqui a BORD edita quem já existe.

Regras (mockup Gestão M2.1.0), todas conferidas aqui, com o texto pronto no erro:
- Admin BORD só com e-mail de um domínio de ADMIN_DOMINIOS, e sem vínculos (vê todas);
- o Overnex não fica sem Admin BORD ativo: o último não perde o perfil nem o acesso, e
  ninguém tira o próprio perfil de admin nem desativa a si mesmo;
- sessão: 30 min a 24 h; mais de 12 h, só Admin BORD; igual ao padrão do perfil fica como
  "padrão" (sessao_minutos null), e passa a acompanhar o padrão;
- o e-mail é o login e não muda.

As gravações passam pelo RLS do admin (migration 27) e vão para seg.log_alteracao.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from app.admin import textos
from app.admin.contrato import (
    Acessos,
    AoEntrar,
    FiltroUsuarios,
    ItemCliente,
    Rotulado,
    Situacao,
    Status,
    Usuario,
)
from app.core.config import email_de_admin
from app.core.db import Conexao
from app.seguranca import perfis

SESSOES: tuple[int, ...] = (30, 60, 240, 480, 720, 1440)
# acima disto, só Admin BORD
SESSAO_MAXIMA_COMUM = 720

MSG_NAO_ENCONTRADA = "Pessoa não encontrada."
MSG_PERFIL = "Perfil inválido."
MSG_SESSAO = "Tempo de sessão inválido."
MSG_SESSAO_LONGA = "Mais de 12 h, só Admin BORD."
MSG_ADMIN_EMAIL = "Admin BORD só com e-mail da BORD."
MSG_ULTIMO_ADMIN = "O Overnex precisa de pelo menos um Admin BORD ativo."
MSG_PROPRIO = "Você não pode tirar o próprio acesso de Admin BORD. Peça a outro Admin BORD."
MSG_CLIENTE = "Empresa não encontrada: {}."
NOTA_CONVITE = (
    "Novos acessos são criados pela equipe BORD, pelo comando de cadastro: a pessoa recebe o "
    "convite por e-mail e cria a própria senha. Aqui se edita quem já tem acesso."
)


class RegraInvalida(Exception):
    """Pedido que quebra uma regra da Gestão; a mensagem vai pronta para a tela (422)."""


class NaoEncontrado(Exception):
    """Pessoa, painel ou perfil que não existe (404)."""


# o RLS do admin mostra todas as pessoas; cfg.cliente só mostra os clientes ativos
SQL_USUARIOS = """
    select u.id, u.nome, u.email, u.perfil, u.ativo,
           coalesce(u.sessao_minutos, seg.sessao_padrao(u.perfil)) as sessao_minutos,
           u.convidado_em, u.primeiro_acesso_em, u.desativado_em,
           coalesce((
               select jsonb_agg(jsonb_build_object('slug', c.slug, 'nome', c.nome)
                                order by c.nome, c.slug)
                 from seg.usuario_cliente uc
                 join cfg.cliente c on c.id = uc.cliente_id
                where uc.usuario_id = u.id
           ), '[]'::jsonb) as clientes,
           coalesce(a.entradas_30d, 0) as entradas_30d,
           a.ultimo_acesso
      from seg.usuario u
      left join seg.resumo_acessos() a on a.usuario_id = u.id
     order by lower(coalesce(u.nome, u.email)), u.id
"""
SQL_ADMINS_ATIVOS = "select count(*) as total from seg.usuario where perfil = 'admin' and ativo"
SQL_TOTAL_CLIENTES = "select count(*) as total from cfg.cliente where ativo"


@dataclass(frozen=True)
class Pessoa:
    """Uma linha de SQL_USUARIOS."""

    id: UUID
    nome: str | None
    email: str
    perfil: str
    ativo: bool
    sessao_minutos: int
    convidado_em: datetime | None
    primeiro_acesso_em: datetime | None
    desativado_em: datetime | None
    clientes: tuple[ItemCliente, ...]
    entradas_30d: int
    ultimo_acesso: datetime | None

    @property
    def admin(self) -> bool:
        return self.perfil == perfis.ADMIN


def pessoa_da_linha(linha: Mapping[str, Any]) -> Pessoa:
    return Pessoa(
        id=linha["id"],
        nome=linha["nome"],
        email=linha["email"],
        perfil=linha["perfil"],
        ativo=bool(linha["ativo"]),
        sessao_minutos=linha["sessao_minutos"],
        convidado_em=linha["convidado_em"],
        primeiro_acesso_em=linha["primeiro_acesso_em"],
        desativado_em=linha["desativado_em"],
        clientes=tuple(ItemCliente(slug=c["slug"], nome=c["nome"]) for c in linha["clientes"]),
        entradas_30d=linha["entradas_30d"],
        ultimo_acesso=linha["ultimo_acesso"],
    )


def situacao(p: Pessoa, agora: datetime, convite_horas: int) -> Situacao:
    """Desativado, convite enviado ou vencido (ainda não entrou), sem empresa ou ativo."""

    def feita(id_: str, rotulo: str, quando: str, status: Status) -> Situacao:
        return Situacao(id=id_, rotulo=rotulo, quando=quando, status=status)

    if not p.ativo:
        quando = f"desativado {textos.quando(p.desativado_em, agora)}" if p.desativado_em else ""
        return feita("inativo", "Desativado", quando, "neutro")
    if p.primeiro_acesso_em is None and p.convidado_em is not None:
        vence = p.convidado_em + timedelta(hours=convite_horas)
        if agora < vence:
            enviado = f"enviado {textos.quando(p.convidado_em, agora)}"
            return feita("convite", "Convite enviado", enviado, "atencao")
        return feita(
            "vencido", "Convite vencido", f"venceu {textos.quando(vence, agora)}", "critico"
        )
    entrou = (
        f"último acesso {textos.quando(p.ultimo_acesso, agora)}"
        if p.ultimo_acesso
        else "ainda não entrou"
    )
    if not p.admin and not p.clientes:
        return feita("sem-empresa", "Sem empresa", entrou, "critico")
    return feita("ativo", "Ativo", entrou, "saudavel")


def ao_entrar(p: Pessoa, total_clientes: int) -> AoEntrar:
    """O que a pessoa vê logo depois do login."""
    if not p.ativo:
        return AoEntrar(tipo="nada", rotulo="Não entra: acesso desativado")
    if p.admin:
        return AoEntrar(
            tipo="lista", rotulo=f"Abre a Gestão e escolhe entre as {total_clientes} empresas"
        )
    if not p.clientes:
        return AoEntrar(tipo="nada", rotulo="Não vê nada")
    if len(p.clientes) == 1:
        return AoEntrar(tipo="direto", rotulo=f"Abre direto em {p.clientes[0].nome}")
    return AoEntrar(tipo="lista", rotulo=f"Escolhe entre {len(p.clientes)} empresas")


def usuario(
    p: Pessoa, agora: datetime, convite_horas: int, total_clientes: int, admins_ativos: int
) -> Usuario:
    return Usuario(
        id=p.id,
        nome=p.nome or p.email.split("@")[0],
        email=p.email,
        perfil=Rotulado(id=p.perfil, rotulo=perfis.rotulo(p.perfil)),
        todos_os_clientes=p.admin,
        clientes=[] if p.admin else list(p.clientes),
        ao_entrar=ao_entrar(p, total_clientes),
        sessao_minutos=p.sessao_minutos,
        situacao=situacao(p, agora, convite_horas),
        acessos=Acessos(
            entradas30d=p.entradas_30d,
            ultimo=textos.quando(p.ultimo_acesso, agora) if p.ultimo_acesso else None,
        ),
        pode_desativar=p.ativo and not (p.admin and admins_ativos < 2),
    )


def filtros(usuarios: Sequence[Usuario]) -> list[FiltroUsuarios]:
    """Contagem dos filtros da lista: todos, um por perfil e os que pedem atenção."""

    def total(condicao: Any) -> int:
        return sum(1 for u in usuarios if condicao(u))

    return [
        FiltroUsuarios(id="todos", rotulo="Todos", total=len(usuarios), atencao=False),
        *(
            FiltroUsuarios(
                id=p.id,
                rotulo=p.rotulo,
                total=total(lambda u, p=p: u.perfil.id == p.id),
                atencao=False,
            )
            for p in perfis.PERFIS
        ),
        FiltroUsuarios(
            id="pendentes",
            rotulo="Convites pendentes",
            total=total(lambda u: u.situacao.id in ("convite", "vencido")),
            atencao=True,
        ),
        FiltroUsuarios(
            id="sem-empresa",
            rotulo="Sem empresa",
            total=total(lambda u: u.situacao.id == "sem-empresa"),
            atencao=True,
        ),
    ]


# ---------- gravações ----------

SQL_ATUAL = """
    select u.id, u.nome, u.email, u.perfil, u.ativo, u.sessao_minutos,
           (select count(*) from seg.usuario a where a.perfil = 'admin' and a.ativo) as admins,
           -- padrão do perfil pedido; sem perfil pedido, o do perfil atual
           seg.sessao_padrao(coalesce(%(perfil)s::text, u.perfil)) as padrao,
           coalesce((
               select jsonb_agg(jsonb_build_object('id', c.id, 'slug', c.slug))
                 from cfg.cliente c
                where c.slug = any(%(slugs)s::text[]) and c.ativo
           ), '[]'::jsonb) as pedidos,
           coalesce((
               select jsonb_agg(c.slug order by c.slug)
                 from seg.usuario_cliente uc
                 join cfg.cliente c on c.id = uc.cliente_id
                where uc.usuario_id = u.id
           ), '[]'::jsonb) as clientes
      from seg.usuario u
     where u.id = %(id)s
"""
SQL_MUDAR = """
    update seg.usuario
       set nome = %(nome)s, perfil = %(perfil)s, sessao_minutos = %(sessao)s
     where id = %(id)s
"""
SQL_VINCULOS = "select seg.definir_vinculos(%(id)s, %(clientes)s::uuid[])"
SQL_SESSAO = "update seg.usuario set sessao_minutos = %(sessao)s where id = %(id)s"
SQL_ACESSO = """
    update seg.usuario
       set ativo = %(ativo)s,
           desativado_em = case when %(ativo)s then null else now() end
     where id = %(id)s
"""
SQL_ENCERRAR = "select seg.encerrar_sessoes(%(id)s)"
SQL_LOG = """
    insert into seg.log_alteracao (usuario_id, tabela, registro_id, operacao, antes, depois)
    values (%(autor)s, %(tabela)s, %(registro)s, %(operacao)s, %(antes)s::jsonb, %(depois)s::jsonb)
"""


def registrar(
    conn: Conexao,
    autor: UUID,
    tabela: str,
    registro: str,
    operacao: str,
    antes: Mapping[str, Any] | None,
    depois: Mapping[str, Any] | None,
) -> None:
    """Linha de seg.log_alteracao da mudança (quem, o quê, antes e depois)."""
    conn.execute(
        SQL_LOG,
        {
            "autor": autor,
            "tabela": tabela,
            "registro": registro,
            "operacao": operacao,
            "antes": None if antes is None else json.dumps(antes, default=str),
            "depois": None if depois is None else json.dumps(depois, default=str),
        },
    )


def _atual(
    conn: Conexao, usuario_id: UUID, perfil: str | None = None, slugs: Sequence[str] = ()
) -> dict[str, Any]:
    """A pessoa como está, com o que as regras precisam; `perfil`: o perfil pedido."""
    linha = conn.execute(
        SQL_ATUAL, {"id": usuario_id, "perfil": perfil, "slugs": list(slugs)}
    ).fetchone()
    if linha is None:
        raise NaoEncontrado(MSG_NAO_ENCONTRADA)
    return dict(linha)


def _conferir_sessao(minutos: int, perfil: str) -> None:
    if minutos not in SESSOES:
        raise RegraInvalida(MSG_SESSAO)
    if minutos > SESSAO_MAXIMA_COMUM and perfil != perfis.ADMIN:
        raise RegraInvalida(MSG_SESSAO_LONGA)


def mudar(
    conn: Conexao,
    autor: UUID,
    usuario_id: UUID,
    *,
    nome: str,
    perfil: str,
    clientes: Sequence[str],
    sessao_minutos: int | None,
    admin_dominios: str,
) -> None:
    """Troca nome, perfil, empresas e sessão da pessoa. Não confirma a transação."""
    if perfil not in perfis.POR_ID:
        raise RegraInvalida(MSG_PERFIL)
    slugs = [] if perfil == perfis.ADMIN else list(dict.fromkeys(clientes))
    atual = _atual(conn, usuario_id, perfil, slugs)
    era_admin = atual["perfil"] == perfis.ADMIN
    vira_admin = perfil == perfis.ADMIN

    if usuario_id == autor and not vira_admin:
        raise RegraInvalida(MSG_PROPRIO)
    if vira_admin and not era_admin and not email_de_admin(atual["email"], admin_dominios):
        raise RegraInvalida(MSG_ADMIN_EMAIL)
    if era_admin and not vira_admin and atual["ativo"] and atual["admins"] < 2:
        raise RegraInvalida(MSG_ULTIMO_ADMIN)
    pedidos = {c["slug"]: c["id"] for c in atual["pedidos"]}
    faltam = [s for s in slugs if s not in pedidos]
    if faltam:
        raise RegraInvalida(MSG_CLIENTE.format(", ".join(faltam)))
    if sessao_minutos is not None:
        _conferir_sessao(sessao_minutos, perfil)
    # igual ao padrão do perfil = sem valor próprio
    sessao = None if sessao_minutos == atual["padrao"] else sessao_minutos

    conn.execute(
        SQL_MUDAR, {"id": usuario_id, "nome": nome.strip(), "perfil": perfil, "sessao": sessao}
    )
    conn.execute(SQL_VINCULOS, {"id": usuario_id, "clientes": [pedidos[s] for s in slugs]})
    registrar(
        conn,
        autor,
        "seg.usuario",
        str(usuario_id),
        "update",
        {
            "nome": atual["nome"],
            "perfil": atual["perfil"],
            "sessao_minutos": atual["sessao_minutos"],
            "clientes": atual["clientes"],
        },
        {
            "nome": nome.strip(),
            "perfil": perfil,
            "sessao_minutos": sessao,
            "clientes": sorted(slugs),
        },
    )


def mudar_sessao(conn: Conexao, autor: UUID, usuario_id: UUID, minutos: int) -> None:
    """Troca só o tempo de sessão da pessoa. Não confirma a transação."""
    atual = _atual(conn, usuario_id)
    _conferir_sessao(minutos, atual["perfil"])
    # igual ao padrão do perfil = sem valor próprio
    sessao = None if minutos == atual["padrao"] else minutos
    conn.execute(SQL_SESSAO, {"id": usuario_id, "sessao": sessao})
    registrar(
        conn,
        autor,
        "seg.usuario",
        str(usuario_id),
        "update",
        {"sessao_minutos": atual["sessao_minutos"]},
        {"sessao_minutos": sessao},
    )


def mudar_acesso(conn: Conexao, autor: UUID, usuario_id: UUID, ativo: bool) -> None:
    """Desativa ou reativa a pessoa; ao desativar, encerra as sessões abertas dela. Não
    confirma a transação."""
    atual = _atual(conn, usuario_id)
    if atual["ativo"] == ativo:
        return
    if not ativo and usuario_id == autor:
        raise RegraInvalida(MSG_PROPRIO)
    if not ativo and atual["perfil"] == perfis.ADMIN and atual["admins"] < 2:
        raise RegraInvalida(MSG_ULTIMO_ADMIN)
    conn.execute(SQL_ACESSO, {"id": usuario_id, "ativo": ativo})
    if not ativo:
        conn.execute(SQL_ENCERRAR, {"id": usuario_id})
    registrar(
        conn,
        autor,
        "seg.usuario",
        str(usuario_id),
        "update",
        {"ativo": atual["ativo"]},
        {"ativo": ativo},
    )

"""GET /api/sessao: o que a moldura do front precisa antes de abrir uma tela.

Usuário (nome e e-mail do cadastro em seg.usuario, não do token), perfil, os clientes que a
pessoa pode abrir (o seletor de empresa do front) e, do cliente escolhido: empresas (filtro
"Empresa"), telas liberadas (menu) e períodos com dados (filtro "Período": os meses com
faturado no resumo mensal e o mês do último snapshot, do mais recente para o mais antigo).

Cliente escolhido: o de `?cliente=<slug>`, que só vale entre os clientes da pessoa (os
vínculos; para o Admin BORD, todos os ativos); slug de fora responde 403. Sem o slug, quem
tem um cliente só já recebe esse; o admin, quem tem mais de um e quem não tem nenhum recebem
`cliente: null` e as listas vazias, e o front mostra a Gestão, a escolha de empresa ou o
`aviso`. O `slug` volta aqui para o front montar a URL certa.

Telas liberadas = ligadas no cliente (cfg.cliente_tela) e em algum perfil da pessoa; para o
admin, as ligadas.

Desempenho: as consultas vão todas numa ida ao banco (pipeline), e a sessão agenda a
pré-montagem, em segundo plano, das telas liberadas (app/telas/preparo.py): quando o front
abre a primeira tela, ela já está pronta ou sendo montada.
"""

from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.cache import existe
from app.core.config import Settings, get_settings
from app.core.db import Pool, em_uma_ida, get_pool
from app.seguranca import perfis
from app.seguranca.acesso import ContextoSessao, exigir_sessao
from app.telas import preparo
from app.telas.moldura import (
    SQL_MESES,
    SQL_ULTIMA_CARGA,
    Cliente,
    ModeloCamel,
    carga_da_linha,
    cliente_da_linha,
    meses_disponiveis,
    rotulo_mes,
)

router = APIRouter(prefix="/api/sessao", tags=["sessao"])

MSG_SEM_CLIENTE = "Sua conta ainda não tem empresa. Fale com a BORD para liberar o acesso."


class Usuario(ModeloCamel):
    nome: str | None
    email: str


class PerfilSessao(ModeloCamel):
    # admin, consultor, cliente_principal ou cliente_usuario
    id: str
    rotulo: str


class ClienteSessao(Cliente):
    slug: str


class Empresa(ModeloCamel):
    id: UUID
    nome: str


class PeriodoDisponivel(ModeloCamel):
    # AAAA-MM
    id: str
    rotulo: str
    # data dos dados mais recentes do mês
    dados_ate: date


class SessaoPayload(ModeloCamel):
    usuario: Usuario
    perfil: PerfilSessao
    # a pessoa abre a Gestão (/api/admin/*): só o Admin BORD
    gestao: bool
    # clientes que a pessoa pode abrir, por nome (o seletor de empresa)
    clientes: list[ClienteSessao]
    # o cliente escolhido; null = nenhum (as três listas abaixo vêm vazias)
    cliente: ClienteSessao | None
    papel: str | None
    empresas: list[Empresa]
    # códigos das telas ligadas no cliente e liberadas à pessoa (ex.: "VG-01")
    telas: list[str]
    periodos: list[PeriodoDisponivel]
    # texto pronto para quem não tem cliente nenhum; null nos outros casos
    aviso: str | None


SQL_USUARIO = "select nome, email from seg.usuario where id = %s"
# o RLS só mostra os clientes que a pessoa pode abrir (seg.clientes_vinculados)
SQL_CLIENTES = "select id, nome, logo_url, slug from cfg.cliente where ativo order by nome, id"
# o RLS só mostra as empresas liberadas à pessoa no cliente escolhido (para o admin, todas)
SQL_EMPRESAS = """
    select e.id, e.nome
      from cfg.empresa e
     where e.cliente_id = %s
     order by e.nome, e.id
"""
SQL_TELAS = """
    select distinct pt.codigo_tela
      from seg.usuario_perfil up
      join cfg.perfil_tela pt on pt.perfil_id = up.perfil_id
      join cfg.cliente_tela ct
        on ct.cliente_id = up.cliente_id and ct.codigo_tela = pt.codigo_tela and ct.ligada
     where up.usuario_id = %s
       and up.cliente_id = %s
     order by pt.codigo_tela
"""
SQL_TELAS_ADMIN = """
    select ct.codigo_tela
      from cfg.cliente_tela ct
     where ct.cliente_id = %s
       and ct.ligada
     order by ct.codigo_tela
"""


def _cliente(linha: dict[str, Any]) -> ClienteSessao:
    base = cliente_da_linha(linha)
    return ClienteSessao(
        nome=base.nome, sigla=base.sigla, logo_url=base.logo_url, slug=linha["slug"]
    )


@router.get(
    "",
    response_model_exclude_defaults=True,
    responses={
        401: {"description": "Não autenticado."},
        403: {"description": "Acesso negado (inclusive `cliente` que a pessoa não pode abrir)."},
    },
)
def sessao(
    ctx: Annotated[ContextoSessao, Depends(exigir_sessao)],
    pool: Annotated[Pool, Depends(get_pool)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SessaoPayload:
    conn = ctx.sessao.conn
    admin = ctx.perfil == perfis.ADMIN
    consultas: dict[str, tuple[str, Any]] = {
        "usuario": (SQL_USUARIO, (ctx.usuario_id,)),
        "clientes": (SQL_CLIENTES, ()),
    }
    if ctx.cliente_id is not None:
        c = (ctx.cliente_id,)
        consultas["empresas"] = (SQL_EMPRESAS, c)
        consultas["telas"] = (
            (SQL_TELAS_ADMIN, c) if admin else (SQL_TELAS, (ctx.usuario_id, ctx.cliente_id))
        )
        consultas["carga"] = (SQL_ULTIMA_CARGA, c)
        if existe(conn, "mart.resumo_mensal"):  # sem a migration 8, só o mês do snapshot
            consultas["meses"] = (SQL_MESES, c)
    # tudo numa ida ao banco só
    linhas = em_uma_ida(conn, consultas)
    if not linhas["usuario"]:  # a checagem já garantiu o usuário
        raise LookupError("usuário da sessão não encontrado")
    usuario = linhas["usuario"][0]
    clientes = [_cliente(r) for r in linhas["clientes"]]

    escolhido: ClienteSessao | None = None
    telas: list[str] = []
    periodos: list[PeriodoDisponivel] = []
    if ctx.cliente_id is not None:
        do_cliente = [r for r in linhas["clientes"] if r["id"] == ctx.cliente_id]
        if not do_cliente:  # a checagem já garantiu o cliente
            raise LookupError("cliente escolhido não encontrado")
        escolhido = _cliente(do_cliente[0])
        carga = carga_da_linha(linhas["carga"][0] if linhas["carga"] else None)
        telas = [r["codigo_tela"] for r in linhas["telas"]]
        # do mais recente para o mais antigo; o primeiro é o padrão da Visão geral
        periodos = [
            PeriodoDisponivel(
                id=m.mes.strftime("%Y-%m"), rotulo=rotulo_mes(m.mes), dados_ate=m.dados_ate
            )
            for m in meses_disponiveis(
                [r["mes"] for r in linhas.get("meses", [])],
                carga.data_snapshot if carga else None,
            )
        ]
        # as telas ficam prontas em segundo plano enquanto o front abre a primeira
        preparo.agendar(pool, ctx.sessao.usuario, telas, settings, escolhido.slug)

    return SessaoPayload(
        usuario=Usuario(nome=usuario["nome"], email=usuario["email"]),
        perfil=PerfilSessao(id=ctx.perfil, rotulo=perfis.rotulo(ctx.perfil)),
        gestao=admin,
        clientes=clientes,
        cliente=escolhido,
        papel=ctx.papel,
        empresas=[Empresa(id=r["id"], nome=r["nome"]) for r in linhas.get("empresas", [])],
        telas=telas,
        periodos=periodos,
        aviso=MSG_SEM_CLIENTE if not clientes and not admin else None,
    )

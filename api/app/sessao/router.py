"""GET /api/sessao: o que a moldura do front precisa antes de abrir uma tela.

Usuário (nome e e-mail do cadastro em seg.usuario, não do token), cliente, empresas do
vínculo (filtro "Empresa"), telas liberadas (menu) e períodos com dados (filtro "Período":
os meses com faturado no resumo mensal e o mês do último snapshot, do mais recente para o
mais antigo). O cliente vem do vínculo no banco, nunca da URL do front: o `slug` volta aqui
para o front montar a URL certa.

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


class Usuario(ModeloCamel):
    nome: str | None
    email: str


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
    cliente: ClienteSessao
    papel: str
    empresas: list[Empresa]
    # códigos das telas liberadas por algum perfil do usuário (ex.: "VG-01")
    telas: list[str]
    periodos: list[PeriodoDisponivel]


SQL_USUARIO = "select nome, email from seg.usuario where id = %s"
SQL_CLIENTE = "select nome, logo_url, slug from cfg.cliente where id = %s"
SQL_EMPRESAS = """
    select e.id, e.nome
      from seg.usuario_empresa ue
      join cfg.empresa e on e.id = ue.empresa_id and e.cliente_id = ue.cliente_id
     where ue.usuario_id = %s
       and ue.cliente_id = %s
     order by e.nome, e.id
"""
SQL_TELAS = """
    select distinct pt.codigo_tela
      from seg.usuario_perfil up
      join cfg.perfil_tela pt on pt.perfil_id = up.perfil_id
     where up.usuario_id = %s
       and up.cliente_id = %s
     order by pt.codigo_tela
"""


@router.get(
    "",
    response_model_exclude_defaults=True,
    responses={401: {"description": "Não autenticado."}, 403: {"description": "Acesso negado."}},
)
def sessao(
    ctx: Annotated[ContextoSessao, Depends(exigir_sessao)],
    pool: Annotated[Pool, Depends(get_pool)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SessaoPayload:
    conn = ctx.sessao.conn
    u, c = (ctx.usuario_id,), (ctx.cliente_id,)
    consultas: dict[str, tuple[str, Any]] = {
        "usuario": (SQL_USUARIO, u),
        "cliente": (SQL_CLIENTE, c),
        "empresas": (SQL_EMPRESAS, (ctx.usuario_id, ctx.cliente_id)),
        "telas": (SQL_TELAS, (ctx.usuario_id, ctx.cliente_id)),
        "carga": (SQL_ULTIMA_CARGA, c),
    }
    if existe(conn, "mart.resumo_mensal"):  # sem a migration 8, só o mês do snapshot
        consultas["meses"] = (SQL_MESES, c)
    # tudo numa ida ao banco só
    linhas = em_uma_ida(conn, consultas)
    if not linhas["usuario"] or not linhas["cliente"]:  # o vínculo já garantiu os dois
        raise LookupError("usuário ou cliente do vínculo não encontrado")
    usuario = linhas["usuario"][0]
    base = cliente_da_linha(linhas["cliente"][0])
    carga = carga_da_linha(linhas["carga"][0] if linhas["carga"] else None)
    telas = [r["codigo_tela"] for r in linhas["telas"]]

    # do mais recente para o mais antigo; o primeiro é o padrão da Visão geral
    periodos = [
        PeriodoDisponivel(
            id=m.mes.strftime("%Y-%m"), rotulo=rotulo_mes(m.mes), dados_ate=m.dados_ate
        )
        for m in meses_disponiveis(
            [r["mes"] for r in linhas.get("meses", [])], carga.data_snapshot if carga else None
        )
    ]

    # as telas ficam prontas em segundo plano enquanto o front abre a primeira
    preparo.agendar(pool, ctx.sessao.usuario, telas, settings)

    return SessaoPayload(
        usuario=Usuario(nome=usuario["nome"], email=usuario["email"]),
        cliente=ClienteSessao(
            nome=base.nome,
            sigla=base.sigla,
            logo_url=base.logo_url,
            slug=linhas["cliente"][0]["slug"],
        ),
        papel=ctx.papel,
        empresas=[Empresa(id=r["id"], nome=r["nome"]) for r in linhas["empresas"]],
        telas=telas,
        periodos=periodos,
    )

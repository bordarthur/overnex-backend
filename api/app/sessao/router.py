"""GET /api/sessao: o que a moldura do front precisa antes de abrir uma tela.

Usuário, cliente, empresas do vínculo (filtro "Empresa"), telas liberadas (menu) e
períodos com dados (filtro "Período"). O cliente vem do vínculo no banco, nunca da URL do
front: o `slug` volta aqui para o front montar a URL certa.
"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.seguranca.acesso import ContextoSessao, exigir_sessao
from app.telas.moldura import Cliente, ModeloCamel, cliente, rotulo_mes, ultima_carga

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


@router.get(
    "",
    response_model_exclude_defaults=True,
    responses={401: {"description": "Não autenticado."}, 403: {"description": "Acesso negado."}},
)
def sessao(ctx: Annotated[ContextoSessao, Depends(exigir_sessao)]) -> SessaoPayload:
    conn = ctx.sessao.conn

    usuario = conn.execute(
        "select email from seg.usuario where id = %s", (ctx.usuario_id,)
    ).fetchone()
    slug = conn.execute("select slug from cfg.cliente where id = %s", (ctx.cliente_id,)).fetchone()
    if usuario is None or slug is None:  # o vínculo já garantiu os dois
        raise LookupError("usuário ou cliente do vínculo não encontrado")

    empresas = [
        Empresa(id=r["id"], nome=r["nome"])
        for r in conn.execute(
            """
            select e.id, e.nome
              from seg.usuario_empresa ue
              join cfg.empresa e on e.id = ue.empresa_id and e.cliente_id = ue.cliente_id
             where ue.usuario_id = %s
               and ue.cliente_id = %s
             order by e.nome, e.id
            """,
            (ctx.usuario_id, ctx.cliente_id),
        )
    ]
    telas = [
        r["codigo_tela"]
        for r in conn.execute(
            """
            select distinct pt.codigo_tela
              from seg.usuario_perfil up
              join cfg.perfil_tela pt on pt.perfil_id = up.perfil_id
             where up.usuario_id = %s
               and up.cliente_id = %s
             order by pt.codigo_tela
            """,
            (ctx.usuario_id, ctx.cliente_id),
        )
    ]

    # TODO(historico): com mart.indicador_mensal, listar também os meses fechados.
    # Hoje as tabelas de movimento guardam só o mês do último snapshot.
    carga = ultima_carga(conn, ctx.cliente_id)
    periodos = (
        [
            PeriodoDisponivel(
                id=carga.data_snapshot.strftime("%Y-%m"),
                rotulo=rotulo_mes(carga.data_snapshot),
                dados_ate=carga.data_snapshot,
            )
        ]
        if carga
        else []
    )

    base = cliente(conn, ctx.cliente_id)
    return SessaoPayload(
        usuario=Usuario(nome=ctx.sessao.usuario.nome, email=usuario["email"]),
        cliente=ClienteSessao(
            nome=base.nome, sigla=base.sigla, logo_url=base.logo_url, slug=slug["slug"]
        ),
        papel=ctx.papel,
        empresas=empresas,
        telas=telas,
        periodos=periodos,
    )

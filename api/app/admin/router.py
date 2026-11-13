"""Gestão (Interno BORD): /api/admin/*, só para o perfil Admin BORD.

- GET  /api/admin/gestao                       empresas (com as camadas), perfis e sessões
- GET  /api/admin/clientes/{slug}              tudo das abas de uma empresa, numa resposta
- PUT  /api/admin/clientes/{slug}/paineis/{codigo}                  liga ou desliga o painel
- PUT  /api/admin/clientes/{slug}/perfis/{perfil}/paineis/{codigo}  matriz perfil × painel
- PUT  /api/admin/clientes/{slug}/preferencias/{id}                 escolha salva do cliente
- PUT e DELETE /api/admin/clientes/{slug}/feriados/{data}           feriado do cliente
- GET  /api/admin/usuarios                     todas as pessoas e a contagem dos filtros
- PUT  /api/admin/usuarios/{id}                nome, perfil, empresas e sessão
- PUT  /api/admin/usuarios/{id}/sessao         só o tempo de sessão
- PUT  /api/admin/usuarios/{id}/acesso         desativa ou reativa

Quem não é admin recebe o 403 genérico (negado na etapa 3, com log `admin:<rota>`). Slug
que não existe: 404. Regra quebrada: 422 com o texto pronto em `detail`. As gravações
respondem 204, confirmam antes de responder e vão para seg.log_alteracao.

Não há rota de convite: criar login precisa da chave secreta do Supabase, que fica fora da
API (ver app/cli/convidar_usuario.py). "Empresa" na tela é o cfg.cliente.
"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status

from app.admin import clientes, textos, usuarios
from app.admin.contrato import (
    ClientePayload,
    GestaoPayload,
    MudancaAcesso,
    MudancaAcessoPerfil,
    MudancaFeriado,
    MudancaPainel,
    MudancaPreferencia,
    MudancaSessao,
    MudancaUsuario,
    UsuariosPayload,
)
from app.admin.usuarios import NaoEncontrado, RegraInvalida
from app.core.config import Settings, get_settings
from app.core.db import confirmar_agora, em_uma_ida
from app.seguranca.acesso import (
    MSG_CLIENTE_INEXISTENTE,
    ContextoAdmin,
    exigir_admin,
    exigir_admin_no_cliente,
)
from app.telas.paineis import router as paineis
from app.telas.visao_geral import router as visao_geral

router = APIRouter(prefix="/api/admin", tags=["admin"])

RESPOSTAS: dict[int | str, dict[str, str]] = {
    401: {"description": "Não autenticado."},
    403: {"description": "Acesso negado (só o Admin BORD)."},
}
RESPOSTAS_CLIENTE: dict[int | str, dict[str, str]] = {
    **RESPOSTAS,
    404: {"description": f'"{MSG_CLIENTE_INEXISTENTE}" ou item da rota não encontrado.'},
}
RESPOSTAS_GRAVAR: dict[int | str, dict[str, str]] = {
    **RESPOSTAS_CLIENTE,
    422: {"description": "Regra da Gestão quebrada; o texto vem pronto em `detail`."},
}

Config = Annotated[Settings, Depends(get_settings)]
CodigoUrl = Annotated[
    str, Path(pattern=r"^[A-Z]{2,5}-[0-9]{2}$", description="Código do painel (ex.: COM-01).")
]


def _cliente(ctx: ContextoAdmin) -> UUID:
    if ctx.cliente_id is None:  # exigir_admin_no_cliente já respondeu 404
        raise LookupError("rota de cliente sem cliente escolhido")
    return ctx.cliente_id


def _erro(exc: Exception) -> HTTPException:
    """404 para o que não existe e 422 para regra quebrada, com o texto pronto."""
    codigo = (
        status.HTTP_404_NOT_FOUND
        if isinstance(exc, NaoEncontrado)
        else status.HTTP_422_UNPROCESSABLE_CONTENT
    )
    return HTTPException(status_code=codigo, detail=str(exc))


def _gravado(ctx: ContextoAdmin, *, telas_do_cliente: bool = False) -> Response:
    """Confirma antes de responder (o front pede a tela de novo logo depois do 204). Com
    `telas_do_cliente`, apaga as respostas prontas das telas do cliente."""
    confirmar_agora(ctx.sessao)
    if telas_do_cliente and ctx.cliente_id is not None:
        visao_geral.apagar_respostas_do_cliente(ctx.cliente_id)
        paineis.apagar_respostas_do_cliente(ctx.cliente_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- empresas ----------


@router.get("/gestao", responses=RESPOSTAS)
def gestao(ctx: Annotated[ContextoAdmin, Depends(exigir_admin("gestao"))]) -> GestaoPayload:
    """Moldura da Gestão: empresas com o estado das camadas, os 4 perfis e as opções de
    sessão."""
    return clientes.gestao(ctx.sessao.conn)


@router.get("/clientes/{slug}", responses=RESPOSTAS_CLIENTE)
def cliente(
    ctx: Annotated[ContextoAdmin, Depends(exigir_admin_no_cliente("cliente"))],
    settings: Config,
) -> ClientePayload:
    """Tudo das abas de uma empresa: camadas, fontes, painéis, matriz de acesso, pessoas,
    preferências, feriados e últimas cargas."""
    return clientes.cliente(ctx.sessao.conn, _cliente(ctx), settings.auth_convite_horas)


@router.put(
    "/clientes/{slug}/paineis/{codigo}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=RESPOSTAS_GRAVAR,
)
def ligar_painel(
    ctx: Annotated[ContextoAdmin, Depends(exigir_admin_no_cliente("painel"))],
    codigo: CodigoUrl,
    corpo: MudancaPainel,
) -> Response:
    """Liga ou desliga o painel na empresa. Painel sem dado não liga (422)."""
    try:
        clientes.ligar_painel(ctx.sessao.conn, ctx.usuario_id, _cliente(ctx), codigo, corpo.ligado)
    except (NaoEncontrado, RegraInvalida) as exc:
        raise _erro(exc) from exc
    return _gravado(ctx)


@router.put(
    "/clientes/{slug}/perfis/{perfil}/paineis/{codigo}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=RESPOSTAS_GRAVAR,
)
def mudar_matriz(
    ctx: Annotated[ContextoAdmin, Depends(exigir_admin_no_cliente("matriz"))],
    perfil: Annotated[str, Path(max_length=40, description="Perfil (ex.: cliente_usuario).")],
    codigo: CodigoUrl,
    corpo: MudancaAcessoPerfil,
) -> Response:
    """Define se o perfil vê o painel nesta empresa. O Admin BORD não muda (422)."""
    try:
        clientes.mudar_matriz(
            ctx.sessao.conn, ctx.usuario_id, _cliente(ctx), perfil, codigo, corpo.ve
        )
    except (NaoEncontrado, RegraInvalida) as exc:
        raise _erro(exc) from exc
    return _gravado(ctx)


@router.put(
    "/clientes/{slug}/preferencias/{id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=RESPOSTAS_GRAVAR,
)
def gravar_preferencia(
    ctx: Annotated[ContextoAdmin, Depends(exigir_admin_no_cliente("preferencia"))],
    id_: Annotated[str, Path(alias="id", max_length=40, description="Id da preferência.")],
    corpo: MudancaPreferencia,
) -> Response:
    """Grava uma escolha salva da empresa (a mesma do seletor da Visão geral)."""
    try:
        clientes.gravar_preferencia(
            ctx.sessao.conn, ctx.usuario_id, _cliente(ctx), id_, corpo.valor, date.today()
        )
    except (NaoEncontrado, RegraInvalida) as exc:
        raise _erro(exc) from exc
    return _gravado(ctx, telas_do_cliente=True)


@router.put(
    "/clientes/{slug}/feriados/{data}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=RESPOSTAS_GRAVAR,
)
def gravar_feriado(
    ctx: Annotated[ContextoAdmin, Depends(exigir_admin_no_cliente("feriado"))],
    data: Annotated[date, Path(description="Dia do feriado (AAAA-MM-DD).")],
    corpo: MudancaFeriado,
) -> Response:
    """Cadastra um feriado da empresa (municipal, estadual ou ponte), ou troca o nome."""
    clientes.gravar_feriado(ctx.sessao.conn, ctx.usuario_id, _cliente(ctx), data, corpo.nome)
    return _gravado(ctx, telas_do_cliente=True)


@router.delete(
    "/clientes/{slug}/feriados/{data}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=RESPOSTAS_CLIENTE,
)
def apagar_feriado(
    ctx: Annotated[ContextoAdmin, Depends(exigir_admin_no_cliente("feriado"))],
    data: Annotated[date, Path(description="Dia do feriado (AAAA-MM-DD).")],
) -> Response:
    """Tira um feriado da empresa."""
    try:
        clientes.apagar_feriado(ctx.sessao.conn, ctx.usuario_id, _cliente(ctx), data)
    except NaoEncontrado as exc:
        raise _erro(exc) from exc
    return _gravado(ctx, telas_do_cliente=True)


# ---------- pessoas ----------


@router.get("/usuarios", responses=RESPOSTAS)
def listar_usuarios(
    ctx: Annotated[ContextoAdmin, Depends(exigir_admin("usuarios"))],
    settings: Config,
) -> UsuariosPayload:
    """Todas as pessoas com perfil, empresas, sessão, situação e registro de acessos."""
    linhas = em_uma_ida(
        ctx.sessao.conn,
        {
            "usuarios": (usuarios.SQL_USUARIOS, ()),
            "admins": (usuarios.SQL_ADMINS_ATIVOS, ()),
            "total_clientes": (usuarios.SQL_TOTAL_CLIENTES, ()),
        },
    )
    agora = textos.agora()
    admins = linhas["admins"][0]["total"]
    total_clientes = linhas["total_clientes"][0]["total"]
    lista = [
        usuarios.usuario(
            usuarios.pessoa_da_linha(r), agora, settings.auth_convite_horas, total_clientes, admins
        )
        for r in linhas["usuarios"]
    ]
    return UsuariosPayload(
        usuarios=lista, filtros=usuarios.filtros(lista), nota_convite=usuarios.NOTA_CONVITE
    )


UsuarioUrl = Annotated[UUID, Path(alias="id", description="`id` da pessoa.")]


@router.put(
    "/usuarios/{id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=RESPOSTAS_GRAVAR,
)
def mudar_usuario(
    ctx: Annotated[ContextoAdmin, Depends(exigir_admin("usuario"))],
    settings: Config,
    id_: UsuarioUrl,
    corpo: MudancaUsuario,
) -> Response:
    """Troca nome, perfil, empresas e sessão da pessoa. O e-mail é o login e não muda."""
    try:
        usuarios.mudar(
            ctx.sessao.conn,
            ctx.usuario_id,
            id_,
            nome=corpo.nome,
            perfil=corpo.perfil,
            clientes=corpo.clientes,
            sessao_minutos=corpo.sessao_minutos,
            admin_dominios=settings.admin_dominios,
        )
    except (NaoEncontrado, RegraInvalida) as exc:
        raise _erro(exc) from exc
    return _gravado(ctx)


@router.put(
    "/usuarios/{id}/sessao",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=RESPOSTAS_GRAVAR,
)
def mudar_sessao(
    ctx: Annotated[ContextoAdmin, Depends(exigir_admin("usuario-sessao"))],
    id_: UsuarioUrl,
    corpo: MudancaSessao,
) -> Response:
    """Troca o tempo de sessão parada da pessoa (mais de 12 h, só Admin BORD)."""
    try:
        usuarios.mudar_sessao(ctx.sessao.conn, ctx.usuario_id, id_, corpo.minutos)
    except (NaoEncontrado, RegraInvalida) as exc:
        raise _erro(exc) from exc
    return _gravado(ctx)


@router.put(
    "/usuarios/{id}/acesso",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=RESPOSTAS_GRAVAR,
)
def mudar_acesso(
    ctx: Annotated[ContextoAdmin, Depends(exigir_admin("usuario-acesso"))],
    id_: UsuarioUrl,
    corpo: MudancaAcesso,
) -> Response:
    """Desativa ou reativa o acesso da pessoa; desativar encerra as sessões abertas dela."""
    try:
        usuarios.mudar_acesso(ctx.sessao.conn, ctx.usuario_id, id_, corpo.ativo)
    except (NaoEncontrado, RegraInvalida) as exc:
        raise _erro(exc) from exc
    return _gravado(ctx)

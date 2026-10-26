"""Painéis comerciais: GET /api/telas/vendas (COM-01), /equipe-comercial (COM-02) e
/clientes (COM-03).

Mesmo padrão da Visão geral (app/telas/visao_geral/router.py): acesso pela tela, `empresa`
opcional (sem ela, todas as do vínculo), `mes` (AAAA-MM, um dos de /api/sessao) e a
resposta pronta em memória por VISAO_GERAL_CACHE_SEGUNDOS, com chave no cliente, nas
empresas, no mês, nos filtros e na última carga.

Filtros próprios de cada tela, pelo `id` que a resposta manda em `filtros`: Vendas, canal e
linha; Equipe, canal e supervisor; Clientes, nenhum. As opções saem dos dados; opção fora
da lista → 422 "Filtro inválido.". A Equipe mostra nomes de vendedores: exige um perfil
que veja dado nominal (etapa 6).

Antes da primeira carga do cliente: 200 com dadosAte e atualizadoEm nulos e tudo vazio.
"""

import json
from collections.abc import Callable, Mapping
from datetime import date
from functools import cache
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.cache import CacheComPrazo, por_requisicao
from app.core.config import Settings, get_settings
from app.seguranca.acesso import ContextoConsolidado, exigir_tela_consolidada
from app.telas.moldura import UltimaCarga
from app.telas.paineis import clientes, equipe, vendas
from app.telas.paineis.base import Conteudo, Opcoes, escolher
from app.telas.paineis.contrato import PainelPayload, TelaPainel
from app.telas.paineis.explicacoes import com_explicacao
from app.telas.preleitura import Inicio, preler
from app.telas.visao_geral.router import FORMATO_MES, mes_escolhido

CODIGOS: Mapping[TelaPainel, str] = {
    "vendas": "COM-01",
    "equipe-comercial": "COM-02",
    "clientes": "COM-03",
}
# telas que mostram nomes de pessoas: só para perfil que vê dado nominal (etapa 6)
NOMINAL: frozenset[TelaPainel] = frozenset({"equipe-comercial"})
DEMO = Path(__file__).with_name("demo")

# (contexto, mês escolhido ou None, a última carga, filtros pedidos) → conteúdo
type Montador = Callable[
    [ContextoConsolidado, date | None, UltimaCarga, Mapping[str, str | None]], Conteudo
]
MONTADORES: dict[TelaPainel, Montador] = {
    "vendas": vendas.montar,
    "equipe-comercial": equipe.montar,
    "clientes": clientes.montar,
}

_respostas: CacheComPrazo[PainelPayload] = CacheComPrazo(maximo=256)

router = APIRouter(prefix="/api/telas", tags=["telas"])

RESPOSTAS: dict[int | str, dict[str, str]] = {
    401: {"description": "Não autenticado."},
    403: {"description": "Acesso negado."},
    422: {"description": "`mes` fora do formato AAAA-MM, mês sem dados ou filtro inválido."},
}

Mes = Annotated[
    str | None,
    Query(pattern=FORMATO_MES, description="Mês da tela (AAAA-MM), um dos de /api/sessao."),
]


def _filtro(rotulo: str) -> object:
    return Query(
        max_length=100,
        description=f"{rotulo}: um `id` das opções do filtro na resposta. Sem ele, todos.",
    )


@cache
def demo(tela: TelaPainel) -> PainelPayload:
    """Painel fictício do mockup (cópia de src/api/fixtures/<tela>.json do front)."""
    dados = json.loads((DEMO / f"{tela}.json").read_text("utf-8"))
    return PainelPayload.model_validate(dados)


@router.get("/vendas", response_model_exclude_defaults=True, responses=RESPOSTAS)
def tela_vendas(
    ctx: Annotated[ContextoConsolidado, Depends(exigir_tela_consolidada(CODIGOS["vendas"]))],
    settings: Annotated[Settings, Depends(get_settings)],
    mes: Mes = None,
    canal: Annotated[str | None, _filtro("Canal")] = None,
    linha: Annotated[str | None, _filtro("Linha de produto")] = None,
) -> PainelPayload:
    return responder(ctx, settings, "vendas", mes, {"canal": canal, "linha": linha})


@router.get("/equipe-comercial", response_model_exclude_defaults=True, responses=RESPOSTAS)
def tela_equipe(
    ctx: Annotated[
        ContextoConsolidado,
        Depends(
            exigir_tela_consolidada(
                CODIGOS["equipe-comercial"], exige_dado_nominal="equipe-comercial" in NOMINAL
            )
        ),
    ],
    settings: Annotated[Settings, Depends(get_settings)],
    mes: Mes = None,
    canal: Annotated[str | None, _filtro("Canal")] = None,
    supervisor: Annotated[str | None, _filtro("Supervisor")] = None,
) -> PainelPayload:
    filtros = {"canal": canal, "supervisor": supervisor}
    return responder(ctx, settings, "equipe-comercial", mes, filtros)


@router.get("/clientes", response_model_exclude_defaults=True, responses=RESPOSTAS)
def tela_clientes(
    ctx: Annotated[ContextoConsolidado, Depends(exigir_tela_consolidada(CODIGOS["clientes"]))],
    settings: Annotated[Settings, Depends(get_settings)],
    mes: Mes = None,
) -> PainelPayload:
    return responder(ctx, settings, "clientes", mes, {})


def responder(
    ctx: ContextoConsolidado,
    settings: Settings,
    tela: TelaPainel,
    mes: str | None = None,
    filtros: Mapping[str, str | None] | None = None,
) -> PainelPayload:
    """A resposta da tela: do cache ou montada (também pela pré-montagem, app/telas/preparo.py)."""
    filtros = filtros or {}
    hoje = date.today()
    pedidos = tuple(sorted((k, v) for k, v in filtros.items() if v is not None))
    with por_requisicao():
        inicio = preler(ctx.sessao.conn, ctx.cliente_id, com_meses=mes is not None)
        # tudo de que a resposta depende (ver a Visão geral)
        chave = (
            tela,
            ctx.cliente_id,
            ctx.empresa_id,
            ctx.empresas,
            mes,
            pedidos,
            inicio.carga,
            hoje,
            settings.visao_geral_demo,
        )
        return _respostas.obter_ou_montar(
            chave,
            lambda: montar(ctx, settings, tela, inicio, mes, filtros),
            settings.visao_geral_cache_segundos,
        )


def montar(
    ctx: ContextoConsolidado,
    settings: Settings,
    tela: TelaPainel,
    inicio: Inicio,
    mes: str | None,
    filtros: Mapping[str, str | None],
) -> PainelPayload:
    carga = inicio.carga
    escolhido: date | None = None
    if mes is not None:
        # mês da lista de /api/sessao (do demo também), antes de qualquer conta
        escolhido = mes_escolhido(mes, inicio.meses or [])

    if settings.visao_geral_demo:
        conteudo = conteudo_demo(tela, filtros)
    elif carga is None:
        # antes da primeira carga: nada a mostrar, e nenhum filtro vale
        for valor in filtros.values():
            escolher(None, valor)
        conteudo = Conteudo()
    else:
        conteudo = MONTADORES[tela](ctx, escolhido, carga, filtros)
        conteudo.cartoes = com_explicacao(tela, conteudo.cartoes)

    return PainelPayload(
        tela=tela,
        versao_contrato="1.0",
        cliente=inicio.cliente,
        dados_ate=carga.data_snapshot if carga else None,
        atualizado_em=carga.fim if carga else None,
        filtros=conteudo.filtros,
        aviso=conteudo.aviso,
        destaques=conteudo.destaques,
        kpis=conteudo.kpis,
        apoios=conteudo.apoios,
        cartoes=conteudo.cartoes,
    )


def conteudo_demo(tela: TelaPainel, filtros: Mapping[str, str | None]) -> Conteudo:
    """O mockup; os filtros são conferidos contra os da fixture, mas não mudam os números.
    O aviso fica de fora: cita um módulo que ainda não existe. As explicações ("i") são as
    da fixture."""
    p = demo(tela)
    por_id = {f.id: f for f in p.filtros}
    for id_filtro, valor in filtros.items():
        f = por_id.get(id_filtro)
        o = None if f is None else Opcoes(f, {op.id: (op.id,) for op in f.opcoes})
        escolher(o, valor)
    return Conteudo(
        filtros=list(p.filtros),
        destaques=list(p.destaques),
        kpis=list(p.kpis),
        apoios=list(p.apoios),
        cartoes=list(p.cartoes),
    )

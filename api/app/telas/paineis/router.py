"""Painéis por cartões: GET /api/telas/vendas (COM-01), /equipe-comercial (COM-02),
/clientes (COM-03), /resultado (FIN-02) e /receber-pagar (FIN-03).

Mesmo padrão da Visão geral (app/telas/visao_geral/router.py): acesso pela tela, `empresa`
opcional (sem ela, todas as do vínculo), `mes` (AAAA-MM, um dos de /api/sessao) e a
resposta pronta em memória por VISAO_GERAL_CACHE_SEGUNDOS, com chave no cliente, nas
empresas, no mês, nos filtros e na última carga.

Filtros próprios de cada tela, pelo `id` que a resposta manda em `filtros`: Vendas, canal e
linha; Equipe, canal e supervisor; Clientes, nenhum; Resultado, visao e cenario; Receber e
pagar, vendedor e atraso. As opções saem dos dados; opção fora da lista → 422 "Filtro
inválido.". O Resultado aceita também `comparar` (orcado ou mes-anterior), que vem do chip
global "Comparar com" do front e não vai em `filtros`. A Equipe e o Receber e pagar mostram
nomes de pessoas (vendedores, clientes): exigem um perfil que veja dado nominal (etapa 6).

Cartões "a decidir" (A_DECIDIR e o nível 3 da DRE): só saem com PAINEIS_A_DECIDIR (dev).

Antes da primeira carga do cliente: 200 com dadosAte e atualizadoEm nulos e tudo vazio.
"""

import json
from collections.abc import Callable, Mapping
from datetime import date
from functools import cache
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.cache import CacheComPrazo, por_requisicao
from app.core.config import Settings, get_settings
from app.seguranca.acesso import ContextoConsolidado, exigir_tela_consolidada
from app.telas.moldura import UltimaCarga
from app.telas.paineis import clientes, equipe, receber_pagar, resultado, vendas
from app.telas.paineis.base import MSG_FILTRO_INVALIDO, Conteudo, Opcoes, escolher
from app.telas.paineis.contrato import Cartao, CartaoDre, PainelPayload, TelaPainel
from app.telas.paineis.explicacoes import com_explicacao
from app.telas.preleitura import Inicio, preler
from app.telas.visao_geral.router import FORMATO_MES, mes_escolhido

CODIGOS: Mapping[TelaPainel, str] = {
    "vendas": "COM-01",
    "equipe-comercial": "COM-02",
    "clientes": "COM-03",
    "resultado": "FIN-02",
    "receber-pagar": "FIN-03",
}
# telas que mostram nomes de pessoas: só para perfil que vê dado nominal (etapa 6)
NOMINAL: frozenset[TelaPainel] = frozenset({"equipe-comercial", "receber-pagar"})
# cartões marcados "A decidir" no mockup: só com PAINEIS_A_DECIDIR (o nível 3 da DRE também)
A_DECIDIR: Mapping[TelaPainel, frozenset[str]] = {
    "resultado": frozenset({"ponte-ebitda", "maiores-desvios", "tabela-financeira"}),
}
# parâmetros que não são filtros da tela (chips globais do front): valem sempre
GLOBAIS = frozenset({"comparar"})
COMPARAR = ("orcado", "mes-anterior")
DEMO = Path(__file__).with_name("demo")

# (contexto, mês escolhido ou None, a última carga, filtros pedidos, cartões a decidir)
# → conteúdo
type Montador = Callable[
    [ContextoConsolidado, date | None, UltimaCarga, Mapping[str, str | None], bool], Conteudo
]
MONTADORES: dict[TelaPainel, Montador] = {
    "vendas": vendas.montar,
    "equipe-comercial": equipe.montar,
    "clientes": clientes.montar,
    "resultado": resultado.montar,
    "receber-pagar": receber_pagar.montar,
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


def _filtro(rotulo: str, padrao: str = "todos") -> object:
    return Query(
        max_length=100,
        description=f"{rotulo}: um `id` das opções do filtro na resposta. Sem ele, {padrao}.",
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


@router.get("/resultado", response_model_exclude_defaults=True, responses=RESPOSTAS)
def tela_resultado(
    ctx: Annotated[ContextoConsolidado, Depends(exigir_tela_consolidada(CODIGOS["resultado"]))],
    settings: Annotated[Settings, Depends(get_settings)],
    mes: Mes = None,
    visao: Annotated[str | None, _filtro("Visão (mes, ano ou projecao)", "mes")] = None,
    cenario: Annotated[str | None, _filtro("Cenário do orçamento", "o padrão")] = None,
    comparar: Annotated[
        str | None,
        Query(
            max_length=20,
            description="Comparar com: `orcado` (padrão) ou `mes-anterior` (chip global).",
        ),
    ] = None,
) -> PainelPayload:
    filtros = {"visao": visao, "cenario": cenario, "comparar": _comparar(comparar)}
    return responder(ctx, settings, "resultado", mes, filtros)


@router.get("/receber-pagar", response_model_exclude_defaults=True, responses=RESPOSTAS)
def tela_receber_pagar(
    ctx: Annotated[
        ContextoConsolidado,
        Depends(
            exigir_tela_consolidada(
                CODIGOS["receber-pagar"], exige_dado_nominal="receber-pagar" in NOMINAL
            )
        ),
    ],
    settings: Annotated[Settings, Depends(get_settings)],
    mes: Mes = None,
    vendedor: Annotated[str | None, _filtro("Vendedor")] = None,
    atraso: Annotated[str | None, _filtro("Atraso dos vencidos (Quem cobrar)")] = None,
) -> PainelPayload:
    filtros = {"vendedor": vendedor, "atraso": atraso}
    return responder(ctx, settings, "receber-pagar", mes, filtros)


def _comparar(valor: str | None) -> str | None:
    """`comparar` conferido antes de tudo (vale até sem carga); o padrão vira None, para a
    chave do cache ser a da pré-montagem."""
    if valor is not None and valor not in COMPARAR:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=MSG_FILTRO_INVALIDO
        )
    return None if valor == COMPARAR[0] else valor


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
            settings.paineis_a_decidir,
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
        if not settings.paineis_a_decidir:
            conteudo.cartoes = sem_a_decidir(tela, conteudo.cartoes)
    elif carga is None:
        # antes da primeira carga: nada a mostrar, e nenhum filtro da tela vale
        for id_filtro, valor in filtros.items():
            if id_filtro not in GLOBAIS:
                escolher(None, valor)
        conteudo = Conteudo()
    else:
        conteudo = MONTADORES[tela](ctx, escolhido, carga, filtros, settings.paineis_a_decidir)
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
        if id_filtro in GLOBAIS:
            continue
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


def sem_a_decidir(tela: TelaPainel, cartoes: list[Cartao]) -> list[Cartao]:
    """Sem os cartões a decidir da tela e sem o nível 3 da DRE (as telas de verdade já montam
    assim; aqui é para o demo, que é o mockup inteiro)."""
    fora = A_DECIDIR.get(tela, frozenset())
    saida: list[Cartao] = []
    for c in cartoes:
        if c.id in fora:
            continue
        if isinstance(c, CartaoDre) and 3 in c.niveis:
            c = c.model_copy(
                update={
                    "linhas": [x for x in c.linhas if x.nivel != 3],
                    "niveis": [n for n in c.niveis if n != 3],
                    "nivel_inicial": min(c.nivel_inicial, 2),
                }
            )
        saida.append(c)
    return saida

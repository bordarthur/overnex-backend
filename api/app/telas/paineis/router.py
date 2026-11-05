"""Painéis por cartões: GET /api/telas/vendas (COM-01), /equipe-comercial (COM-02),
/clientes (COM-03), /resultado (FIN-02), /receber-pagar (FIN-03) e /pessoas (PES-01).

Mesmo padrão da Visão geral (app/telas/visao_geral/router.py): acesso pela tela, `empresa`
opcional (sem ela, todas as do vínculo), `mes` (AAAA-MM, um dos de /api/sessao) e a
resposta pronta em memória por VISAO_GERAL_CACHE_SEGUNDOS, com chave no cliente, nas
empresas, no mês, nos filtros, na última carga e na versão do cadastro do cliente.

Filtros próprios de cada tela, pelo `id` que a resposta manda em `filtros`: Vendas, canal e
linha; Equipe, canal e supervisor; Clientes, nenhum; Resultado, visao e cenario; Receber e
pagar, vendedor e atraso; Pessoas, nenhum (o setor se escolhe dentro do cartão). As opções
saem dos dados; opção fora da lista → 422 "Filtro inválido.". O Resultado aceita também
`comparar` (orcado ou mes-anterior), que vem do chip global "Comparar com" do front e não
vai em `filtros`. A Equipe e o Receber e pagar mostram nomes de pessoas (vendedores,
clientes): exigem um perfil que veja dado nominal (etapa 6).
Pessoas tem dado de folha, mas sem nomes: o cadastro libera PES-01 só à diretoria e ao RH.

Cartões "a decidir" (A_DECIDIR): só saem com PAINEIS_A_DECIDIR (dev).

Nomes por cartão (NOMES; hoje só Pessoas, "Cargos e salários"): o cartão traz a chave
"Mostrar nomes" (`nomes`) só para quem vê dado nominal na tela (o perfil de RH na PES-01);
por isso o `ve_dado_nominal` entra na chave do cache. Ligada, o front pede
GET /api/telas/{tela}/cartoes/{id}/nomes, com os mesmos `mes` e `empresa` do painel. A rota
confere de novo (a chave não vale como permissão: qualquer um pode chamar a URL) e:
- 404 "Este cartão não tem nomes." para tela ou cartão fora de NOMES;
- 403 "Você não pode ver os nomes deste cartão." para quem tem a tela, mas não vê dado
  nominal; as outras negações são o 403 genérico;
- sem cache no back e com Cache-Control: no-store;
- o pedido vai para seg.log_acesso com o recurso "<código da tela>:<cartão>:nomes" e os
  filtros no motivo (quem, de qual cliente e empresa, com quais filtros).

Antes da primeira carga do cliente: 200 com dadosAte e atualizadoEm nulos e tudo vazio.
"""

import json
import logging
from collections.abc import Callable, Mapping
from datetime import date
from functools import cache
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi import Path as CaminhoUrl

from app.core.cache import CacheComPrazo, por_requisicao
from app.core.config import Settings, get_settings
from app.core.db import Sessao, get_sessao
from app.core.erros import AcessoNegado
from app.seguranca.acesso import (
    ContextoConsolidado,
    exigir_tela_consolidada,
    verificar_acesso_consolidado,
)
from app.telas.moldura import UltimaCarga
from app.telas.paineis import clientes, equipe, pessoas, receber_pagar, resultado, vendas
from app.telas.paineis.base import MSG_FILTRO_INVALIDO, Conteudo, Opcoes, escolher
from app.telas.paineis.contrato import (
    Cartao,
    CartaoTabela,
    NomesCartao,
    PainelPayload,
    TabelaNomes,
    TelaPainel,
)
from app.telas.paineis.explicacoes import com_explicacao
from app.telas.preleitura import Inicio, preler
from app.telas.visao_geral.router import FORMATO_MES, mes_escolhido

CODIGOS: Mapping[TelaPainel, str] = {
    "vendas": "COM-01",
    "equipe-comercial": "COM-02",
    "clientes": "COM-03",
    "resultado": "FIN-02",
    "receber-pagar": "FIN-03",
    "pessoas": "PES-01",
}
# telas que mostram nomes de pessoas: só para perfil que vê dado nominal (etapa 6)
NOMINAL: frozenset[TelaPainel] = frozenset({"equipe-comercial", "receber-pagar"})
# cartões marcados "A decidir" no mockup: só com PAINEIS_A_DECIDIR
A_DECIDIR: Mapping[TelaPainel, frozenset[str]] = {
    "resultado": frozenset({"ponte-ebitda", "maiores-desvios"}),
}
# parâmetros que não são filtros da tela (chips globais do front): valem sempre
GLOBAIS = frozenset({"comparar"})
COMPARAR = ("orcado", "mes-anterior")
DEMO = Path(__file__).with_name("demo")

logger = logging.getLogger(__name__)

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
    "pessoas": pessoas.montar,
}

# (contexto, mês escolhido ou None, a última carga ou None) → a tabela de nomes
type MontadorNomes = Callable[[ContextoConsolidado, date | None, UltimaCarga | None], TabelaNomes]
# cartões com a chave "Mostrar nomes" (tabela de uma linha por pessoa): só para quem vê dado
# nominal na tela
NOMES: dict[tuple[TelaPainel, str], MontadorNomes] = {
    ("pessoas", "cargos-salarios"): pessoas.nomes_cargos,
}
CHAVE_NOMES = NomesCartao(rotulo="Mostrar nomes")
MSG_SEM_NOMES = "Este cartão não tem nomes."
MSG_NOMES_NEGADO = "Você não pode ver os nomes deste cartão."

_respostas: CacheComPrazo[PainelPayload] = CacheComPrazo(maximo=256)

router = APIRouter(prefix="/api/telas", tags=["telas"])

RESPOSTAS: dict[int | str, dict[str, str]] = {
    401: {"description": "Não autenticado."},
    403: {"description": "Acesso negado."},
    422: {"description": "`mes` fora do formato AAAA-MM, mês sem dados ou filtro inválido."},
}
RESPOSTAS_NOMES: dict[int | str, dict[str, str]] = {
    401: {"description": "Não autenticado."},
    403: {"description": f'Acesso negado, ou "{MSG_NOMES_NEGADO}" (perfil sem dado nominal).'},
    404: {"description": MSG_SEM_NOMES},
    422: {"description": "`mes` fora do formato AAAA-MM ou mês sem dados."},
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


@cache
def demo_nomes(tela: TelaPainel, cartao: str) -> TabelaNomes:
    """Tabela de nomes fictícia (cópia de src/api/fixtures/<tela>-<cartão>-nomes.json)."""
    dados = json.loads((DEMO / f"{tela}-{cartao}-nomes.json").read_text("utf-8"))
    return TabelaNomes.model_validate(dados)


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


@router.get("/pessoas", response_model_exclude_defaults=True, responses=RESPOSTAS)
def tela_pessoas(
    ctx: Annotated[ContextoConsolidado, Depends(exigir_tela_consolidada(CODIGOS["pessoas"]))],
    settings: Annotated[Settings, Depends(get_settings)],
    mes: Mes = None,
) -> PainelPayload:
    return responder(ctx, settings, "pessoas", mes, {})


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
            # a chave "Mostrar nomes" só sai para quem vê dado nominal
            ctx.ve_dado_nominal,
            mes,
            pedidos,
            inicio.carga,
            hoje,
            settings.visao_geral_demo,
            settings.paineis_a_decidir,
            inicio.versao,
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
    conteudo.cartoes = com_nomes(tela, conteudo.cartoes, ctx.ve_dado_nominal)

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


def com_nomes(tela: TelaPainel, cartoes: list[Cartao], ve_dado_nominal: bool) -> list[Cartao]:
    """A chave "Mostrar nomes" nos cartões de NOMES: com ela só para quem vê dado nominal;
    sem ela para os outros (o demo já traz a chave). Cópia: o demo fica em cache."""
    com = {c for t, c in NOMES if t == tela}
    return [
        c.model_copy(update={"nomes": CHAVE_NOMES if ve_dado_nominal else None})
        if isinstance(c, CartaoTabela) and c.id in com
        else c
        for c in cartoes
    ]


def sem_a_decidir(tela: TelaPainel, cartoes: list[Cartao]) -> list[Cartao]:
    """Sem os cartões a decidir da tela (as telas de verdade já montam assim; aqui é para o
    demo, que é o mockup inteiro)."""
    fora = A_DECIDIR.get(tela, frozenset())
    return [c for c in cartoes if c.id not in fora]


# ---------- nomes por cartão ----------


def _cartao_com_nomes(tela: str, cartao: str) -> tuple[TelaPainel, str]:
    for chave in NOMES:
        if chave == (tela, cartao):
            return chave
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=MSG_SEM_NOMES)


TelaUrl = Annotated[str, CaminhoUrl(max_length=40, description="Tela do painel (ex.: pessoas).")]
CartaoUrl = Annotated[
    str, CaminhoUrl(max_length=60, description="`id` do cartão (ex.: cargos-salarios).")
]


def exigir_nomes(
    sessao: Annotated[Sessao, Depends(get_sessao)],
    tela: TelaUrl,
    cartao: CartaoUrl,
    empresa: Annotated[
        UUID | None,
        Query(description="Opcional: só esta empresa. Sem valor: todas as do vínculo."),
    ] = None,
    mes: Mes = None,
) -> ContextoConsolidado:
    """Cartão com nomes (senão 404) e as 6 etapas, com dado nominal obrigatório; a negação na
    etapa 6 tem resposta própria. O log leva o cartão e os filtros."""
    tela_painel, id_cartao = _cartao_com_nomes(tela, cartao)
    codigo = CODIGOS[tela_painel]
    try:
        return verificar_acesso_consolidado(
            sessao,
            codigo,
            empresa,
            exige_dado_nominal=True,
            recurso=f"{codigo}:{id_cartao}:nomes",
            detalhe=f"filtros: mes={mes or 'padrão'}",
        )
    except AcessoNegado as negado:
        if negado.etapa == 6:
            negado.resposta = MSG_NOMES_NEGADO
        raise


@router.get(
    "/{tela}/cartoes/{cartao}/nomes",
    response_model_exclude_defaults=True,
    responses=RESPOSTAS_NOMES,
)
def nomes_do_cartao(
    ctx: Annotated[ContextoConsolidado, Depends(exigir_nomes)],
    settings: Annotated[Settings, Depends(get_settings)],
    response: Response,
    tela: TelaUrl,
    cartao: CartaoUrl,
    mes: Mes = None,
) -> TabelaNomes:
    """Tabela de uma linha por pessoa do cartão (só o perfil que vê dado nominal na tela). Sem
    cache, nem no back nem no navegador."""
    response.headers["Cache-Control"] = "no-store"
    tela_painel, id_cartao = _cartao_com_nomes(tela, cartao)
    logger.info(
        "Nomes do cartão %s/%s pedidos: usuário %s, cliente %s, empresa %s, mês %s",
        tela_painel,
        id_cartao,
        ctx.usuario_id,
        ctx.cliente_id,
        ctx.empresa_id or "todas",
        mes or "padrão",
    )
    inicio = preler(ctx.sessao.conn, ctx.cliente_id, com_meses=mes is not None)
    escolhido = None if mes is None else mes_escolhido(mes, inicio.meses or [])
    if settings.visao_geral_demo:
        return demo_nomes(tela_painel, id_cartao)
    return NOMES[(tela_painel, id_cartao)](ctx, escolhido, inicio.carga)

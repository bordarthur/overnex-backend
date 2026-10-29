"""VG-01 — Visão geral: GET /api/telas/visao-geral.

Os dois períodos (mes e ano) vêm na mesma resposta; cada velocímetro tem a própria chave
Mês/Acumulado, e o front troca sem nova requisição. Sem `empresa`, a tela soma todas as
empresas do vínculo do usuário.

`mes` (AAAA-MM, opcional): o mês da tela, um dos que /api/sessao lista. Sem ele, cada seção
usa o seu mês mais recente (Vendas e Custos, o da última venda; Resultado, o último
fechado; Financeiro, a posição no snapshot). Com ele (ver Recorte):
- Vendas e Custos: o mês escolhido; fora da janela das tabelas de vendas, sai do resumo
  mensal, e os cartões do dia a dia viram o total do mês;
- Resultado: o mês escolhido, se fechado; no mês do snapshot, o último fechado antes dele;
- Financeiro: a posição dos títulos no último dia do mês (no mês do snapshot, na data dele).

Desempenho: a resposta pronta fica em memória por VISAO_GERAL_CACHE_SEGUNDOS (padrão 15
min; 0 desliga), com chave no cliente, nas empresas, no mês e na última carga: uma carga
nova troca a chave. Parâmetros mudados no banco (farol, de-para) valem depois do prazo; as
escolhas salvas do cliente (preferencias.py) entram na chave, e a rota que grava uma
escolha apaga as respostas do cliente. O acesso continua conferido e registrado a cada
requisição. Duas requisições da mesma chave
ao mesmo tempo (ex.: a pré-montagem depois de /api/sessao e o clique) montam uma vez só.
Ao montar, última carga, cliente, fontes, parâmetros e feriados vêm numa ida ao banco
(app/telas/preleitura.py), e as seções independentes rodam em paralelo, cada ramo com uma
conexão própria do mesmo usuário (_em_paralelo): o tempo da tela é o do ramo mais lento.
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path as Caminho
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from pydantic import TypeAdapter

from app.core.cache import CacheComPrazo, por_requisicao
from app.core.config import Settings, get_settings
from app.core.db import confirmar_agora
from app.seguranca.acesso import ContextoConsolidado, exigir_tela_consolidada
from app.telas.calendario import ultimo_dia_do_mes
from app.telas.moldura import MesComDados, ModeloCamel
from app.telas.paralelo import em_paralelo
from app.telas.preleitura import Inicio, preler
from app.telas.visao_geral import preferencias
from app.telas.visao_geral.contrato import Periodo, Secao, VisaoGeralPayload
from app.telas.visao_geral.custos import secao_custos
from app.telas.visao_geral.financeiro import secao_financeiro
from app.telas.visao_geral.resultado import secao_resultado
from app.telas.visao_geral.vendas import SecaoDoMes, mes_menos, secao_vendas

CODIGO_TELA = "VG-01"
DEMO = Caminho(__file__).with_name("demo.json")
FORMATO_MES = r"^\d{4}-(0[1-9]|1[0-2])$"
# respostas prontas (VISAO_GERAL_CACHE_SEGUNDOS); a resposta é imutável (modelo frozen)
_respostas: CacheComPrazo[VisaoGeralPayload] = CacheComPrazo(maximo=256)
PERIODOS = [Periodo(id="mes", rotulo="Mês"), Periodo(id="ano", rotulo="Acumulado")]
NOTA = (
    "Cada cartão tem a própria chave Mês/Acumulado. A comparação é com o orçado ou a meta; "
    "sem eles, com o mês anterior."
)

router = APIRouter(prefix="/api/telas", tags=["telas"])


@cache
def secoes_demo() -> list[Secao]:
    """Seções fictícias do mockup (cópia de src/api/fixtures/visao-geral.json do front)."""
    dados = json.loads(DEMO.read_text("utf-8"))
    return TypeAdapter(list[Secao]).validate_python(dados["secoes"])


@dataclass(frozen=True)
class Recorte:
    """O mês da tela e a data da posição."""

    # primeiro dia do mês escolhido; None = sem `mes` (cada seção no seu mais recente)
    mes: date | None
    # data da posição: a do snapshot no mês dele (e sem `mes`), senão o último dia do mês
    posicao: date
    # mês escolhido antes do mês do snapshot
    fechado: bool

    @property
    def ultimo_fechado(self) -> date:
        """Mês de referência do Resultado (primeiro dia): o escolhido, se fechado; senão o
        último fechado antes do mês da posição."""
        if self.fechado and self.mes is not None:
            return self.mes
        return mes_menos(self.posicao.replace(day=1), 1)


def recorte(mes: date | None, data_snapshot: date) -> Recorte:
    """`data_snapshot`: a da última carga (sem carga, hoje)."""
    if mes is None:
        return Recorte(mes=None, posicao=data_snapshot, fechado=False)
    if mes == data_snapshot.replace(day=1):
        return Recorte(mes=mes, posicao=data_snapshot, fechado=False)
    return Recorte(mes=mes, posicao=ultimo_dia_do_mes(mes), fechado=mes < data_snapshot)


def mes_escolhido(mes: str, disponiveis: list[MesComDados]) -> date:
    """AAAA-MM (formato já validado) → primeiro dia; 422 se o mês não tem dados."""
    escolhido = date(int(mes[:4]), int(mes[5:7]), 1)
    if escolhido not in {d.mes for d in disponiveis}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Mês sem dados."
        )
    return escolhido


type Ramo = Callable[[ContextoConsolidado], list[SecaoDoMes]]


def _em_paralelo(ctx: ContextoConsolidado, ramos: Sequence[Ramo]) -> list[list[SecaoDoMes]]:
    """As seções independentes ao mesmo tempo (app/telas/paralelo.py)."""
    return em_paralelo(ctx, ramos)


def _secoes(ctx: ContextoConsolidado, r: Recorte) -> list[Secao]:
    """Seções montadas.

    Três ramos independentes, em paralelo: Vendas e Custos (Custos usa a base de Vendas),
    Resultado e Financeiro.
    """
    # TODO(regra): Compras e Estoque, e a lista de seções do cliente em cfg.painel. Ver TODO.md.

    def vendas_e_custos(c: ContextoConsolidado) -> list[SecaoDoMes]:
        vendas = secao_vendas(c, r.mes, fechado=r.fechado)
        if vendas is None:
            return []
        secao, base = vendas
        custos = secao_custos(c, base)
        return [secao] if custos is None else [secao, custos]

    def resultado(c: ContextoConsolidado) -> list[SecaoDoMes]:
        s = secao_resultado(c, r.ultimo_fechado)
        return [] if s is None else [s]

    def financeiro(c: ContextoConsolidado) -> list[SecaoDoMes]:
        s = secao_financeiro(c, r.posicao)
        return [] if s is None else [s]

    return [
        s.secao
        for ramo in _em_paralelo(ctx, [vendas_e_custos, resultado, financeiro])
        for s in ramo
    ]


@router.get(
    "/visao-geral",
    response_model_exclude_defaults=True,
    responses={
        401: {"description": "Não autenticado."},
        403: {"description": "Acesso negado."},
        422: {"description": "`mes` fora do formato AAAA-MM, ou mês sem dados."},
    },
)
def visao_geral(
    ctx: Annotated[ContextoConsolidado, Depends(exigir_tela_consolidada(CODIGO_TELA))],
    settings: Annotated[Settings, Depends(get_settings)],
    mes: Annotated[
        str | None,
        Query(pattern=FORMATO_MES, description="Mês da tela (AAAA-MM), um dos de /api/sessao."),
    ] = None,
) -> VisaoGeralPayload:
    return responder(ctx, settings, mes)


def responder(
    ctx: ContextoConsolidado, settings: Settings, mes: str | None = None
) -> VisaoGeralPayload:
    """A resposta da tela: do cache ou montada (também pela pré-montagem, app/telas/preparo.py)."""
    hoje = date.today()
    with por_requisicao():
        inicio = preler(ctx.sessao.conn, ctx.cliente_id, com_meses=mes is not None)
        # Tudo de que a resposta depende. Os dados só mudam com uma carga nova (sem carga, a
        # tela usa o dia de hoje); as empresas são as que a tela soma, que o RLS já recorta.
        # O cliente vem primeiro: gravar uma escolha apaga as respostas dele (_apagar_cliente).
        chave = (
            ctx.cliente_id,
            ctx.empresa_id,
            ctx.empresas,
            mes,
            inicio.carga,
            hoje,
            settings.visao_geral_demo,
            # lidas da pré-leitura, sem outra ida ao banco
            preferencias.valores(ctx.sessao.conn, ctx.cliente_id),
        )
        return _respostas.obter_ou_montar(
            chave,
            lambda: _montar(ctx, settings, inicio, mes, hoje),
            settings.visao_geral_cache_segundos,
        )


def _montar(
    ctx: ContextoConsolidado,
    settings: Settings,
    inicio: Inicio,
    mes: str | None,
    hoje: date,
) -> VisaoGeralPayload:
    carga = inicio.carga
    if settings.visao_geral_demo:
        secoes = secoes_demo()
    else:
        escolhido = mes_escolhido(mes, inicio.meses or []) if mes is not None else None
        secoes = _secoes(ctx, recorte(escolhido, carga.data_snapshot if carga else hoje))

    return VisaoGeralPayload(
        tela="visao-geral",
        versao_contrato="1.0",
        cliente=inicio.cliente,
        dados_ate=carga.data_snapshot if carga else None,
        atualizado_em=carga.fim if carga else None,
        periodos=PERIODOS,
        nota=NOTA,
        secoes=secoes,
    )


class EscolhaPreferencia(ModeloCamel):
    valor: str


def apagar_respostas_do_cliente(cliente_id: UUID) -> int:
    """Apaga as respostas prontas do cliente (todos os meses e empresas)."""
    return _respostas.apagar_se(lambda chave: isinstance(chave, tuple) and chave[0] == cliente_id)


@router.put(
    "/visao-geral/preferencias/{id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={
        401: {"description": "Não autenticado."},
        403: {"description": "Acesso negado, ou o papel do usuário não pode mudar a escolha."},
        404: {"description": "Preferência não encontrada."},
        422: {"description": "Opção inválida."},
    },
)
def gravar_preferencia(
    ctx: Annotated[ContextoConsolidado, Depends(exigir_tela_consolidada(CODIGO_TELA))],
    id_: Annotated[str, Path(alias="id", description="Id do seletor (ex.: linhas-custo).")],
    escolha: EscolhaPreferencia,
) -> Response:
    """Grava a escolha de um seletor da Visão geral para o cliente inteiro.

    Confirma antes de responder e apaga as respostas prontas do cliente: o front pede a
    tela de novo logo depois do 204.
    """
    p = preferencias.PREFERENCIAS.get(id_)
    if p is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Preferência não encontrada."
        )
    if ctx.papel not in preferencias.PAPEIS_QUE_MUDAM:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Você não pode mudar esta escolha."
        )
    if not p.aceita(escolha.valor):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Opção inválida."
        )
    preferencias.gravar(
        ctx.sessao.conn, ctx.cliente_id, ctx.usuario_id, p, escolha.valor, date.today()
    )
    confirmar_agora(ctx.sessao)
    apagar_respostas_do_cliente(ctx.cliente_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

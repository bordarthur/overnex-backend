"""VG-01 — Visão geral: GET /api/telas/visao-geral.

Os dois períodos (mes e ano) vêm na mesma resposta; o front troca entre eles sem nova
requisição. Sem `empresa`, a tela soma todas as empresas do vínculo do usuário.
"""

import json
from datetime import date
from functools import cache
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import TypeAdapter

from app.core.config import Settings, get_settings
from app.seguranca.acesso import ContextoConsolidado, exigir_tela_consolidada
from app.telas.moldura import cliente, rotulo_mes, ultima_carga
from app.telas.visao_geral.contrato import Periodo, Secao, VisaoGeralPayload
from app.telas.visao_geral.custos import secao_custos
from app.telas.visao_geral.financeiro import secao_financeiro
from app.telas.visao_geral.resultado import secao_resultado
from app.telas.visao_geral.vendas import SecaoDoMes, secao_vendas

CODIGO_TELA = "VG-01"
DEMO = Path(__file__).with_name("demo.json")

router = APIRouter(prefix="/api/telas", tags=["telas"])


@cache
def secoes_demo() -> list[Secao]:
    """Seções fictícias do mockup (cópia de src/api/fixtures/visao-geral.json do front)."""
    dados = json.loads(DEMO.read_text("utf-8"))
    return TypeAdapter(list[Secao]).validate_python(dados["secoes"])


def _secoes(ctx: ContextoConsolidado, hoje: date) -> tuple[list[Secao], date | None]:
    """Seções montadas e o mês de referência mais recente entre elas.

    `hoje`: a data da última carga. Resultado usa o último mês fechado antes dela, e
    Financeiro, a posição dos títulos nela.
    """
    # TODO(regra): Compras e Estoque, e a lista de seções do cliente em cfg.painel. Ver TODO.md.
    montadas: list[SecaoDoMes] = []
    vendas = secao_vendas(ctx)
    if vendas is not None:
        secao, base = vendas
        montadas.append(secao)
        custos = secao_custos(ctx, base)
        if custos is not None:
            montadas.append(custos)
    for outra in (secao_resultado(ctx, hoje), secao_financeiro(ctx, hoje)):
        if outra is not None:
            montadas.append(outra)
    return [s.secao for s in montadas], max((s.mes for s in montadas), default=None)


@router.get(
    "/visao-geral",
    response_model_exclude_defaults=True,
    responses={401: {"description": "Não autenticado."}, 403: {"description": "Acesso negado."}},
)
def visao_geral(
    ctx: Annotated[ContextoConsolidado, Depends(exigir_tela_consolidada(CODIGO_TELA))],
    settings: Annotated[Settings, Depends(get_settings)],
) -> VisaoGeralPayload:
    conn = ctx.sessao.conn
    carga = ultima_carga(conn, ctx.cliente_id)

    if settings.visao_geral_demo:
        secoes, mes = secoes_demo(), None
    else:
        secoes, mes = _secoes(ctx, carga.data_snapshot if carga else date.today())

    # rótulos do mês dos números (ex.: mês fechado), ou do snapshot se não há seção
    ref = mes or (carga.data_snapshot if carga else None)
    periodos: list[Periodo] = []
    if ref is not None:
        periodos = [
            Periodo(id="mes", rotulo=rotulo_mes(ref)),
            Periodo(id="ano", rotulo=f"Acumulado de {ref.year}"),
        ]

    return VisaoGeralPayload(
        tela="visao-geral",
        versao_contrato="1.0",
        cliente=cliente(conn, ctx.cliente_id),
        dados_ate=carga.data_snapshot if carga else None,
        atualizado_em=carga.fim if carga else None,
        periodos=periodos,
        secoes=secoes,
    )

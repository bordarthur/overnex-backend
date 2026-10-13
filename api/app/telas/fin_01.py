"""FIN-01 — tela de exemplo. Sem regra de negócio: só mostra o caminho mart → contrato."""

from datetime import date
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.db import Conexao
from app.seguranca.acesso import ContextoAcesso, exigir_tela
from app.telas.contrato import Alerta, Apoio, Hero, Ponto, ScreenPayload, Trecho, Visual

CODIGO_TELA = "FIN-01"
PADRAO_MES = r"^\d{4}-(0[1-9]|1[0-2])$"

router = APIRouter(prefix="/api/telas", tags=["telas"])


def atualizado_em(conn: Conexao, cliente_id: UUID) -> date | None:
    """data_snapshot da última carga 'ok' de lançamentos do cliente; None se nunca houve."""
    linha = conn.execute(
        """
        select cf.data_snapshot
          from mart.carga_fonte cf
          join cfg.fonte_carga f on f.id = cf.fonte_id
         where cf.cliente_id = %s
           and cf.status = 'ok'
           and f.tipo = 'lancamento'
         order by cf.inicio desc, cf.id desc
         limit 1
        """,
        (cliente_id,),
    ).fetchone()
    return linha["data_snapshot"] if linha else None


def _mes_referencia(conn: Conexao, empresa_id: UUID, mes: str | None) -> date | None:
    if mes:
        ano, m = mes.split("-")
        return date(int(ano), int(m), 1)
    linha = conn.execute(
        "select max(mes) as mes from mart.lancamento where empresa_id = %s", (empresa_id,)
    ).fetchone()
    return linha["mes"] if linha else None


@router.get(
    "/fin-01",
    responses={401: {"description": "Não autenticado."}, 403: {"description": "Acesso negado."}},
)
def fin_01(
    ctx: Annotated[ContextoAcesso, Depends(exigir_tela(CODIGO_TELA))],
    mes: Annotated[
        str | None,
        Query(pattern=PADRAO_MES, description="AAAA-MM. Sem valor: último mês com dados."),
    ] = None,
) -> ScreenPayload:
    conn = ctx.sessao.conn
    ref = _mes_referencia(conn, ctx.empresa_id, mes)

    por_descritivo: list[tuple[int, Decimal]] = []
    serie: list[tuple[date, Decimal]] = []
    if ref is not None:
        por_descritivo = [
            (r["id_descritivo"], r["valor"])
            for r in conn.execute(
                """
                select id_descritivo, sum(valor) as valor
                  from mart.lancamento
                 where empresa_id = %s and mes = %s
                 group by id_descritivo
                 order by id_descritivo
                """,
                (ctx.empresa_id, ref),
            )
        ]
        serie = [
            (r["mes"], r["valor"])
            for r in conn.execute(
                """
                select mes, sum(valor) as valor
                  from mart.lancamento
                 where empresa_id = %s
                   and mes > (%s::date - interval '12 months')
                   and mes <= %s::date
                 group by mes
                 order by mes
                """,
                (ctx.empresa_id, ref, ref),
            )
        ]

    total = sum((v for _, v in por_descritivo), Decimal(0)) if por_descritivo else None

    return ScreenPayload(
        codigo_tela=CODIGO_TELA,
        titulo="Resultado do mês",
        empresa_id=ctx.empresa_id,
        mes=ref,
        atualizado_em=atualizado_em(conn, ctx.cliente_id),
        # TODO(regra): status do farol quando houver faixa definida para a tela.
        hero=Hero(rotulo="Resultado do mês", valor=total, formato="moeda"),
        apoios=[
            Apoio(rotulo=f"Descritivo {id_descritivo}", valor=valor, formato="moeda")
            for id_descritivo, valor in por_descritivo
        ],
        visuais=[
            Visual(
                tipo="linha",
                titulo="Últimos 12 meses",
                pontos=[Ponto(rotulo=m.strftime("%Y-%m"), valor=v) for m, v in serie],
            )
        ],
        alertas=[
            Alerta(
                trechos=[
                    Trecho(texto="Tela de exemplo: "),
                    Trecho(texto="sem regra de negócio", destaque=True),
                    Trecho(texto=" — os valores são a soma direta do mart."),
                ]
            )
        ],
    )

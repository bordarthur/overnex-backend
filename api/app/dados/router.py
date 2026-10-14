"""GET /api/dados/{tabela}: linhas de uma tabela de dados do mart, para o front.

Cada tabela de dados tem as mesmas colunas do Parquet do DW (ver
supabase/migrations/*_tabelas_de_dados.sql). A API devolve as colunas como estão,
paginadas por _linha, e o RLS garante que só saem linhas do cliente e das empresas do
usuário. Não aceita SQL nem nome de coluna da requisição.
"""

import base64
from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from psycopg import sql
from pydantic import BaseModel, ConfigDict

from app.core.db import Conexao
from app.seguranca.acesso import ContextoTabela, exigir_tabela

LIMITE_PADRAO = 1000
LIMITE_MAXIMO = 5000

router = APIRouter(prefix="/api/dados", tags=["dados"])

Celula = str | float | int | bool | None


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Coluna(_Modelo):
    nome: str
    # tipo no Postgres: text, numeric, date, bigint, uuid…
    tipo: str


class DadosPayload(_Modelo):
    tabela: str
    empresa_id: UUID | None
    # data_snapshot da última carga 'ok' desta tabela para o cliente; None se nunca houve
    atualizado_em: date | None
    colunas: list[Coluna]
    # uma lista por linha, na ordem de `colunas`
    linhas: list[list[Celula]]
    # passe em `apos` para a próxima página; None = acabou
    proximo: int | None


def _celula(valor: object) -> Celula:
    if valor is None or isinstance(valor, str | bool | int | float):
        return valor
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, date | datetime | time):
        return valor.isoformat()
    if isinstance(valor, UUID):
        return str(valor)
    if isinstance(valor, bytes | memoryview):
        return base64.b64encode(bytes(valor)).decode()
    return str(valor)


def _colunas(conn: Conexao, tabela: str) -> list[Coluna]:
    return [
        Coluna(nome=r["column_name"], tipo=r["data_type"])
        for r in conn.execute(
            """
            select column_name, data_type
              from information_schema.columns
             where table_schema = 'mart' and table_name = %s and column_name <> '_cliente_id'
             order by ordinal_position
            """,
            (tabela,),
        ).fetchall()
    ]


def _atualizado_em(conn: Conexao, cliente_id: UUID, tabela: str) -> date | None:
    linha = conn.execute(
        """
        select cf.data_snapshot
          from mart.carga_fonte cf
          join cfg.fonte_carga f on f.id = cf.fonte_id
         where cf.cliente_id = %s
           and cf.status = 'ok'
           and f.tipo = 'tabela'
           and f.tabela_destino = %s
         order by cf.inicio desc, cf.id desc
         limit 1
        """,
        (cliente_id, tabela),
    ).fetchone()
    return linha["data_snapshot"] if linha else None


@router.get(
    "/{tabela}",
    responses={
        401: {"description": "Não autenticado."},
        403: {"description": "Acesso negado."},
        404: {"description": "Tabela registrada, mas ainda não criada no banco."},
    },
)
def ler_tabela(
    ctx: Annotated[ContextoTabela, Depends(exigir_tabela)],
    apos: Annotated[
        int, Query(ge=0, description="Devolve as linhas com _linha maior que este valor.")
    ] = 0,
    limite: Annotated[int, Query(ge=1, le=LIMITE_MAXIMO)] = LIMITE_PADRAO,
) -> DadosPayload:
    conn = ctx.sessao.conn
    colunas = _colunas(conn, ctx.tabela)
    if not colunas:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tabela sem dados.")

    filtro_empresa = sql.SQL("and _empresa_id = %(empresa)s" if ctx.empresa_id else "")
    consulta = sql.SQL(
        "select {colunas} from {tabela} "
        "where _cliente_id = %(cliente)s and _linha > %(apos)s {filtro} "
        "order by _linha limit %(limite)s"
    ).format(
        colunas=sql.SQL(", ").join(sql.Identifier(c.nome) for c in colunas),
        tabela=sql.Identifier("mart", ctx.tabela),
        filtro=filtro_empresa,
    )
    resultado = conn.execute(
        consulta,
        {
            "cliente": ctx.cliente_id,
            "empresa": ctx.empresa_id,
            "apos": apos,
            # uma a mais para saber se há próxima página
            "limite": limite + 1,
        },
    ).fetchall()

    tem_mais = len(resultado) > limite
    pagina = resultado[:limite]
    return DadosPayload(
        tabela=ctx.tabela,
        empresa_id=ctx.empresa_id,
        atualizado_em=_atualizado_em(conn, ctx.cliente_id, ctx.tabela),
        colunas=colunas,
        linhas=[[_celula(linha[c.nome]) for c in colunas] for linha in pagina],
        proximo=pagina[-1]["_linha"] if tem_mais else None,
    )

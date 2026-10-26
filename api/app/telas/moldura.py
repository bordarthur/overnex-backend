"""Partes comuns à sessão e às telas no formato do front (camelCase): cliente, última
carga e meses com dados."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from app.core.cache import existe
from app.core.db import Conexao
from app.telas.calendario import ultimo_dia_do_mes

# meses no filtro "Período"
MESES_NA_LISTA = 24

MESES = (
    "Janeiro",
    "Fevereiro",
    "Março",
    "Abril",
    "Maio",
    "Junho",
    "Julho",
    "Agosto",
    "Setembro",
    "Outubro",
    "Novembro",
    "Dezembro",
)


class ModeloCamel(BaseModel):
    """Campos em snake_case no Python e camelCase no JSON, como no contrato do front.

    As rotas usam `response_model_exclude_defaults=True`: campo opcional (com padrão)
    sem valor sai do JSON, como o `campo?:` do TypeScript; campo obrigatório que aceita
    None sai como null.
    """

    model_config = ConfigDict(
        extra="forbid", frozen=True, alias_generator=to_camel, populate_by_name=True
    )


class Cliente(ModeloCamel):
    nome: str
    sigla: str
    # só caminho do próprio domínio: a CSP do front recusa imagem de fora
    logo_url: str | None


@dataclass(frozen=True)
class UltimaCarga:
    data_snapshot: date
    fim: datetime | None


def sigla(nome: str) -> str:
    """Iniciais da primeira e da última palavra: "Cliente Demo" → "CD"."""
    palavras = nome.split()
    if not palavras:
        return ""
    if len(palavras) == 1:
        return palavras[0][:2].upper()
    return (palavras[0][0] + palavras[-1][0]).upper()


def rotulo_mes(mes: date) -> str:
    """Rótulo do mês, ex.: Outubro de 2026."""
    return f"{MESES[mes.month - 1]} de {mes.year}"


SQL_CLIENTE = "select nome, logo_url from cfg.cliente where id = %s"


def cliente(conn: Conexao, cliente_id: UUID) -> Cliente:
    return cliente_da_linha(conn.execute(SQL_CLIENTE, (cliente_id,)).fetchone())


def cliente_da_linha(linha: Mapping[str, Any] | None) -> Cliente:
    if linha is None:  # o vínculo já garantiu o cliente; RLS não deixaria faltar
        raise LookupError("cliente do vínculo não encontrado")
    logo: str | None = linha["logo_url"]
    return Cliente(
        nome=linha["nome"],
        sigla=sigla(linha["nome"]),
        logo_url=logo if logo and logo.startswith("/") and not logo.startswith("//") else None,
    )


SQL_ULTIMA_CARGA = """
    select cf.data_snapshot, cf.fim
      from mart.carga_fonte cf
     where cf.cliente_id = %s
       and cf.status = 'ok'
     order by cf.inicio desc, cf.id desc
     limit 1
"""


def ultima_carga(conn: Conexao, cliente_id: UUID) -> UltimaCarga | None:
    """Última fonte com carga 'ok' do cliente, de qualquer tipo; None se nunca houve."""
    return carga_da_linha(conn.execute(SQL_ULTIMA_CARGA, (cliente_id,)).fetchone())


def carga_da_linha(linha: Mapping[str, Any] | None) -> UltimaCarga | None:
    if linha is None:
        return None
    return UltimaCarga(data_snapshot=linha["data_snapshot"], fim=linha["fim"])


@dataclass(frozen=True)
class MesComDados:
    # primeiro dia do mês
    mes: date
    # no mês do snapshot, a data do snapshot; nos outros, o último dia do mês
    dados_ate: date


def meses_disponiveis(meses: Iterable[date], data_snapshot: date | None) -> list[MesComDados]:
    """Os `meses` e o mês do snapshot, do mais recente para o mais antigo, até
    MESES_NA_LISTA; nenhum depois do mês do snapshot."""
    todos = {m.replace(day=1) for m in meses}
    snapshot = data_snapshot.replace(day=1) if data_snapshot else None
    if snapshot is not None:
        todos = {m for m in todos if m <= snapshot} | {snapshot}
    return [
        MesComDados(
            mes=m,
            dados_ate=data_snapshot if data_snapshot and m == snapshot else ultimo_dia_do_mes(m),
        )
        for m in sorted(todos, reverse=True)[:MESES_NA_LISTA]
    ]


def meses_com_dados(
    conn: Conexao, cliente_id: UUID, carga: UltimaCarga | None
) -> list[MesComDados]:
    """Meses com faturado no resumo mensal das tabelas f_vendas_* do cliente, mais o mês
    do último snapshot (filtro "Período" do front)."""
    meses: list[date] = []
    if existe(conn, "mart.resumo_mensal"):  # sem a migration 8, só o mês do snapshot
        meses = [r["mes"] for r in conn.execute(SQL_MESES, (cliente_id,))]
    return meses_disponiveis(meses, carga.data_snapshot if carga else None)


SQL_MESES = """
    select distinct mes
      from mart.resumo_mensal
     where cliente_id = %s and medida = 'faturado'
       and starts_with(tabela, 'f_vendas_')
"""

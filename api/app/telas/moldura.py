"""Partes comuns à sessão e às telas no formato do front (camelCase): cliente e última carga."""

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from app.core.db import Conexao

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


def cliente(conn: Conexao, cliente_id: UUID) -> Cliente:
    linha = conn.execute(
        "select nome, logo_url from cfg.cliente where id = %s", (cliente_id,)
    ).fetchone()
    if linha is None:  # o vínculo já garantiu o cliente; RLS não deixaria faltar
        raise LookupError("cliente do vínculo não encontrado")
    logo: str | None = linha["logo_url"]
    return Cliente(
        nome=linha["nome"],
        sigla=sigla(linha["nome"]),
        logo_url=logo if logo and logo.startswith("/") and not logo.startswith("//") else None,
    )


def ultima_carga(conn: Conexao, cliente_id: UUID) -> UltimaCarga | None:
    """Última fonte com carga 'ok' do cliente, de qualquer tipo; None se nunca houve."""
    linha = conn.execute(
        """
        select cf.data_snapshot, cf.fim
          from mart.carga_fonte cf
         where cf.cliente_id = %s
           and cf.status = 'ok'
         order by cf.inicio desc, cf.id desc
         limit 1
        """,
        (cliente_id,),
    ).fetchone()
    if linha is None:
        return None
    return UltimaCarga(data_snapshot=linha["data_snapshot"], fim=linha["fim"])

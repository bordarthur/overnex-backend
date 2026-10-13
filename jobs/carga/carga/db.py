"""Conexão do role carga."""

import psycopg
from psycopg import Connection
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import TupleRow

from carga.config import Settings
from carga.erros import ErroCarga

Conexao = Connection[TupleRow]

# Pooler do Supabase em modo transação: quebra o COPY e a transação longa da carga.
PORTA_POOLER_TRANSACAO = "6543"


def conectar(settings: Settings) -> Conexao:
    """autocommit=True: cada escrita de controle (mart.carga*) é gravada na hora; a troca
    dos dados de cada fonte roda num `with conn.transaction()` explícito."""
    url = settings.database_url_carga.get_secret_value()
    if str(conninfo_to_dict(url).get("port", "")) == PORTA_POOLER_TRANSACAO:
        raise ErroCarga(
            "DATABASE_URL_CARGA aponta para o pooler em modo transação (6543); "
            "use o pooler em modo sessão (porta 5432)"
        )
    return psycopg.connect(url, autocommit=True, application_name="overnex-carga")

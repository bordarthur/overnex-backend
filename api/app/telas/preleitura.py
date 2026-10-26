"""O começo de toda tela, numa ida ao banco só.

Depois da checagem de acesso (que define app.usuario_id na transação), toda tela precisa
da última carga, do cliente, das fontes, dos feriados e dos parâmetros do cliente: cinco
consultas pequenas, que custavam cinco idas e voltas (~100 ms cada). Aqui elas vão juntas
(app.core.db.em_uma_ida), e fontes, feriados e parâmetros ficam no cache da requisição
(app.core.cache.memo) com as mesmas chaves que as seções usam: ninguém lê de novo.

Chamar dentro de `por_requisicao()`.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.core.cache import existe, memo
from app.core.db import Conexao, em_uma_ida
from app.farol.parametro import SQL_PARAMETROS, parametros_das_linhas
from app.telas.calendario import SQL_FERIADOS
from app.telas.moldura import (
    SQL_CLIENTE,
    SQL_MESES,
    SQL_ULTIMA_CARGA,
    Cliente,
    MesComDados,
    UltimaCarga,
    carga_da_linha,
    cliente_da_linha,
    meses_disponiveis,
)
from app.telas.visao_geral.vendas import SQL_FONTES


@dataclass(frozen=True)
class Inicio:
    carga: UltimaCarga | None
    cliente: Cliente
    # meses do filtro "Período" (como em /api/sessao); None se não foram pedidos
    meses: list[MesComDados] | None = None


def preler(conn: Conexao, cliente_id: UUID, *, com_meses: bool = False) -> Inicio:
    """Última carga, cliente, fontes, feriados e parâmetros (e, com `com_meses`, os meses
    com dados) numa ida só. Sem a tabela de uma migration, a consulta dela fica de fora,
    como nas leituras uma a uma."""
    p = (cliente_id,)
    consultas: dict[str, tuple[Any, Any]] = {
        "carga": (SQL_ULTIMA_CARGA, p),
        "cliente": (SQL_CLIENTE, p),
        "fontes": (SQL_FONTES, p),
    }
    # existe() guarda o "existe" em memória: só custa uma ida na primeira vez do processo
    if existe(conn, "cfg.feriado"):
        consultas["feriados"] = (SQL_FERIADOS, p)
    if existe(conn, "cfg.parametro_cliente"):
        consultas["parametros"] = (SQL_PARAMETROS, p)
    if com_meses and existe(conn, "mart.resumo_mensal"):
        consultas["meses"] = (SQL_MESES, p)
    linhas = em_uma_ida(conn, consultas)

    fontes = linhas["fontes"]
    memo(("fontes", cliente_id), lambda: fontes)
    feriados = {r["data"] for r in linhas.get("feriados", [])}
    memo(("feriados", cliente_id), lambda: feriados)
    parametros = parametros_das_linhas(linhas.get("parametros", []))
    memo(("parametros", cliente_id), lambda: parametros)

    carga = carga_da_linha(linhas["carga"][0] if linhas["carga"] else None)
    meses = None
    if com_meses:
        meses = meses_disponiveis(
            [r["mes"] for r in linhas.get("meses", [])], carga.data_snapshot if carga else None
        )
    return Inicio(
        carga=carga,
        cliente=cliente_da_linha(linhas["cliente"][0] if linhas["cliente"] else None),
        meses=meses,
    )

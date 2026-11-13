"""O começo de toda tela, numa ida ao banco só.

Depois da checagem de acesso (que define app.usuario_id na transação), toda tela precisa
da última carga, do cliente, das fontes, dos feriados, dos parâmetros do cliente, do plano
de contas e do saldo inicial de caixa: consultas pequenas, que custavam uma ida e volta
cada (~100 ms). Aqui elas vão juntas (app.core.db.em_uma_ida), e fontes, feriados,
parâmetros, plano e saldo ficam no cache da requisição (app.core.cache.memo) com as mesmas
chaves que as seções usam: ninguém lê de novo.

As mesmas linhas, mais a assinatura do de-para da DRE, dão a versão do cadastro
(`Inicio.versao`), que entra na chave do cache das telas: um parâmetro, feriado, fonte,
de-para ou saldo inicial mudado no banco vale na hora, sem esperar o prazo.

Nas rotas das telas, nem essa ida existe: a dependência `exigir_tela_com_inicio` manda as
mesmas consultas junto com a checagem de acesso (`consultas_adiantadas`), e `preler` só
monta o `Inicio` com as linhas que já vieram. O cliente ainda não é conhecido quando elas
saem: usam o cliente escolhido da transação (`seg.cliente_atual()`), que
seg.contexto_acesso define antes delas, na mesma ida.

Chamar `preler` dentro de `por_requisicao()`.
"""

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Request

from app.core.cache import existe, memo
from app.core.db import Conexao, Consulta, Sessao, em_uma_ida, get_sessao
from app.farol.parametro import SQL_PARAMETROS, parametros_das_linhas
from app.seguranca.acesso import (
    ClienteDaQuery,
    ContextoConsolidado,
    EmpresaDaQuery,
    verificar_acesso_consolidado,
)
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
from app.telas.plano import SQL_PLANO, plano_das_linhas
from app.telas.saldo import SQL_SALDO_INICIAL
from app.telas.visao_geral.vendas import SQL_FONTES


@dataclass(frozen=True)
class Inicio:
    carga: UltimaCarga | None
    cliente: Cliente
    # meses do filtro "Período" (como em /api/sessao); None se não foram pedidos
    meses: list[MesComDados] | None = None
    # assinatura do cadastro do cliente (ver versao_do_cadastro); entra na chave do cache
    versao: str = ""


# o que o cliente cadastra e a tela usa: mudou, a resposta pronta não vale mais
CADASTRO = (
    "cliente",
    "fontes",
    "feriados",
    "parametros",
    "plano",
    "conta_dre",
    "saldo_inicial",
)
# de-para da DRE (cfg.conta_dre): a assinatura vem pronta do banco, sem trazer as linhas
SQL_VERSAO_CONTA_DRE = """
    select md5(coalesce(string_agg(
               conta || '|' || coalesce(id_descritivo, '') || '|' || coalesce(fora_da_dre, ''),
               ',' order by conta), '')) as versao
      from cfg.conta_dre
     where cliente_id = %s
"""


def versao_do_cadastro(linhas: Mapping[str, Sequence[Mapping[str, Any]]]) -> str:
    """Assinatura das linhas de cadastro lidas (CADASTRO): muda quando um parâmetro, um
    feriado, uma fonte, o plano, o de-para da DRE, o saldo inicial ou o cliente mudam no
    banco."""
    cadastro = {nome: [dict(r) for r in linhas.get(nome, [])] for nome in CADASTRO}
    texto = json.dumps(cadastro, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.blake2b(texto.encode(), digest_size=16).hexdigest()


# o cliente escolhido da transação, no lugar do parâmetro (migration 26)
CLIENTE_ATUAL = "(select seg.cliente_atual())"
# com `com_meses` e sem o resumo mensal: nenhum mês (a chave "meses" diz que foram pedidos)
SQL_SEM_MESES = "select null::date as mes where false"


def _consultas(conn: Conexao, cliente_id: UUID | None, com_meses: bool) -> dict[str, Consulta]:
    """As consultas do começo da tela. `cliente_id` None: as do cliente escolhido da
    transação (para ir junto com a checagem de acesso, antes de saber quem é). Sem a tabela
    de uma migration, a consulta dela fica de fora, como nas leituras uma a uma."""

    def do_cliente(consulta: str) -> Consulta:
        if cliente_id is None:
            return consulta.replace("%s", CLIENTE_ATUAL), None
        return consulta, (cliente_id,)

    consultas: dict[str, Consulta] = {
        "carga": do_cliente(SQL_ULTIMA_CARGA),
        "cliente": do_cliente(SQL_CLIENTE),
        "fontes": do_cliente(SQL_FONTES),
    }
    # existe() guarda o "existe" em memória: só custa uma ida na primeira vez do processo
    if existe(conn, "cfg.feriado"):
        consultas["feriados"] = do_cliente(SQL_FERIADOS)
    if existe(conn, "cfg.parametro_cliente"):
        consultas["parametros"] = do_cliente(SQL_PARAMETROS)
    if com_meses:
        tem_resumo = existe(conn, "mart.resumo_mensal")
        consultas["meses"] = do_cliente(SQL_MESES) if tem_resumo else (SQL_SEM_MESES, None)
    if existe(conn, "cfg.plano_conta"):
        consultas["plano"] = (SQL_PLANO, None)
    if existe(conn, "cfg.conta_dre"):
        consultas["conta_dre"] = do_cliente(SQL_VERSAO_CONTA_DRE)
    if existe(conn, "cfg.saldo_inicial"):
        consultas["saldo_inicial"] = do_cliente(SQL_SALDO_INICIAL)
    return consultas


def consultas_adiantadas(conn: Conexao, *, com_meses: bool = False) -> dict[str, Consulta]:
    """As consultas de `preler` para ir junto com a checagem de acesso (`junto` de
    verificar_acesso_consolidado): com elas, `preler` não vai ao banco."""
    return _consultas(conn, None, com_meses)


def exigir_tela_com_inicio(
    codigo_tela: str, *, exige_dado_nominal: bool = False
) -> Callable[..., ContextoConsolidado]:
    """Como `exigir_tela_consolidada`, com a pré-leitura na mesma ida ao banco da checagem
    (os meses também, se a requisição traz `mes`)."""

    def dependencia(
        request: Request,
        sessao: Annotated[Sessao, Depends(get_sessao)],
        empresa: EmpresaDaQuery = None,
        cliente: ClienteDaQuery = None,
    ) -> ContextoConsolidado:
        com_meses = request.query_params.get("mes") is not None
        return verificar_acesso_consolidado(
            sessao,
            codigo_tela,
            empresa,
            exige_dado_nominal=exige_dado_nominal,
            cliente=cliente,
            junto=consultas_adiantadas(sessao.conn, com_meses=com_meses),
        )

    return dependencia


def preler(sessao: Sessao, cliente_id: UUID, *, com_meses: bool = False) -> Inicio:
    """Última carga, cliente, fontes, feriados e parâmetros (e, com `com_meses`, os meses
    com dados): as linhas que vieram com a checagem de acesso (`sessao.adiantadas`) ou,
    sem elas, numa ida ao banco."""
    linhas: Mapping[str, list[Any]] = sessao.adiantadas
    if "carga" not in linhas or (com_meses and "meses" not in linhas):
        linhas = em_uma_ida(sessao.conn, _consultas(sessao.conn, cliente_id, com_meses))
    if "plano" in linhas:
        plano = plano_das_linhas(linhas["plano"])
        memo(("plano",), lambda: plano)

    fontes = linhas["fontes"]
    memo(("fontes", cliente_id), lambda: fontes)
    feriados = {r["data"] for r in linhas.get("feriados", [])}
    memo(("feriados", cliente_id), lambda: feriados)
    parametros = parametros_das_linhas(linhas.get("parametros", []))
    memo(("parametros", cliente_id), lambda: parametros)
    saldos = [(r["data"], r["saldo"]) for r in linhas.get("saldo_inicial", [])]
    memo(("saldo_inicial", cliente_id), lambda: saldos)

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
        versao=versao_do_cadastro(linhas),
    )

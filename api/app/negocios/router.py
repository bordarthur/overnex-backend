"""GET /api/negocios: os valores da bandeja "Negócios" (setor › grupo › unidade).

O front pede quando a bandeja abre, com o mesmo `cliente` da sessão e o `mes` da tela. O
valor é o faturado do mês (vendas, do resumo mensal da carga: as tabelas f_vendas_*), em
reais, do setor, do grupo e da unidade **inteiros**, sem olhar o que está marcado. Só as
unidades liberadas à pessoa (o RLS de cfg.empresa e de mart.resumo_mensal recorta).

- sem `mes`: o mês mais recente da lista de períodos de /api/sessao (o da última carga);
  `mes` fora da lista: 422 "Mês sem dados.";
- `rotulo`: "Valores: faturado de 1º a 12/10" no mês da última carga, "Valores: faturado em
  setembro de 2026" num mês fechado;
- cliente com um negócio só (`negocios` null na sessão), sem cliente escolhido ou sem
  faturado por unidade no mês: 200 com os mapas vazios (a bandeja fica só com as contagens);
- o faturado sem unidade (tabela sem coluna de empresa) não entra: não é de nenhum negócio;
- erros de acesso: os da sessão (403 para o cliente que a pessoa não abre).

Tudo numa ida ao banco, depois da checagem.
"""

from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.core.cache import existe
from app.core.db import Consulta, em_uma_ida
from app.negocios.arvore import (
    MSG_SEM_VALORES,
    SQL_SETORES,
    SQL_UNIDADES,
    ordenar,
    rotulo_valores,
    setores_negocio,
    totais,
    um_negocio_so,
    unidade_da_linha,
)
from app.negocios.contrato import NegociosValores
from app.seguranca.acesso import ContextoSessao, exigir_sessao
from app.telas.moldura import SQL_MESES, SQL_ULTIMA_CARGA, carga_da_linha, meses_disponiveis
from app.telas.visao_geral.contrato import Valor
from app.telas.visao_geral.router import FORMATO_MES, mes_escolhido

router = APIRouter(prefix="/api/negocios", tags=["negocios"])

# faturado por mês e unidade (o histórico do resumo é pequeno: meses × unidades)
SQL_FATURADO = """
    select r.mes, r.empresa_id, sum(r.valor) as valor
      from mart.resumo_mensal r
     where r.cliente_id = %s
       and r.medida = 'faturado'
       and starts_with(r.tabela, 'f_vendas_')
       and r.empresa_id is not null
     group by r.mes, r.empresa_id
"""


def _vazio() -> NegociosValores:
    return NegociosValores(rotulo=MSG_SEM_VALORES, setores={}, grupos={}, unidades={})


def _brl(valor: Decimal) -> Valor:
    return Valor(valor=valor, unidade="brl")


@router.get(
    "",
    response_model_exclude_defaults=True,
    responses={
        401: {"description": "Não autenticado."},
        403: {"description": "Acesso negado (inclusive `cliente` que a pessoa não pode abrir)."},
        422: {"description": "`mes` fora do formato AAAA-MM, ou mês sem dados."},
    },
)
def valores(
    ctx: Annotated[ContextoSessao, Depends(exigir_sessao)],
    mes: Annotated[
        str | None,
        Query(pattern=FORMATO_MES, description="Mês (AAAA-MM), um dos de /api/sessao."),
    ] = None,
) -> NegociosValores:
    if ctx.cliente_id is None:
        return _vazio()
    conn = ctx.sessao.conn
    c = (ctx.cliente_id,)
    consultas: dict[str, Consulta] = {
        "unidades": (SQL_UNIDADES, c),
        "setores": (SQL_SETORES, None),
        "carga": (SQL_ULTIMA_CARGA, c),
    }
    if existe(conn, "mart.resumo_mensal"):  # sem a migration 8, nenhum valor
        consultas["meses"] = (SQL_MESES, c)
        consultas["faturado"] = (SQL_FATURADO, c)
    linhas: dict[str, list[Any]] = em_uma_ida(conn, consultas)

    carga = carga_da_linha(linhas["carga"][0] if linhas["carga"] else None)
    dados_ate = carga.data_snapshot if carga else None
    disponiveis = meses_disponiveis([r["mes"] for r in linhas.get("meses", [])], dados_ate)
    if mes is not None:
        escolhido = mes_escolhido(mes, disponiveis)
    elif disponiveis:
        escolhido = disponiveis[0].mes
    else:
        return _vazio()

    rotulos = {r["id"]: r["rotulo"] for r in linhas["setores"]}
    ordem = {r["id"]: i for i, r in enumerate(linhas["setores"])}
    unidades = ordenar((unidade_da_linha(r) for r in linhas["unidades"]), ordem)
    if um_negocio_so(unidades):
        return _vazio()
    por_unidade = {
        r["empresa_id"]: r["valor"] for r in linhas.get("faturado", []) if r["mes"] == escolhido
    }
    if not por_unidade:
        return _vazio()

    t = totais(setores_negocio(unidades, rotulos), por_unidade)
    return NegociosValores(
        rotulo=rotulo_valores(escolhido, dados_ate),
        setores={k: _brl(v) for k, v in t.setores.items()},
        grupos={k: _brl(v) for k, v in t.grupos.items()},
        unidades={k: _brl(v) for k, v in t.unidades.items()},
    )

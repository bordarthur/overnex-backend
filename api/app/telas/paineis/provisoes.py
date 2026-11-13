"""Provisões pelo histórico do Fluxo de caixa (FIN-04): os grupos, o período da média e as
escolhas salvas. REGRAS APROVADAS pelo usuário em 09/11/2026 (ver TODO.md, "Projeção de
caixa").

Grupos pelo plano de contas padrão da LANN (Id_Descritivo; no realizado, pela conta da
linha; nos títulos a pagar, pela conta do ERP no de-para, cfg.conta_dre):
- recebimentos: as entradas de FCO (conta FCO com fator +1); os títulos a receber, todos;
- saídas, pelo prefixo do Id_Descritivo (PREFIXOS_SAIDA): fornecedores (120), folha
  (140.01), impostos (110.03, impostos sobre a venda e retidos, e 200), despesas fixas (140,
  menos 140.01), despesas variáveis (130) e empréstimos e juros (170 e 400); investimentos:
  as saídas de FCI (420). Começa desligado: não se repete.
Ficam sem grupo, sem provisão: 110.01 (descontos), 110.02 (devoluções), 150 (depreciação),
160 (receitas financeiras), 180 (despesas não operacionais), 190 (receitas não
operacionais), 210 (participações dos sócios), 410 (empréstimos tomados) e o "Sem
classificação".

Escolhas (quem tem a FIN-04 muda, e vale para todos os usuários do cliente): em
cfg.parametro_cliente, como as preferências da Visão geral (migration 19: a API só grava
chaves `preferencia.*` do próprio cliente), com vigente_de = dia da escolha e a mais recente
valendo. Por cliente e empresa (`todas`, sem filtro de empresa):
- `preferencia.provisao.<empresa>.periodo`: "6-meses";
- `preferencia.provisao.<empresa>.grupo.<id>`: {"ligado": true, "valor": 800000 ou null,
  "por": "Ana" ou null, "em": "2026-11-05" ou null}. `por` e `em` são de quem digitou o
  valor (o nome vem de seg.usuario na gravação); ligar ou desligar sem mudar o valor não
  troca o autor.
Cada troca vai também para seg.log_alteracao.
"""

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from app.core.db import Conexao
from app.farol.parametro import Parametro, vigente
from app.telas.plano import Conta
from app.telas.visao_geral.preferencias import PREFIXO

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GrupoProvisao:
    id: str
    nome: str
    # recebimentos (entra no caixa) ou saída
    entrada: bool
    # a linha em "De onde vem a projeção"
    origem: str
    ligado_padrao: bool = True
    # segunda linha na tabela: com o grupo ligado e desligado
    detalhe: str | None = None
    detalhe_desligado: str | None = None


GRUPOS: tuple[GrupoProvisao, ...] = (
    GrupoProvisao(
        "recebimentos",
        "Recebimentos de vendas ainda não faturadas",
        True,
        "Vendas ainda não faturadas, pela média recebida",
        detalhe="média recebida menos o que já está a receber",
        detalhe_desligado="média recebida menos o que já está a receber",
    ),
    GrupoProvisao(
        "fornecedores", "Fornecedores", False, "Fornecedores ainda não lançados, pela média"
    ),
    GrupoProvisao("folha", "Folha e encargos", False, "Folha e encargos, pela média"),
    GrupoProvisao("impostos", "Impostos", False, "Impostos, pela média"),
    GrupoProvisao("despesas-fixas", "Despesas fixas", False, "Despesas fixas, pela média"),
    GrupoProvisao(
        "despesas-variaveis", "Despesas variáveis", False, "Despesas variáveis, pela média"
    ),
    GrupoProvisao(
        "investimentos",
        "Investimentos",
        False,
        "Investimentos, pela média",
        ligado_padrao=False,
        detalhe="não recorrente",
        detalhe_desligado="não recorrente: desligado",
    ),
    GrupoProvisao(
        "emprestimos", "Empréstimos e juros", False, "Empréstimos e juros ainda não lançados"
    ),
)
POR_ID: Mapping[str, GrupoProvisao] = {g.id: g for g in GRUPOS}
RECEBIMENTOS = "recebimentos"
# o grupo da dívida: em "De onde vem a projeção", os títulos dele saem em linha própria
DIVIDA = "emprestimos"

# saídas: prefixo do Id_Descritivo → grupo, do mais específico ao mais geral
PREFIXOS_SAIDA: tuple[tuple[str, str], ...] = (
    ("140.01", "folha"),
    ("140", "despesas-fixas"),
    ("130", "despesas-variaveis"),
    ("120", "fornecedores"),
    ("110.03", "impostos"),
    ("200", "impostos"),
    ("170", "emprestimos"),
    ("400", "emprestimos"),
)

# período da média: id → (rótulo, meses fechados)
PERIODOS: Mapping[str, tuple[str, int]] = {
    "ultimo-mes": ("Último mês", 1),
    "3-meses": ("3 meses", 3),
    "6-meses": ("6 meses", 6),
    "12-meses": ("12 meses", 12),
}
PERIODO_PADRAO = "6-meses"


def grupo_da_conta(codigo: str | None, conta: Conta | None, entrada: bool) -> str | None:
    """O grupo de provisão de uma conta do plano (`codigo`, normalizado); None fora dos
    grupos. `entrada`: o caixa entra (no realizado, conta de fator +1)."""
    if entrada:
        return RECEBIMENTOS if conta is not None and conta.caixa == "FCO" else None
    if codigo is None:
        return None
    for prefixo, grupo in PREFIXOS_SAIDA:
        if codigo == prefixo or codigo.startswith(prefixo + "."):
            return grupo
    if conta is not None and conta.caixa == "FCI":
        return "investimentos"
    return None


# ---------- escolhas salvas ----------


@dataclass(frozen=True)
class EscolhaGrupo:
    ligado: bool
    # valor digitado para os próximos 30 dias, em reais; None: o sugerido
    valor: Decimal | None = None
    # quem digitou o valor, e quando
    por: str | None = None
    em: date | None = None


@dataclass(frozen=True)
class Escolhas:
    periodo: str = PERIODO_PADRAO
    grupos: Mapping[str, EscolhaGrupo] = field(default_factory=dict)

    @property
    def meses(self) -> int:
        return PERIODOS[self.periodo][1]

    def do_grupo(self, id_: str) -> EscolhaGrupo:
        salvo = self.grupos.get(id_)
        return salvo if salvo is not None else EscolhaGrupo(ligado=POR_ID[id_].ligado_padrao)


def escopo(empresa_id: UUID | None) -> str:
    return str(empresa_id) if empresa_id is not None else "todas"


def chave_periodo(empresa_id: UUID | None) -> str:
    return f"{PREFIXO}provisao.{escopo(empresa_id)}.periodo"


def chave_grupo(empresa_id: UUID | None, grupo: str) -> str:
    return f"{PREFIXO}provisao.{escopo(empresa_id)}.grupo.{grupo}"


def _decimal(valor: Any) -> Decimal | None:
    if valor is None or isinstance(valor, bool):
        return None
    try:
        d = Decimal(str(valor))
    except InvalidOperation:
        return None
    return d if d.is_finite() and d >= 0 else None


def _escolha_grupo(salvo: Any) -> EscolhaGrupo | None:
    if not isinstance(salvo, dict) or not isinstance(salvo.get("ligado"), bool):
        return None
    valor = _decimal(salvo.get("valor"))
    em: date | None = None
    if valor is not None and isinstance(salvo.get("em"), str):
        try:
            em = date.fromisoformat(salvo["em"])
        except ValueError:
            em = None
    por = salvo.get("por") if valor is not None and isinstance(salvo.get("por"), str) else None
    return EscolhaGrupo(ligado=salvo["ligado"], valor=valor, por=por, em=em)


def ler_escolhas(parametros: list[Parametro], empresa_id: UUID | None, hoje: date) -> Escolhas:
    """As escolhas salvas do cliente para a empresa (ou para todas); salvo inválido vale o
    padrão (e vai para o log)."""
    periodo = vigente(parametros, chave_periodo(empresa_id), hoje)
    if periodo is not None and periodo not in PERIODOS:
        logger.warning("Período de provisão inválido: %r", periodo)
        periodo = None
    grupos: dict[str, EscolhaGrupo] = {}
    for g in GRUPOS:
        salvo = vigente(parametros, chave_grupo(empresa_id, g.id), hoje)
        if salvo is None:
            continue
        escolha = _escolha_grupo(salvo)
        if escolha is None:
            logger.warning("Provisão do grupo %s inválida: %r", g.id, salvo)
            continue
        grupos[g.id] = escolha
    return Escolhas(periodo=periodo or PERIODO_PADRAO, grupos=grupos)


# ---------- gravação ----------

# como app/telas/visao_geral/preferencias.py, com o nome de quem digitou o valor lido de
# seg.usuario (o api_site lê o próprio usuário) quando `com_nome`
SQL_GRAVAR = """
    with antes as (
        select valor, vigente_de = %(hoje)s as mesmo_dia
          from cfg.parametro_cliente
         where cliente_id = %(cliente)s and chave = %(chave)s
           and vigente_de <= %(hoje)s and (vigente_ate is null or vigente_ate >= %(hoje)s)
         order by vigente_de desc
         limit 1
    ),
    novo as (
        select case
                   when %(com_nome)s then
                       %(valor)s::jsonb || jsonb_build_object('por', (
                           select coalesce(nullif(trim(u.nome), ''), split_part(u.email, '@', 1))
                             from seg.usuario u
                            where u.id = %(usuario)s))
                   else %(valor)s::jsonb
               end as valor
    ),
    gravado as (
        insert into cfg.parametro_cliente (cliente_id, chave, valor, vigente_de)
        select %(cliente)s, %(chave)s, novo.valor, %(hoje)s from novo
        on conflict (cliente_id, chave, vigente_de) do update set valor = excluded.valor
        returning valor
    )
    insert into seg.log_alteracao (usuario_id, tabela, registro_id, operacao, antes, depois)
    select %(usuario)s, 'cfg.parametro_cliente', %(registro)s,
           case when coalesce((select mesmo_dia from antes), false) then 'update'
                else 'insert' end,
           (select jsonb_build_object('valor', valor) from antes),
           jsonb_build_object('valor', g.valor)
      from gravado g
"""


def _gravar(
    conn: Conexao,
    cliente_id: UUID,
    usuario_id: UUID,
    chave: str,
    valor: object,
    hoje: date,
    com_nome: bool = False,
) -> None:
    conn.execute(
        SQL_GRAVAR,
        {
            "cliente": cliente_id,
            "chave": chave,
            "valor": json.dumps(valor),
            "com_nome": com_nome,
            "hoje": hoje,
            "usuario": usuario_id,
            "registro": f"{cliente_id}/{chave}/{hoje.isoformat()}",
        },
    )


def gravar_periodo(
    conn: Conexao,
    cliente_id: UUID,
    usuario_id: UUID,
    empresa_id: UUID | None,
    periodo: str,
    hoje: date,
) -> None:
    """Grava o período da média (numa ida ao banco, com o log). Não confirma."""
    _gravar(conn, cliente_id, usuario_id, chave_periodo(empresa_id), periodo, hoje)


def _numero_json(valor: Decimal) -> int | float:
    return int(valor) if valor == valor.to_integral_value() else float(valor)


def gravar_grupo(
    conn: Conexao,
    cliente_id: UUID,
    usuario_id: UUID,
    empresa_id: UUID | None,
    grupo: str,
    ligado: bool,
    valor: Decimal | None,
    anterior: EscolhaGrupo,
    hoje: date,
) -> None:
    """Grava a escolha do grupo (numa ida ao banco, com o log). Valor novo leva quem digitou
    e o dia; o mesmo valor de antes mantém o autor. Não confirma."""
    salvo: dict[str, Any] = {"ligado": ligado, "valor": None, "por": None, "em": None}
    com_nome = False
    if valor is not None:
        salvo["valor"] = _numero_json(valor)
        if anterior.valor == valor and anterior.por is not None:
            salvo["por"] = anterior.por
            salvo["em"] = anterior.em.isoformat() if anterior.em else None
        else:
            salvo["em"] = hoje.isoformat()
            del salvo["por"]
            com_nome = True
    _gravar(conn, cliente_id, usuario_id, chave_grupo(empresa_id, grupo), salvo, hoje, com_nome)

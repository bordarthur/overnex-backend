"""Escolhas da Visão geral que ficam salvas para o cliente (o seletor de uma seção).

A escolha vale para todos os usuários do cliente. Fica em cfg.parametro_cliente, com chave
`preferencia.<id>` e vigente_de = dia da escolha: a mais recente vale, e as antigas ficam
como histórico (migration 19: a API só grava chaves `preferencia.*` do próprio cliente).
Cada troca vai também para seg.log_alteracao.

Quem pode mudar: PAPEIS_QUE_MUDAM. REGRA PROVISÓRIA: confirmar com o negócio (ver TODO.md).
"""

import json
import logging
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from app.core.db import Conexao
from app.farol.parametro import parametros_do_cliente, vigente
from app.telas.visao_geral.contrato import OpcaoSeletor, SeletorSecao

logger = logging.getLogger(__name__)

PAPEIS_QUE_MUDAM: frozenset[str] = frozenset({"gestor", "admin"})
PREFIXO = "preferencia."

# Custos: quantas linhas de custo viram velocímetro
LINHAS_CUSTO = "linhas-custo"
LINHAS_CUSTO_AUTO = "auto"
LINHAS_CUSTO_MAXIMO = 7
# no automático: as linhas que somam este percentual do custo do mês
LINHAS_CUSTO_PARTICIPACAO = 80


@dataclass(frozen=True)
class Preferencia:
    id: str
    rotulo: str
    opcoes: tuple[tuple[str, str], ...]
    padrao: str
    nota: str

    @property
    def chave(self) -> str:
        return PREFIXO + self.id

    def aceita(self, valor: str) -> bool:
        return valor in {id_ for id_, _ in self.opcoes}

    def seletor(self, valor: str) -> SeletorSecao:
        return SeletorSecao(
            id=self.id,
            rotulo=self.rotulo,
            opcoes=[OpcaoSeletor(id=id_, rotulo=rotulo) for id_, rotulo in self.opcoes],
            valor=valor,
            nota=self.nota,
        )


PREFERENCIAS: dict[str, Preferencia] = {
    LINHAS_CUSTO: Preferencia(
        id=LINHAS_CUSTO,
        rotulo="Linhas de custo",
        opcoes=(
            (LINHAS_CUSTO_AUTO, f"Automático ({LINHAS_CUSTO_PARTICIPACAO}%)"),
            *((str(n), str(n)) for n in range(1, LINHAS_CUSTO_MAXIMO + 1)),
        ),
        padrao=LINHAS_CUSTO_AUTO,
        nota=(
            f"Automático: as linhas que somam {LINHAS_CUSTO_PARTICIPACAO}% do custo, no máximo "
            f"{LINHAS_CUSTO_MAXIMO}, e o resto em Outros. A escolha fica salva para o cliente."
        ),
    ),
}


def valor(conn: Conexao, cliente_id: UUID, id_: str) -> str:
    """A escolha salva do cliente; sem escolha, ou com uma que não vale mais, o padrão.

    Lê os parâmetros do cliente do cache da requisição (a pré-leitura já trouxe)."""
    p = PREFERENCIAS[id_]
    salvo = vigente(parametros_do_cliente(conn, cliente_id), p.chave, date.today())
    if salvo is None:
        return p.padrao
    if not isinstance(salvo, str) or not p.aceita(salvo):
        logger.warning("Preferência %s inválida (cliente %s): %r", p.chave, cliente_id, salvo)
        return p.padrao
    return salvo


def valores(conn: Conexao, cliente_id: UUID) -> tuple[tuple[str, str], ...]:
    """Todas as escolhas do cliente (id, valor): entram na chave do cache da tela."""
    return tuple((id_, valor(conn, cliente_id, id_)) for id_ in PREFERENCIAS)


SQL_GRAVAR = """
    with antes as (
        select valor, vigente_de = %(hoje)s as mesmo_dia
          from cfg.parametro_cliente
         where cliente_id = %(cliente)s and chave = %(chave)s
           and vigente_de <= %(hoje)s and (vigente_ate is null or vigente_ate >= %(hoje)s)
         order by vigente_de desc
         limit 1
    ),
    gravado as (
        insert into cfg.parametro_cliente (cliente_id, chave, valor, vigente_de)
        values (%(cliente)s, %(chave)s, %(valor)s::jsonb, %(hoje)s)
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


def gravar(
    conn: Conexao, cliente_id: UUID, usuario_id: UUID, p: Preferencia, escolha: str, hoje: date
) -> None:
    """Grava a escolha do dia (troca a de hoje, se já houver) e registra a alteração, numa
    ida ao banco. Não confirma a transação."""
    conn.execute(
        SQL_GRAVAR,
        {
            "cliente": cliente_id,
            "chave": p.chave,
            "valor": json.dumps(escolha),
            "hoje": hoje,
            "usuario": usuario_id,
            "registro": f"{cliente_id}/{p.chave}/{hoje.isoformat()}",
        },
    )

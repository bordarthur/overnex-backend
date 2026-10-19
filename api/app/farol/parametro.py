"""Faixas do farol de um indicador: cfg.parametro_cliente, com padrão no código.

Chave `farol.<indicador>`, valor jsonb com qualquer um destes campos (os ausentes vêm do
padrão): {"atencao": 100, "critico": 90, "maior_melhor": true, "min": 80, "max": 110,
"meta": 100}. Vale o parâmetro vigente na data de referência.

Parâmetro inválido não derruba a tela: registra no log e usa o padrão.
"""

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.db import Conexao
from app.farol.status import Faixa, status
from app.telas.contrato import Status

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Farol:
    faixa: Faixa
    # escala do velocímetro
    minimo: Decimal
    maximo: Decimal
    meta: Decimal

    def status(self, valor: Decimal) -> Status:
        s = status(valor, self.faixa)
        if s is None:  # status() só devolve None sem valor
            raise ValueError("valor sem status")
        return s

    def ponteiro(self, valor: Decimal) -> Decimal:
        """O valor dentro da escala: fora dela, o ponteiro fica no limite."""
        return min(max(valor, self.minimo), self.maximo)

    def faixas(self) -> list[tuple[Decimal, Decimal, Status]]:
        """(de, até, status) das faixas coloridas do velocímetro, da esquerda para a direita."""
        f = self.faixa
        if f.maior_melhor:
            trechos: list[tuple[Decimal, Decimal, Status]] = [
                (self.minimo, f.critico, "critico"),
                (f.critico, f.atencao, "atencao"),
                (f.atencao, self.maximo, "saudavel"),
            ]
        else:
            trechos = [
                (self.minimo, f.atencao, "saudavel"),
                (f.atencao, f.critico, "atencao"),
                (f.critico, self.maximo, "critico"),
            ]
        recortados = [(self.ponteiro(de), self.ponteiro(ate), s) for de, ate, s in trechos]
        return [(de, ate, s) for de, ate, s in recortados if ate > de]


class _Parametro(BaseModel):
    model_config = ConfigDict(extra="forbid")

    atencao: Decimal | None = None
    critico: Decimal | None = None
    maior_melhor: bool | None = None
    min: Decimal | None = None
    max: Decimal | None = None
    meta: Decimal | None = None


# PROVISÓRIOS: os mesmos limites do mockup, até o negócio definir os de cada cliente.
PADROES: dict[str, Farol] = {
    # atingimento da meta de vendas, em %
    "vendas_meta": Farol(
        faixa=Faixa(atencao=Decimal(100), critico=Decimal(90)),
        minimo=Decimal(80),
        maximo=Decimal(110),
        meta=Decimal(100),
    ),
}


def _existe_tabela(conn: Conexao) -> bool:
    linha = conn.execute("select to_regclass('cfg.parametro_cliente') is not null as ok").fetchone()
    return bool(linha and linha["ok"])


def farol(conn: Conexao, cliente_id: UUID, indicador: str, referencia: date) -> Farol:
    padrao = PADROES[indicador]
    if not _existe_tabela(conn):  # migration 8 ainda não aplicada
        return padrao
    linha = conn.execute(
        """
        select valor
          from cfg.parametro_cliente
         where cliente_id = %s
           and chave = %s
           and vigente_de <= %s
           and (vigente_ate is null or vigente_ate >= %s)
         order by vigente_de desc
         limit 1
        """,
        (cliente_id, f"farol.{indicador}", referencia, referencia),
    ).fetchone()
    if linha is None:
        return padrao
    try:
        p = _Parametro.model_validate(linha["valor"])
        return Farol(
            faixa=Faixa(
                atencao=p.atencao if p.atencao is not None else padrao.faixa.atencao,
                critico=p.critico if p.critico is not None else padrao.faixa.critico,
                maior_melhor=(
                    p.maior_melhor if p.maior_melhor is not None else padrao.faixa.maior_melhor
                ),
            ),
            minimo=p.min if p.min is not None else padrao.minimo,
            maximo=p.max if p.max is not None else padrao.maximo,
            meta=p.meta if p.meta is not None else padrao.meta,
        )
    except (ValidationError, ValueError) as exc:
        logger.warning("Parâmetro farol.%s inválido (cliente %s): %s", indicador, cliente_id, exc)
        return padrao

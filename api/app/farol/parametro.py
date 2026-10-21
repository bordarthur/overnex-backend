"""Faixas do farol de um indicador: cfg.parametro_cliente, com padrão no código.

Chave `farol.<indicador>`, valor jsonb com qualquer um destes campos (os ausentes vêm do
padrão): {"atencao": 100, "critico": 90, "maior_melhor": true, "min": 80, "max": 110,
"meta": 100, "referencia": 1120}. Vale o parâmetro vigente na data de referência.
`referencia` é a meta fixa do indicador na unidade dele (ex.: 1.120 clientes), para os
indicadores que, sem ela, comparam com a média dos meses anteriores.

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
    # referência fixa do indicador, na unidade dele (ex.: meta de 1.120 clientes).
    # None = o indicador usa a referência dele (ex.: média dos meses anteriores).
    referencia: Decimal | None = None

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
    referencia: Decimal | None = None


# PROVISÓRIOS: os mesmos limites do mockup, até o negócio definir os de cada cliente.
PADROES: dict[str, Farol] = {
    # atingimento da meta de vendas, em %
    "vendas_meta": Farol(
        faixa=Faixa(atencao=Decimal(100), critico=Decimal(90)),
        minimo=Decimal(80),
        maximo=Decimal(110),
        meta=Decimal(100),
    ),
    # os três abaixo: valor do mês em % da referência (média de 6 meses ou meta cadastrada)
    "clientes_compraram": Farol(
        faixa=Faixa(atencao=Decimal(100), critico=Decimal(90)),
        minimo=Decimal(80),
        maximo=Decimal(110),
        meta=Decimal(100),
    ),
    "ticket_pedido": Farol(
        faixa=Faixa(atencao=Decimal(100), critico=Decimal(95)),
        minimo=Decimal(85),
        maximo=Decimal(115),
        meta=Decimal(100),
    ),
    # CMV: limites RELATIVOS, em p.p. acima da referência do ramo (ver deslocado())
    "cmv": Farol(
        faixa=Faixa(atencao=Decimal("0.3"), critico=Decimal("1.5"), maior_melhor=False),
        minimo=Decimal(-4),
        maximo=Decimal(6),
        meta=Decimal(0),
    ),
    "produtos_cliente": Farol(
        faixa=Faixa(atencao=Decimal(100), critico=Decimal(90)),
        minimo=Decimal(70),
        maximo=Decimal(110),
        meta=Decimal(100),
    ),
    # Resultado: limites RELATIVOS, em p.p. da receita líquida contra o orçado (ou a média)
    "margem_ebitda": Farol(
        faixa=Faixa(atencao=Decimal("-0.5"), critico=Decimal("-2.5")),
        minimo=Decimal(-5),
        maximo=Decimal(2),
        meta=Decimal(0),
    ),
    "despesas_fixas": Farol(
        faixa=Faixa(atencao=Decimal(0), critico=Decimal("0.6"), maior_melhor=False),
        minimo=Decimal("-2.4"),
        maximo=Decimal("2.6"),
        meta=Decimal(0),
    ),
    "folha_receita": Farol(
        faixa=Faixa(atencao=Decimal("0.3"), critico=Decimal("1.1"), maior_melhor=False),
        minimo=Decimal("-1.9"),
        maximo=Decimal("2.1"),
        meta=Decimal(0),
    ),
    # Financeiro: vencido sobre a carteira, em p.p. acima da média de 12 meses (RELATIVO)
    "inadimplencia": Farol(
        faixa=Faixa(atencao=Decimal(0), critico=Decimal(2), maior_melhor=False),
        minimo=Decimal(-6),
        maximo=Decimal(4),
        meta=Decimal(0),
    ),
    # prazos médios: limites RELATIVOS, em dias de distância da média de 6 meses
    "prazo_recebimento": Farol(
        faixa=Faixa(atencao=Decimal(0), critico=Decimal(5), maior_melhor=False),
        minimo=Decimal(-13),
        maximo=Decimal(17),
        meta=Decimal(0),
    ),
    "prazo_pagamento": Farol(
        faixa=Faixa(atencao=Decimal(0), critico=Decimal(-6)),
        minimo=Decimal(-16),
        maximo=Decimal(14),
        meta=Decimal(0),
    ),
    # a pagar lançado nos próximos 30 dias, em % da média paga por mês
    "pagar_lancado": Farol(
        faixa=Faixa(atencao=Decimal(80), critico=Decimal(50)),
        minimo=Decimal(0),
        maximo=Decimal(120),
        meta=Decimal(100),
    ),
}


def deslocado(f: Farol, base: Decimal) -> Farol:
    """Farol de limites relativos (ex.: CMV em p.p. da referência) levado para a base.

    Com base 84 e o padrão do CMV: meta 84, atenção a partir de 84,3, crítico a partir
    de 85,5, escala de 80 a 90.
    """
    return Farol(
        faixa=Faixa(
            atencao=f.faixa.atencao + base,
            critico=f.faixa.critico + base,
            maior_melhor=f.faixa.maior_melhor,
        ),
        minimo=f.minimo + base,
        maximo=f.maximo + base,
        meta=f.meta + base,
        referencia=f.referencia,
    )


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
            referencia=p.referencia if p.referencia is not None else padrao.referencia,
        )
    except (ValidationError, ValueError) as exc:
        logger.warning("Parâmetro farol.%s inválido (cliente %s): %s", indicador, cliente_id, exc)
        return padrao

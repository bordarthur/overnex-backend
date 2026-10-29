"""Plano de contas gerencial padrão da LANN (cfg.plano_conta, migration 17).

Diz, para cada Id_Descritivo, em que demonstrativo a conta entra, com que fator e os nomes:
- DRE: competência CO ou CO/CA; fluxo de caixa: CA ou CO/CA. Conta fora do plano não entra;
- fator: +1 soma no resultado, −1 subtrai (as redutoras de custo têm +1). O realizado já
  vem com o sinal do lançamento (Tipo E/S, contas a pagar); o fator dá o sinal do orçado,
  que o DW grava sem sinal;
- nomes: nível 2 (o nome mais comum do id de nível 2) e nível 3 (descritivo).

Lido uma vez por requisição, na pré-leitura (app/telas/preleitura.py). Sem a migration 17
ou com a tabela vazia, o plano fica vazio e vale a regra antiga: toda conta de um grupo da
DRE entra, com o sinal do grupo.
"""

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

from app.core.cache import existe, memo
from app.core.db import Conexao

COMPETENCIA_DRE = frozenset({"CO", "CO/CA"})
COMPETENCIA_CAIXA = frozenset({"CA", "CO/CA"})

SQL_PLANO = """
    select id_descritivo, descritivo, id_nivel_2, nivel_2, fator, caixa, competencia
      from cfg.plano_conta
"""


def normalizar(texto: str | None) -> str | None:
    """Id_Descritivo como no plano: números separados por ponto ("110.03;14" → "110.03.14").
    Sem separador, 3 + 2 + o resto dos dígitos ("1400101" → "140.01.01")."""
    if not texto:
        return None
    partes = re.findall(r"\d+", texto)
    if not partes:
        return None
    if len(partes) == 1:
        d = partes[0]
        partes = [d[:3], d[3:5], d[5:]]
    return ".".join(p for p in partes if p)


@dataclass(frozen=True)
class Conta:
    id_descritivo: str
    descritivo: str
    id_nivel_2: str
    nivel_2: str
    fator: int
    # FCO, FCI ou FCF
    caixa: str | None
    # CO, CA ou CO/CA
    competencia: str | None


@dataclass(frozen=True)
class Plano:
    contas: Mapping[str, Conta] = field(default_factory=dict)

    @property
    def vazio(self) -> bool:
        return not self.contas

    def conta(self, codigo: str) -> Conta | None:
        return self.contas.get(codigo)

    def na_dre(self, codigo: str) -> bool:
        """Entra na DRE: competência CO ou CO/CA (plano vazio: toda conta)."""
        if self.vazio:
            return True
        c = self.contas.get(codigo)
        return c is not None and c.competencia in COMPETENCIA_DRE

    def no_caixa(self, codigo: str) -> bool:
        """Entra no fluxo de caixa: competência CA ou CO/CA (plano vazio: toda conta)."""
        if self.vazio:
            return True
        c = self.contas.get(codigo)
        return c is not None and c.competencia in COMPETENCIA_CAIXA

    def fator(self, codigo: str) -> int | None:
        c = self.contas.get(codigo)
        return c.fator if c is not None else None

    def nome(self, codigo: str) -> str | None:
        """Descritivo da conta (nível 3) ou o nome do nível 2."""
        if codigo in self.contas:
            return self.contas[codigo].descritivo
        return self._niveis_2.get(codigo)

    @cached_property
    def _niveis_2(self) -> dict[str, str]:
        # o plano repete o nome do nível 2 em cada conta, às vezes diferente: vale o mais comum
        nomes: dict[str, Counter[str]] = {}
        for c in self.contas.values():
            nomes.setdefault(c.id_nivel_2, Counter())[c.nivel_2] += 1
        return {k: v.most_common(1)[0][0] for k, v in nomes.items()}


def plano_das_linhas(linhas: Iterable[Mapping[str, Any]]) -> Plano:
    contas: dict[str, Conta] = {}
    for r in linhas:
        codigo = normalizar(r["id_descritivo"])
        if codigo is None:
            continue
        contas[codigo] = Conta(
            id_descritivo=codigo,
            descritivo=r["descritivo"],
            id_nivel_2=normalizar(r["id_nivel_2"]) or "",
            nivel_2=r["nivel_2"],
            fator=int(r["fator"]),
            caixa=r["caixa"],
            competencia=r["competencia"],
        )
    return Plano(contas)


def ler_plano(conn: Conexao) -> Plano:
    """O plano, uma vez por requisição; vazio sem a migration 17."""

    def ler() -> Plano:
        if not existe(conn, "cfg.plano_conta"):
            return Plano()
        return plano_das_linhas(conn.execute(SQL_PLANO).fetchall())

    return memo(("plano",), ler)

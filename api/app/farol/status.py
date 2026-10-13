"""Farol: classifica um valor em saudável / atenção / crítico. Função pura."""

from dataclasses import dataclass
from decimal import Decimal

from app.telas.contrato import Status

Numero = Decimal | int | float


@dataclass(frozen=True)
class Faixa:
    """Limites do farol.

    maior_melhor=True (ex.: margem):
        valor >= atencao           → saudável
        critico <= valor < atencao → atenção
        valor < critico            → crítico
    maior_melhor=False (ex.: prazo médio): espelhado, com <=.

    O valor exatamente no limite fica sempre na faixa melhor.
    """

    atencao: Decimal
    critico: Decimal
    maior_melhor: bool = True

    def __post_init__(self) -> None:
        if not (self.atencao.is_finite() and self.critico.is_finite()):
            raise ValueError("limites da faixa precisam ser finitos")
        if self.maior_melhor and self.critico > self.atencao:
            raise ValueError("com maior_melhor, critico precisa ser <= atencao")
        if not self.maior_melhor and self.critico < self.atencao:
            raise ValueError("com menor_melhor, critico precisa ser >= atencao")


def status(valor: Numero | None, faixa: Faixa) -> Status | None:
    """Status do valor na faixa; None quando não há valor (None ou NaN)."""
    if valor is None:
        return None
    # str() evita levar o erro binário do float para o Decimal (0.1 → Decimal('0.1'))
    v = Decimal(str(valor)) if isinstance(valor, float) else Decimal(valor)
    if v.is_nan():
        return None

    if faixa.maior_melhor:
        if v >= faixa.atencao:
            return "saudavel"
        if v >= faixa.critico:
            return "atencao"
        return "critico"

    if v <= faixa.atencao:
        return "saudavel"
    if v <= faixa.critico:
        return "atencao"
    return "critico"

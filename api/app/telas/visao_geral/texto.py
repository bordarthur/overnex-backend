"""Números escritos dentro de textos (contexto, resumo), no mesmo formato do front.

O valor principal de cada cartão vai como número e o front formata; aqui só os números
que aparecem no meio de uma frase.
"""

from decimal import ROUND_HALF_UP, Decimal


def _pt(valor: Decimal, casas: int) -> str:
    """1234567.891 → "1.234.567,89"."""
    q = Decimal(1).scaleb(-casas)
    texto = f"{abs(valor.quantize(q, rounding=ROUND_HALF_UP)):,.{casas}f}"
    return texto.replace(",", "_").replace(".", ",").replace("_", ".")


def moeda(valor: Decimal) -> str:
    """Compacto como o `money` do front: R$ 6,54 mi, R$ 360 mil, R$ 950."""
    a = abs(valor)
    if a >= Decimal(1_000_000):
        return f"R$ {_pt(a / Decimal(1_000_000), 2)} mi"
    if a >= Decimal(1_000):
        return f"R$ {_pt(a / Decimal(1_000), 0)} mil"
    return f"R$ {_pt(a, 0)}"


def inteiro(valor: int) -> str:
    """12345 → "12.345"."""
    return _pt(Decimal(valor), 0)


def moeda_sinal(valor: Decimal) -> str:
    """Como `moeda`, com "-" na frente do valor negativo: -R$ 51 mil."""
    return ("-" if valor < 0 else "") + moeda(valor)


def moeda_cheia(valor: Decimal) -> str:
    """Sem abreviar, como `compacto: false` no front: R$ 2.995."""
    return f"R$ {_pt(abs(valor), 0)}"


def numero(valor: Decimal, casas: int = 0) -> str:
    """2.345 com 1 casa → "2,3"."""
    return ("-" if valor < 0 else "") + _pt(valor, casas)

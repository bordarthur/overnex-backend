"""Sorteio determinístico: o mesmo conjunto de chaves dá sempre os mesmos números.

Cada dia, cliente, pessoa ou mês tem o seu gerador, semeado pelas chaves. Assim, gerar de
novo no dia seguinte repete todo o passado e só acrescenta o dia novo: os números dos meses
fechados não mudam de uma carga para a outra.
"""

import hashlib
import math
import random
from collections.abc import Sequence

# trocar a semente muda todos os dados da demonstração
SEMENTE = "distribuidora-overnex-v1"


def rng(*chaves: object) -> random.Random:
    texto = "|".join([SEMENTE, *(str(c) for c in chaves)])
    digest = hashlib.sha256(texto.encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def poisson(r: random.Random, media: float) -> int:
    """Número de eventos com essa média (Knuth até 30; acima, aproximação normal)."""
    if media <= 0:
        return 0
    if media > 30:
        return max(0, round(r.gauss(media, math.sqrt(media))))
    limite = math.exp(-media)
    k, p = 0, 1.0
    while True:
        p *= r.random()
        if p <= limite:
            return k
        k += 1


def lognormal(r: random.Random, sigma: float) -> float:
    """Fator multiplicativo com média 1."""
    return r.lognormvariate(-(sigma**2) / 2, sigma)


def acumulados(pesos: Sequence[float]) -> list[float]:
    total = 0.0
    saida = []
    for p in pesos:
        total += p
        saida.append(total)
    return saida

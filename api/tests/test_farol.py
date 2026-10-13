from decimal import Decimal

import pytest

from app.farol import Faixa, status
from app.telas.contrato import Status

MARGEM = Faixa(atencao=Decimal("0.15"), critico=Decimal("0.05"))  # maior é melhor
PRAZO = Faixa(atencao=Decimal(30), critico=Decimal(60), maior_melhor=False)


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        (Decimal("0.20"), "saudavel"),
        (Decimal("0.15"), "saudavel"),  # no limite de atenção: faixa melhor
        (Decimal("0.1499"), "atencao"),
        (Decimal("0.05"), "atencao"),  # no limite crítico: faixa melhor
        (Decimal("0.0499"), "critico"),
        (Decimal("-1"), "critico"),
        (0.15, "saudavel"),  # float sem erro binário
        (1, "saudavel"),
        (float("inf"), "saudavel"),
        (float("-inf"), "critico"),
    ],
)
def test_maior_melhor(valor: Decimal | float | int, esperado: Status) -> None:
    assert status(valor, MARGEM) == esperado


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        (0, "saudavel"),
        (30, "saudavel"),
        (Decimal("30.01"), "atencao"),
        (60, "atencao"),
        (61, "critico"),
        (float("inf"), "critico"),
    ],
)
def test_menor_melhor(valor: Decimal | float | int, esperado: Status) -> None:
    assert status(valor, PRAZO) == esperado


@pytest.mark.parametrize("valor", [None, float("nan"), Decimal("NaN")])
def test_sem_valor(valor: Decimal | float | None) -> None:
    assert status(valor, MARGEM) is None


def test_faixa_degenerada_atencao_igual_critico() -> None:
    faixa = Faixa(atencao=Decimal(10), critico=Decimal(10))
    assert status(10, faixa) == "saudavel"
    assert status(Decimal("9.99"), faixa) == "critico"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"atencao": Decimal(5), "critico": Decimal(10)},
        {"atencao": Decimal(10), "critico": Decimal(5), "maior_melhor": False},
        {"atencao": Decimal("Infinity"), "critico": Decimal(0)},
        {"atencao": Decimal("NaN"), "critico": Decimal(0)},
    ],
)
def test_faixa_invalida(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError, match=r"faixa|critico"):
        Faixa(**kwargs)  # type: ignore[arg-type]

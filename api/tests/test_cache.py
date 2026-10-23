"""Caches da API (app/core/cache.py) e a escolha do parâmetro vigente — sem banco."""

from datetime import date

from app.core.cache import CacheComPrazo, memo, por_requisicao
from app.farol.parametro import Parametro, vigente


def test_memo_so_guarda_dentro_da_requisicao() -> None:
    chamadas: list[int] = []

    def ler() -> int:
        chamadas.append(1)
        return len(chamadas)

    # fora de por_requisicao(): calcula sempre
    assert memo("fontes", ler) == 1
    assert memo("fontes", ler) == 2

    with por_requisicao():
        assert memo("fontes", ler) == 3
        assert memo("fontes", ler) == 3
        assert memo("outra", ler) == 4
    # a requisição seguinte começa vazia
    with por_requisicao():
        assert memo("fontes", ler) == 5


def test_cache_com_prazo_vence_e_tira_o_mais_antigo() -> None:
    agora = [0.0]
    cache: CacheComPrazo[str] = CacheComPrazo(maximo=2, relogio=lambda: agora[0])

    cache.guardar("a", "A", 60)
    cache.guardar("b", "B", 60)
    assert cache.obter("a") == "A"  # "a" passa a ser o mais recente
    cache.guardar("c", "C", 60)  # cheio: sai "b", o usado há mais tempo
    assert cache.obter("b") is None
    assert cache.obter("a") == "A"

    agora[0] = 60.0  # venceu
    assert cache.obter("a") is None
    assert cache.obter("c") is None


def test_parametro_vigente_na_data() -> None:
    parametros = [
        Parametro("farol.cmv", {"atencao": 1}, date(2025, 1, 1), None),
        Parametro("farol.cmv", {"atencao": 2}, date(2026, 1, 1), date(2026, 6, 30)),
        Parametro("custos.abertura", {"nome": "grupo"}, date(2025, 1, 1), None),
    ]

    assert vigente(parametros, "farol.cmv", date(2026, 3, 1)) == {"atencao": 2}
    # depois do fim da vigência do mais novo, volta o anterior
    assert vigente(parametros, "farol.cmv", date(2026, 7, 1)) == {"atencao": 1}
    assert vigente(parametros, "farol.cmv", date(2024, 12, 31)) is None
    assert vigente(parametros, "farol.ticket_pedido", date(2026, 3, 1)) is None

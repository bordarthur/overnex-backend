"""Cache que espera quem já está montando, consultas numa ida só e pré-montagem das telas —
sem banco."""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import pytest

from app.core.cache import CacheComPrazo
from app.core.config import get_settings
from app.core.db import em_uma_ida
from app.core.erros import AcessoNegado
from app.seguranca.auth import UsuarioToken
from app.telas import preparo
from app.telas.paineis import base
from app.telas.visao_geral.vendas import TabelaDoCliente

# ---------- CacheComPrazo.obter_ou_montar ----------


def test_duas_threads_da_mesma_chave_montam_uma_vez() -> None:
    cache: CacheComPrazo[str] = CacheComPrazo(maximo=10)
    comecou, pode_terminar = threading.Event(), threading.Event()
    montagens: list[str] = []

    def montar() -> str:
        montagens.append(threading.current_thread().name)
        comecou.set()
        pode_terminar.wait(5)
        return "tela"

    resultados: list[str] = []
    primeira = threading.Thread(
        target=lambda: resultados.append(cache.obter_ou_montar("k", montar, 60))
    )
    primeira.start()
    comecou.wait(5)
    # a segunda chega com a primeira ainda montando: espera por ela
    segunda = threading.Thread(
        target=lambda: resultados.append(cache.obter_ou_montar("k", montar, 60))
    )
    segunda.start()
    pode_terminar.set()
    primeira.join(5)
    segunda.join(5)

    assert resultados == ["tela", "tela"]
    assert len(montagens) == 1
    # e fica guardada
    assert cache.obter_ou_montar("k", lambda: "outra", 60) == "tela"


def test_erro_de_quem_monta_vale_para_quem_espera_e_nao_fica_guardado() -> None:
    cache: CacheComPrazo[str] = CacheComPrazo(maximo=10)
    with pytest.raises(ValueError, match="quebrou"):
        cache.obter_ou_montar("k", lambda: (_ for _ in ()).throw(ValueError("quebrou")), 60)
    assert cache.obter_ou_montar("k", lambda: "depois", 60) == "depois"


def test_cache_desligado_monta_sempre() -> None:
    cache: CacheComPrazo[int] = CacheComPrazo(maximo=10)
    contas = iter(range(10))
    assert cache.obter_ou_montar("k", lambda: next(contas), 0) == 0
    assert cache.obter_ou_montar("k", lambda: next(contas), 0) == 1


# ---------- em_uma_ida ----------


class Cursor:
    def __init__(self, linhas: list[Any]) -> None:
        self.linhas = linhas

    def fetchall(self) -> list[Any]:
        return self.linhas


class ConexaoPipeline:
    def __init__(self) -> None:
        self.em_pipeline = False
        self.consultas: list[tuple[Any, Any, bool]] = []

    @contextmanager
    def pipeline(self) -> Iterator[None]:
        self.em_pipeline = True
        yield
        self.em_pipeline = False

    def execute(self, q: Any, p: Any) -> Cursor:
        self.consultas.append((q, p, self.em_pipeline))
        return Cursor([{"q": q}])


def test_em_uma_ida_manda_tudo_no_pipeline_e_devolve_por_nome() -> None:
    conn = ConexaoPipeline()
    linhas = em_uma_ida(conn, {"a": ("select 1", None), "b": ("select 2", (1,))})  # type: ignore[arg-type]

    assert linhas == {"a": [{"q": "select 1"}], "b": [{"q": "select 2"}]}
    assert [c[2] for c in conn.consultas] == [True, True]
    assert em_uma_ida(conn, {}) == {}  # type: ignore[arg-type]


# ---------- colunas da Equipe ----------


def test_papeis_da_pj_valem_so_se_a_coluna_existe() -> None:
    pj = TabelaDoCliente(
        "f_vendas_pj",
        dict(base.COLUNAS_VENDAS_PAINEL),
        existentes=frozenset({"en_codigovendedor", "it_grupo"}),
    )
    padrao = TabelaDoCliente("f_vendas_nilo_maia", dict(base.COLUNAS_VENDAS_PAINEL))
    assert base.tem_papel([pj], "vendedor")
    assert base.tem_papel([pj], "linha")
    assert not base.tem_papel([pj], "quantidade")
    assert not base.tem_papel([padrao], "vendedor")


# ---------- pré-montagem ----------

USUARIO = UsuarioToken(id_autenticacao=UUID(int=7))


class PoolFalso:
    def __init__(self) -> None:
        self.desfeitas = 0

    @contextmanager
    def connection(self) -> Iterator[Any]:
        pool = self

        class Conn:
            def rollback(self) -> None:
                pool.desfeitas += 1

        yield Conn()


@pytest.fixture
def com_cache() -> Iterator[Any]:
    preparo._agendados.limpar()
    yield get_settings().model_copy(update={"visao_geral_cache_segundos": 900})
    preparo._agendados.limpar()


def test_agenda_so_as_telas_liberadas_e_uma_vez_por_usuario(
    com_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    enviados: list[Any] = []
    monkeypatch.setattr(preparo._fila, "submit", lambda *a: enviados.append(a))

    agendadas = preparo.agendar(PoolFalso(), USUARIO, ["FIN-01", "COM-03", "VG-01"], com_cache)  # type: ignore[arg-type]

    # na ordem de TELAS (a Visão geral primeiro), sem o que não é tela pré-montável
    assert agendadas == ["VG-01", "COM-03"]
    assert len(enviados) == 1
    assert preparo.agendar(PoolFalso(), USUARIO, ["VG-01"], com_cache) == []  # type: ignore[arg-type]


def test_sem_cache_ou_sem_pool_nao_agenda(com_cache: Any) -> None:
    sem_cache = com_cache.model_copy(update={"visao_geral_cache_segundos": 0})
    assert preparo.agendar(PoolFalso(), USUARIO, ["VG-01"], sem_cache) == []  # type: ignore[arg-type]
    assert preparo.agendar(None, USUARIO, ["VG-01"], com_cache) == []


def test_preparar_monta_cada_tela_sem_gravar_e_segue_depois_de_negado_ou_erro(
    com_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    montadas: list[str] = []

    def verificar(sessao: Any, codigo: str, empresa: Any, *, exige_dado_nominal: bool) -> Any:
        if codigo == "COM-02":
            assert exige_dado_nominal
            raise AcessoNegado(6, "sem dado nominal")
        return codigo

    def responder(nome: str, falha: bool = False) -> Any:
        def f(ctx: Any, settings: Any) -> None:
            if falha:
                raise RuntimeError("regra quebrou")
            montadas.append(nome)

        return f

    monkeypatch.setattr(preparo, "verificar_acesso_consolidado", verificar)
    telas = [
        preparo.Tela("VG-01", False, responder("VG-01", falha=True)),
        preparo.Tela("COM-02", True, responder("COM-02")),
        preparo.Tela("COM-03", False, responder("COM-03")),
    ]
    pool = PoolFalso()
    preparo.preparar(pool, USUARIO, telas, com_cache)  # type: ignore[arg-type]

    assert montadas == ["COM-03"]
    # toda conexão termina desfeita: nada gravado (nem o log de acesso)
    assert pool.desfeitas == 3

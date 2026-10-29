"""Caches da API, para cortar idas ao banco (cada uma custa a latência da rede).

- por requisição (`memo`): o que várias seções leem de novo na mesma requisição (fontes,
  parâmetros e feriados do cliente). Só guarda dentro de `por_requisicao()`; fora dele,
  calcula sempre. Some no fim da requisição: nada passa de um usuário para outro;
- de esquema (`existe`, `existe_coluna`): tabela ou coluna que existe não deixa de existir
  sem migration. Guarda só o "existe": uma migration nova vale sem reiniciar a API;
- de respostas (`CacheComPrazo`): a resposta pronta de uma tela, por um prazo. A chave é
  de quem chama: precisa ter tudo de que a resposta depende (cliente, empresas, recorte,
  carga).
"""

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable, Iterator
from concurrent.futures import Future
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from app.core.db import Conexao

_requisicao: ContextVar[dict[Hashable, Any] | None] = ContextVar("cache_requisicao", default=None)


@contextmanager
def por_requisicao() -> Iterator[None]:
    """Liga o `memo` no bloco (uma requisição)."""
    token = _requisicao.set({})
    try:
        yield
    finally:
        _requisicao.reset(token)


def memo[T](chave: Hashable, calcular: Callable[[], T]) -> T:
    """`calcular()` uma vez por requisição para a `chave`; fora de `por_requisicao()`,
    sempre."""
    cache = _requisicao.get()
    if cache is None:
        return calcular()
    if chave not in cache:
        cache[chave] = calcular()
    valor: T = cache[chave]
    return valor


_existentes: set[str] = set()
_trava_esquema = threading.Lock()


def existe(conn: Conexao, nome: str) -> bool:
    """A tabela `esquema.nome` existe (ex.: "mart.resumo_mensal")."""
    if nome in _existentes:
        return True
    linha = conn.execute("select to_regclass(%s) is not null as ok", (nome,)).fetchone()
    if linha and linha["ok"]:
        with _trava_esquema:
            _existentes.add(nome)
        return True
    return False


def existe_coluna(conn: Conexao, tabela: str, coluna: str) -> bool:
    """A coluna existe em `esquema.tabela` (ex.: "mart.resumo_mensal", "dimensao")."""
    nome = f"{tabela}.{coluna}"
    if nome in _existentes:
        return True
    esquema, _, so_tabela = tabela.partition(".")
    linha = conn.execute(
        """
        select exists (
            select 1 from information_schema.columns
             where table_schema = %s and table_name = %s and column_name = %s
        ) as ok
        """,
        (esquema, so_tabela, coluna),
    ).fetchone()
    if linha and linha["ok"]:
        with _trava_esquema:
            _existentes.add(nome)
        return True
    return False


class CacheComPrazo[V]:
    """Até `maximo` valores, cada um válido por um prazo; cheio, sai o usado há mais tempo.
    Seguro entre threads (as rotas síncronas rodam num pool de threads)."""

    def __init__(self, maximo: int, relogio: Callable[[], float] = time.monotonic) -> None:
        self._maximo = maximo
        self._relogio = relogio
        self._itens: OrderedDict[Hashable, tuple[float, V]] = OrderedDict()
        self._trava = threading.Lock()
        # chave → resultado de quem está montando agora (ver obter_ou_montar)
        self._montando: dict[Hashable, Future[V]] = {}

    def obter_ou_montar(self, chave: Hashable, montar: Callable[[], V], segundos: float) -> V:
        """O valor guardado; senão, monta e guarda. Se outra thread já está montando a mesma
        chave (ex.: a pré-montagem da tela e o clique do usuário), espera por ela em vez de
        montar de novo. Com `segundos` <= 0, só monta (cache desligado)."""
        if segundos <= 0:
            return montar()
        with self._trava:
            item = self._itens.get(chave)
            if item is not None and self._relogio() < item[0]:
                self._itens.move_to_end(chave)
                return item[1]
            andamento = self._montando.get(chave)
            dono = andamento is None
            if andamento is None:
                andamento = self._montando[chave] = Future()
        if not dono:
            # o erro de quem montou (ex.: 422) vale para quem esperou
            return andamento.result()
        try:
            valor = montar()
        except BaseException as erro:
            andamento.set_exception(erro)
            raise
        finally:
            with self._trava:
                self._montando.pop(chave, None)
        self.guardar(chave, valor, segundos)
        andamento.set_result(valor)
        return valor

    def obter(self, chave: Hashable) -> V | None:
        with self._trava:
            item = self._itens.get(chave)
            if item is None:
                return None
            vence, valor = item
            if self._relogio() >= vence:
                del self._itens[chave]
                return None
            self._itens.move_to_end(chave)
            return valor

    def guardar(self, chave: Hashable, valor: V, segundos: float) -> None:
        with self._trava:
            self._itens[chave] = (self._relogio() + segundos, valor)
            self._itens.move_to_end(chave)
            while len(self._itens) > self._maximo:
                self._itens.popitem(last=False)

    def apagar_se(self, condicao: Callable[[Hashable], bool]) -> int:
        """Apaga os valores cuja chave atende à condição; devolve quantos."""
        with self._trava:
            chaves = [c for c in self._itens if condicao(c)]
            for c in chaves:
                del self._itens[c]
            return len(chaves)

    def limpar(self) -> None:
        with self._trava:
            self._itens.clear()

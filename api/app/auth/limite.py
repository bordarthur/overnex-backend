"""Limite de tentativas em memória (janela deslizante), para entrar e esqueci-senha.

Vale por processo: com mais de uma instância da API, cada uma conta as suas (ver TODO.md).

- entrar, senhas erradas por e-mail + IP do cliente: segura a tentativa de senha contra uma
  conta. Só conta o 401, e acertar a senha zera a contagem;
- entrar, tentativas por IP: segura quem varre e-mails. Também protege a cota do Supabase
  Auth, que vê todas as chamadas saindo do IP da API (150 chamadas a /token a cada 5 min,
  login e renovação somados, para todos os usuários);
- esqueci-senha, pedidos por e-mail + IP e por IP.

Tentativa recusada pelo limite não conta: a espera (Retry-After) não aumenta a cada clique.
"""

import math
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

# (tentativas, janela em segundos)
ENTRAR_FALHAS_POR_EMAIL_IP = (5, 15 * 60)
ENTRAR_POR_IP = (50, 5 * 60)
ESQUECI_POR_EMAIL_IP = (3, 15 * 60)
ESQUECI_POR_IP = (10, 15 * 60)

# a cada tantas tentativas, apaga as chaves sem tentativa na janela (memória não cresce)
_VARRER_A_CADA = 1000


class LimiteTentativas:
    def __init__(
        self, maximo: int, janela: float, relogio: Callable[[], float] = time.monotonic
    ) -> None:
        self.maximo = maximo
        self.janela = janela
        self._relogio = relogio
        self._tentativas: dict[str, deque[float]] = {}
        self._trava = threading.Lock()
        self._desde_varredura = 0

    def _fila(self, chave: str, agora: float) -> deque[float]:
        fila = self._tentativas.setdefault(chave, deque())
        while fila and fila[0] <= agora - self.janela:
            fila.popleft()
        return fila

    def _espera(self, fila: deque[float], agora: float) -> int:
        if len(fila) < self.maximo:
            return 0
        # quando a tentativa que estourou o limite sai da janela
        libera = fila[len(fila) - self.maximo] + self.janela
        return max(1, math.ceil(libera - agora))

    def espera(self, chave: str) -> int:
        """Segundos até poder tentar de novo; 0 = pode agora. Não conta tentativa."""
        agora = self._relogio()
        with self._trava:
            return self._espera(self._fila(chave, agora), agora)

    def registrar(self, chave: str) -> int:
        """Conta a tentativa se couber no limite. Devolve a espera: 0 = contou e pode seguir;
        mais que 0 = passou do limite (não conta) e tem de esperar esses segundos."""
        agora = self._relogio()
        with self._trava:
            self._desde_varredura += 1
            if self._desde_varredura >= _VARRER_A_CADA:
                self._varrer(agora)
            fila = self._fila(chave, agora)
            if (espera := self._espera(fila, agora)) > 0:
                return espera
            fila.append(agora)
            return 0

    def limpar(self, chave: str) -> None:
        with self._trava:
            self._tentativas.pop(chave, None)

    def _varrer(self, agora: float) -> None:
        self._desde_varredura = 0
        limite = agora - self.janela
        for chave in [c for c, f in self._tentativas.items() if not f or f[-1] <= limite]:
            del self._tentativas[chave]


@dataclass
class Limites:
    entrar_falhas: LimiteTentativas = field(
        default_factory=lambda: LimiteTentativas(*ENTRAR_FALHAS_POR_EMAIL_IP)
    )
    entrar_ip: LimiteTentativas = field(default_factory=lambda: LimiteTentativas(*ENTRAR_POR_IP))
    esqueci_email_ip: LimiteTentativas = field(
        default_factory=lambda: LimiteTentativas(*ESQUECI_POR_EMAIL_IP)
    )
    esqueci_ip: LimiteTentativas = field(default_factory=lambda: LimiteTentativas(*ESQUECI_POR_IP))

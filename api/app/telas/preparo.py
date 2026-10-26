"""Pré-montagem das telas depois de GET /api/sessao.

O front chama /api/sessao ao abrir e, logo depois, a primeira tela. Sem cache, cada tela
leva de 1 a 3 s para montar; com a resposta pronta, ~0,25 s. Por isso, a sessão agenda a
montagem, em segundo plano, das telas que o usuário pode abrir (no mês padrão, todas as
empresas do vínculo, sem filtro): é a mesma chave do cache que a tela usa ao ser aberta. Se
o usuário clicar numa tela que ainda está sendo montada, a requisição espera por ela em vez
de montar de novo (CacheComPrazo.obter_ou_montar).

- a checagem de acesso é a mesma da rota (tela no perfil, empresas, dado nominal), mas o
  registro em seg.log_acesso não é gravado: o usuário não abriu a tela. Quando abrir, a
  rota confere e registra como sempre;
- uma vez por usuário a cada metade do prazo do cache; com o cache desligado
  (VISAO_GERAL_CACHE_SEGUNDOS=0), não pré-monta;
- uma tela por vez, numa fila de 2 threads, para não disputar o banco com as requisições;
- erro numa tela vai para o log e não afeta as outras nem a sessão.
"""

import logging
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from uuid import UUID

from app.core.cache import CacheComPrazo
from app.core.config import Settings
from app.core.db import Pool, Sessao
from app.core.erros import AcessoNegado
from app.seguranca.acesso import ContextoConsolidado, verificar_acesso_consolidado
from app.seguranca.auth import UsuarioToken
from app.telas.paineis import router as paineis
from app.telas.visao_geral import router as visao_geral

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Tela:
    codigo: str
    nominal: bool
    responder: Callable[[ContextoConsolidado, Settings], object]


# na ordem em que o usuário costuma abrir: a Visão geral é a primeira tela
TELAS: Sequence[Tela] = (
    Tela(visao_geral.CODIGO_TELA, False, lambda c, s: visao_geral.responder(c, s)),
    *(
        Tela(
            paineis.CODIGOS[t],
            t in paineis.NOMINAL,
            lambda c, s, t=t: paineis.responder(c, s, t),  # type: ignore[misc]
        )
        for t in paineis.CODIGOS
    ),
)

_agendados: CacheComPrazo[bool] = CacheComPrazo(maximo=1024)
_fila = ThreadPoolExecutor(max_workers=2, thread_name_prefix="preparo")


def agendar(
    pool: Pool | None, usuario: UsuarioToken, telas: Sequence[str], settings: Settings
) -> list[str]:
    """Agenda a pré-montagem das `telas` liberadas (códigos); devolve as agendadas."""
    prazo = settings.visao_geral_cache_segundos
    if pool is None or prazo <= 0:
        return []
    chave: UUID = usuario.id_autenticacao
    if _agendados.obter(chave):
        return []
    _agendados.guardar(chave, True, prazo / 2)
    escolhidas = [t for t in TELAS if t.codigo in telas]
    _fila.submit(preparar, pool, usuario, escolhidas, settings)
    return [t.codigo for t in escolhidas]


def preparar(pool: Pool, usuario: UsuarioToken, telas: Sequence[Tela], settings: Settings) -> None:
    for tela in telas:
        try:
            with pool.connection() as conn:
                sessao = Sessao(conn=conn, usuario=usuario, pool=pool)
                try:
                    ctx = verificar_acesso_consolidado(
                        sessao, tela.codigo, None, exige_dado_nominal=tela.nominal
                    )
                    tela.responder(ctx, settings)
                finally:
                    # só leitura; o log de acesso da checagem não é gravado (ver acima)
                    conn.rollback()
        except AcessoNegado:
            continue
        except Exception:
            logger.warning("Pré-montagem da tela %s falhou", tela.codigo, exc_info=True)

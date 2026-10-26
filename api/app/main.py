"""Cria o app. Sem CORS: front e API ficam no mesmo domínio."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.admin.router import router as admin_router
from app.auth.gotrue import GoTrue, GoTrueIndisponivel
from app.auth.limite import Limites
from app.auth.router import MSG_INDISPONIVEL, ErroAuth, apagar_cookie
from app.auth.router import router as auth_router
from app.core.config import Ambiente, ProvedorAuth, Settings, get_settings
from app.core.db import Pool, criar_pool, get_pool
from app.core.erros import AcessoNegado
from app.dados.router import router as dados_router
from app.pdf.router import router as pdf_router
from app.sessao.router import router as sessao_router
from app.telas.fin_01 import router as fin_01_router
from app.telas.paineis.router import router as paineis_router
from app.telas.visao_geral.router import router as visao_geral_router

logger = logging.getLogger("app")


def create_app(settings: Settings | None = None) -> FastAPI:
    # Settings inválido (ex.: AUTH_FAKE fora de dev) levanta aqui e o app não sobe.
    settings = settings or get_settings()
    dev = settings.ambiente is Ambiente.DEV
    login_supabase = settings.auth_provedor is ProvedorAuth.SUPABASE
    gotrue = (
        GoTrue(settings.supabase_url, settings.supabase_anon_key.get_secret_value())
        if login_supabase
        else None
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool = criar_pool(settings)
        # wait=False: o app sobe mesmo com o banco fora; /health acusa.
        pool.open(wait=False)
        app.state.pool = pool
        try:
            yield
        finally:
            pool.close()
            if gotrue is not None:
                gotrue.close()

    app = FastAPI(
        title="Overnex API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if dev else None,
        redoc_url=None,
        openapi_url="/openapi.json" if dev else None,
    )
    app.state.gotrue = gotrue
    app.state.limites_auth = Limites()

    @app.exception_handler(AcessoNegado)
    async def _acesso_negado(request: Request, exc: AcessoNegado) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN, content={"detail": "Acesso negado."}
        )

    @app.exception_handler(ErroAuth)
    async def _erro_auth(request: Request, exc: ErroAuth) -> JSONResponse:
        resposta = JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        resposta.headers["Cache-Control"] = "no-store"
        if exc.retry_after is not None:
            resposta.headers["Retry-After"] = str(exc.retry_after)
        if exc.apagar_cookie:
            apagar_cookie(resposta, settings)
        return resposta

    @app.exception_handler(GoTrueIndisponivel)
    async def _gotrue_indisponivel(request: Request, exc: GoTrueIndisponivel) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"detail": MSG_INDISPONIVEL}
        )

    @app.exception_handler(RequestValidationError)
    async def _requisicao_invalida(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Só onde e o quê; sem ecoar o valor recebido.
        erros = [{"loc": e.get("loc"), "msg": e.get("msg")} for e in exc.errors()]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": erros}
        )

    @app.exception_handler(Exception)
    async def _erro_interno(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Erro não tratado em %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Erro interno."})

    @app.get("/health", tags=["infra"])
    def health(pool: Annotated[Pool, Depends(get_pool)]) -> dict[str, str]:
        """200 só se o banco responder."""
        try:
            with pool.connection(timeout=3) as conn:
                conn.execute("select 1")
        except Exception as exc:
            logger.warning("Health: banco indisponível: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Banco indisponível."
            ) from exc
        return {"status": "ok"}

    if login_supabase:
        app.include_router(auth_router)
    app.include_router(sessao_router)
    app.include_router(visao_geral_router)
    app.include_router(paineis_router)
    app.include_router(fin_01_router)
    app.include_router(dados_router)
    app.include_router(admin_router)
    app.include_router(pdf_router)
    return app


app = create_app()

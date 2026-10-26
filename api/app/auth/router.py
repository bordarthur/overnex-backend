"""POST /api/auth/*: login com e-mail e senha no Supabase Auth, pela própria API.

O front não fala com o Supabase: chama estas rotas, que repassam ao GoTrue. O refresh token
fica num cookie httpOnly (ovx_refresh, Path=/api/auth); o access token vai no corpo e fica
só na memória do front. Todas exigem Content-Type: application/json (com SameSite=Strict,
fecha a porta para formulário de outro site).

Erros no formato {"detail": "..."}, com texto pronto para o usuário. Senha e tokens nunca
vão para o log.
"""

import logging
import re
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field

from app.auth.cadastro import VerificarCadastro, get_verificar_cadastro
from app.auth.gotrue import GoTrue, GoTrueErro, GoTrueIndisponivel, SessaoGoTrue, TipoLink
from app.auth.limite import ENTRAR_FALHAS_POR_EMAIL_IP, Limites
from app.core.config import Settings, get_settings
from app.seguranca.auth import PREFIXO_FAKE
from app.telas.moldura import ModeloCamel

logger = logging.getLogger(__name__)

COOKIE_REFRESH = "ovx_refresh"
CAMINHO_COOKIE = "/api/auth"
# Política de senha: a mesma de src/auth/senha.ts do front (8 caracteres, uma maiúscula, um
# número). O Supabase não tem essa combinação exata (ver api/README.md): lá fica "letras e
# números", mais fraca, e a regra de verdade é conferida aqui, antes do /verify.
SENHA_MINIMA = 8
# mesmas classes do front: /[A-ZÀ-Ý]/ e /\d/ (no JavaScript, \d é só 0-9)
_MAIUSCULA = re.compile(r"[A-ZÀ-Ý]")
_NUMERO = re.compile(r"[0-9]")
# limite do bcrypt, que o GoTrue usa
SENHA_MAXIMA_BYTES = 72
# espera sugerida quando quem recusa por excesso é o próprio Supabase
ESPERA_SUPABASE = 60

MSG_CREDENCIAIS = "E-mail ou senha não conferem. Confira os dois e tente de novo."
MSG_ACESSO_NEGADO = "Acesso negado."
MSG_SESSAO = "Sessão expirada. Entre de novo."
MSG_FALHAS = f"Foram {ENTRAR_FALHAS_POR_EMAIL_IP[0]} tentativas com a senha errada."
MSG_LIMITE = "Muitas tentativas. Tente de novo em alguns minutos."
MSG_LINK = "Este link não vale mais."
MSG_SENHA_LONGA = f"A senha pode ter no máximo {SENHA_MAXIMA_BYTES} caracteres."
MSG_INDISPONIVEL = "Autenticação indisponível."

TIPOS_LINK: dict[str, TipoLink] = {"convite": "invite", "recuperacao": "recovery"}


class ErroAuth(Exception):
    """Erro das rotas de /api/auth; o handler em main.py responde, põe o Retry-After (429) e,
    se pedido, apaga o cookie."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        *,
        apagar_cookie: bool = False,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.apagar_cookie = apagar_cookie
        self.retry_after = retry_after


def _muitas(detail: str, segundos: int) -> ErroAuth:
    return ErroAuth(status.HTTP_429_TOO_MANY_REQUESTS, detail, retry_after=segundos)


def problemas_da_senha(senha: str) -> str | None:
    """Texto pronto do que falta na senha, pela política do front; None = senha aceita."""
    if len(senha.encode("utf-8")) > SENHA_MAXIMA_BYTES:
        return MSG_SENHA_LONGA
    faltam = [
        texto
        for texto, atende in (
            (f"pelo menos {SENHA_MINIMA} caracteres", len(senha) >= SENHA_MINIMA),
            ("uma letra maiúscula", _MAIUSCULA.search(senha) is not None),
            ("um número", _NUMERO.search(senha) is not None),
        )
        if not atende
    ]
    if not faltam:
        return None
    lista = faltam[0] if len(faltam) == 1 else ", ".join(faltam[:-1]) + " e " + faltam[-1]
    return f"A senha precisa ter {lista}."


def exigir_json(request: Request) -> None:
    tipo = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if tipo != "application/json":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Envie o corpo em JSON (Content-Type: application/json).",
        )


router = APIRouter(prefix="/api/auth", tags=["auth"], dependencies=[Depends(exigir_json)])

_bearer = HTTPBearer(auto_error=False)


# --- contrato ----------------------------------------------------------------------


class PedidoEntrar(ModeloCamel):
    email: str = Field(min_length=1, max_length=254)
    senha: str = Field(min_length=1, max_length=1024)


class PedidoEsqueciSenha(ModeloCamel):
    email: str = Field(min_length=1, max_length=254)


class PedidoDefinirSenha(ModeloCamel):
    token_hash: str = Field(min_length=1, max_length=512)
    tipo: Literal["convite", "recuperacao"]
    senha: str = Field(max_length=1024)


class PedidoVazio(ModeloCamel):
    """Corpo `{}` de renovar e sair (pode vir vazio, mas com Content-Type: application/json)."""


class UsuarioAuth(ModeloCamel):
    # nome cadastrado pela LANN em seg.usuario (pode faltar)
    nome: str | None
    email: str


class RespostaAuth(ModeloCamel):
    access_token: str
    # quando o access token vence (ISO 8601, UTC); renovar um pouco antes
    expira_em: datetime
    usuario: UsuarioAuth


# --- dependências ------------------------------------------------------------------


def get_gotrue(request: Request) -> GoTrue:
    gotrue: GoTrue | None = getattr(request.app.state, "gotrue", None)
    if gotrue is None:
        raise GoTrueIndisponivel("GoTrue não configurado")
    return gotrue


def get_limites(request: Request) -> Limites:
    limites: Limites = request.app.state.limites_auth
    return limites


def _ip(request: Request) -> str:
    # atrás de proxy, o uvicorn (--proxy-headers e --forwarded-allow-ips) já põe aqui o IP
    # do cliente que veio no X-Forwarded-For
    return request.client.host if request.client else "desconhecido"


def _email(valor: str) -> str:
    return valor.strip().lower()


# --- cookie ------------------------------------------------------------------------


def gravar_cookie(response: Response, refresh_token: str, settings: Settings) -> None:
    response.set_cookie(
        COOKIE_REFRESH,
        refresh_token,
        max_age=settings.auth_refresh_segundos,
        path=CAMINHO_COOKIE,
        secure=settings.cookie_seguro,
        httponly=True,
        samesite="strict",
    )


def apagar_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        COOKIE_REFRESH,
        path=CAMINHO_COOKIE,
        secure=settings.cookie_seguro,
        httponly=True,
        samesite="strict",
    )


# --- apoio -------------------------------------------------------------------------


def _encerrar(gotrue: GoTrue, access_token: str) -> None:
    """Logout no GoTrue sem falhar a resposta (o cookie some de qualquer jeito)."""
    try:
        gotrue.sair(access_token)
    except GoTrueErro as exc:
        logger.info("Logout recusado pelo GoTrue: %s", exc.codigo)
    except GoTrueIndisponivel:
        logger.warning("Logout não chegou ao GoTrue (indisponível)")


def _concluir(
    sessao: SessaoGoTrue,
    recurso: str,
    response: Response,
    gotrue: GoTrue,
    verificar: VerificarCadastro,
    settings: Settings,
    *,
    apagar_cookie_se_negado: bool = False,
) -> RespostaAuth:
    """Confere o cadastro em seg.usuario e devolve o access token, com o cookie do refresh."""
    cadastro = verificar(sessao.id_autenticacao, recurso)
    if cadastro is None:
        # tem login no Supabase, mas não está ativo no Overnex: a sessão criada não fica viva
        _encerrar(gotrue, sessao.access_token)
        raise ErroAuth(
            status.HTTP_403_FORBIDDEN, MSG_ACESSO_NEGADO, apagar_cookie=apagar_cookie_se_negado
        )
    gravar_cookie(response, sessao.refresh_token, settings)
    response.headers["Cache-Control"] = "no-store"
    return RespostaAuth(
        access_token=sessao.access_token,
        expira_em=sessao.expira_em,
        usuario=UsuarioAuth(nome=cadastro.nome, email=cadastro.email),
    )


def _mensagem_senha(exc: GoTrueErro) -> str:
    """Texto da regra que o GoTrue recusou ao gravar a senha."""
    if exc.codigo == "same_password":
        return "A nova senha precisa ser diferente da anterior."
    if exc.codigo == "weak_password":
        detalhe = exc.corpo.get("weak_password")
        motivos = detalhe.get("reasons", []) if isinstance(detalhe, dict) else []
        if "pwned" in motivos:
            return "Essa senha aparece em vazamentos conhecidos. Escolha outra."
        # o Supabase tem regra mais fraca que a nossa; se recusar, mostra a política inteira
        return problemas_da_senha("") or ""
    return "Não foi possível definir essa senha. Escolha outra."


Gotrue = Annotated[GoTrue, Depends(get_gotrue)]
Verificar = Annotated[VerificarCadastro, Depends(get_verificar_cadastro)]
Config = Annotated[Settings, Depends(get_settings)]

_RESPOSTAS_LOGIN: dict[int | str, dict[str, str]] = {
    403: {"description": "Tem login, mas não está ativo no Overnex. Sem cookie."},
    415: {"description": "Content-Type diferente de application/json."},
    503: {"description": "Supabase Auth fora do ar."},
}


# --- rotas -------------------------------------------------------------------------


@router.post(
    "/entrar",
    response_model=RespostaAuth,
    responses={
        401: {"description": MSG_CREDENCIAIS},
        429: {
            "description": f"{MSG_FALHAS} (5 senhas erradas para o e-mail em 15 min) ou "
            f"{MSG_LIMITE} Com Retry-After em segundos."
        },
        **_RESPOSTAS_LOGIN,
    },
)
def entrar(
    corpo: PedidoEntrar,
    request: Request,
    response: Response,
    gotrue: Gotrue,
    verificar: Verificar,
    settings: Config,
    limites: Annotated[Limites, Depends(get_limites)],
) -> RespostaAuth:
    email = _email(corpo.email)
    ip = _ip(request)
    chave = f"{email}|{ip}"
    # 5 senhas erradas para este e-mail, deste IP: pausa até a mais antiga sair da janela
    if espera := limites.entrar_falhas.espera(chave):
        raise _muitas(MSG_FALHAS, espera)
    if espera := limites.entrar_ip.registrar(ip):
        raise _muitas(MSG_LIMITE, espera)
    try:
        sessao = gotrue.entrar(email, corpo.senha)
    except GoTrueErro as exc:
        if exc.status == status.HTTP_429_TOO_MANY_REQUESTS:
            logger.warning("Limite do Supabase Auth atingido no login")
            raise _muitas(MSG_LIMITE, ESPERA_SUPABASE) from exc
        # mesma resposta para e-mail inexistente, senha errada ou e-mail não confirmado
        logger.info("Login recusado pelo GoTrue: %s", exc.codigo)
        limites.entrar_falhas.registrar(chave)
        raise ErroAuth(status.HTTP_401_UNAUTHORIZED, MSG_CREDENCIAIS) from exc
    limites.entrar_falhas.limpar(chave)
    return _concluir(sessao, "auth:entrar", response, gotrue, verificar, settings)


@router.post(
    "/renovar",
    response_model=RespostaAuth,
    responses={
        401: {"description": MSG_SESSAO + " Cookie apagado."},
        403: {"description": "Usuário desativado no Overnex. Cookie apagado."},
        415: {"description": "Content-Type diferente de application/json."},
        503: {"description": "Supabase Auth fora do ar (ou no limite). O cookie fica."},
    },
)
def renovar(
    request: Request,
    response: Response,
    gotrue: Gotrue,
    verificar: Verificar,
    settings: Config,
    _corpo: PedidoVazio | None = None,
) -> RespostaAuth:
    refresh_token = request.cookies.get(COOKIE_REFRESH)
    if not refresh_token:
        raise ErroAuth(status.HTTP_401_UNAUTHORIZED, MSG_SESSAO, apagar_cookie=True)
    try:
        sessao = gotrue.renovar(refresh_token)
    except GoTrueErro as exc:
        if exc.status == status.HTTP_429_TOO_MANY_REQUESTS:
            # a cota é do IP da API, não do usuário: não derruba a sessão dele
            logger.warning("Limite do Supabase Auth atingido na renovação")
            raise ErroAuth(status.HTTP_503_SERVICE_UNAVAILABLE, MSG_INDISPONIVEL) from exc
        logger.info("Renovação recusada pelo GoTrue: %s", exc.codigo)
        raise ErroAuth(status.HTTP_401_UNAUTHORIZED, MSG_SESSAO, apagar_cookie=True) from exc
    return _concluir(
        sessao,
        "auth:renovar",
        response,
        gotrue,
        verificar,
        settings,
        apagar_cookie_se_negado=True,
    )


@router.post(
    "/sair",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={415: {"description": "Content-Type diferente de application/json."}},
)
def sair(
    gotrue: Gotrue,
    settings: Config,
    credenciais: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    _corpo: PedidoVazio | None = None,
) -> Response:
    """Encerra a sessão no GoTrue (se veio o access token) e apaga o cookie. Sempre 204."""
    token = credenciais.credentials if credenciais else ""
    if token and not token.startswith(PREFIXO_FAKE):
        _encerrar(gotrue, token)
    resposta = Response(status_code=status.HTTP_204_NO_CONTENT)
    apagar_cookie(resposta, settings)
    return resposta


@router.post(
    "/esqueci-senha",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={
        415: {"description": "Content-Type diferente de application/json."},
        429: {"description": f"{MSG_LIMITE} Com Retry-After em segundos."},
        503: {"description": "Supabase Auth fora do ar."},
    },
)
def esqueci_senha(
    corpo: PedidoEsqueciSenha,
    request: Request,
    gotrue: Gotrue,
    settings: Config,
    limites: Annotated[Limites, Depends(get_limites)],
) -> Response:
    """Pede o e-mail de recuperação. 204 exista ou não o e-mail (não revela quem tem conta)."""
    email = _email(corpo.email)
    ip = _ip(request)
    if espera := max(
        limites.esqueci_email_ip.registrar(f"{email}|{ip}"), limites.esqueci_ip.registrar(ip)
    ):
        raise _muitas(MSG_LIMITE, espera)
    try:
        gotrue.recuperar(email, f"{settings.front_url}/definir-senha")
    except GoTrueErro as exc:
        # inclusive o limite de envio de e-mails do projeto: responder diferente revelaria
        # se o e-mail existe
        logger.warning("Recuperação recusada pelo GoTrue: %s %s", exc.status, exc.codigo)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/definir-senha",
    response_model=RespostaAuth,
    responses={
        400: {"description": "A senha não atende à política; o texto diz o que falta."},
        410: {"description": f"{MSG_LINK} (vencido, já usado ou inexistente)"},
        429: {"description": f"{MSG_LIMITE} Com Retry-After em segundos."},
        **_RESPOSTAS_LOGIN,
    },
)
def definir_senha(
    corpo: PedidoDefinirSenha,
    response: Response,
    gotrue: Gotrue,
    verificar: Verificar,
    settings: Config,
) -> RespostaAuth:
    """Link do convite ou da recuperação: confere o link, grava a senha e já entra."""
    # antes do /verify, que gasta o link: senha recusada aqui pode ser tentada de novo
    if problema := problemas_da_senha(corpo.senha):
        raise ErroAuth(status.HTTP_400_BAD_REQUEST, problema)
    try:
        sessao = gotrue.verificar(corpo.token_hash, TIPOS_LINK[corpo.tipo])
    except GoTrueErro as exc:
        if exc.status == status.HTTP_429_TOO_MANY_REQUESTS:
            raise _muitas(MSG_LIMITE, ESPERA_SUPABASE) from exc
        # vencido, já usado ou inexistente: o mesmo 410
        logger.info("Link recusado pelo GoTrue: %s", exc.codigo)
        raise ErroAuth(status.HTTP_410_GONE, MSG_LINK) from exc
    try:
        gotrue.definir_senha(sessao.access_token, corpo.senha)
    except GoTrueErro as exc:
        logger.info("Senha recusada pelo GoTrue: %s", exc.codigo)
        _encerrar(gotrue, sessao.access_token)
        raise ErroAuth(status.HTTP_400_BAD_REQUEST, _mensagem_senha(exc)) from exc
    return _concluir(sessao, "auth:definir-senha", response, gotrue, verificar, settings)

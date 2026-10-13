# Overnex — API

FastAPI que serve o front: valida o usuário do Microsoft Entra ID, confere o acesso no banco e devolve o JSON de cada tela pronto para desenhar. É a única peça que fala com o banco para o front, e nunca acessa o Supabase Storage.

## Subir local

Pré-requisitos: [uv](https://docs.astral.sh/uv/), [Supabase CLI](https://supabase.com/docs/guides/cli) e Docker (o `supabase start` usa Docker).

```bash
# 1. banco (na raiz do repositório)
supabase start
supabase db reset          # aplica migrations e seed (base fictícia "Padrão LANN")

# 2. API
cd api
cp .env.example .env       # AMBIENTE=dev e AUTH_FAKE=1 já vêm prontos
uv sync
uv run fastapi dev         # http://127.0.0.1:8000/docs
```

Com `AUTH_FAKE=1` (só em `AMBIENTE=dev`), o token pode ser `fake:<object_id_entra>`. Usuários do seed:

| Usuário | Cliente | `Authorization` |
|---|---|---|
| Ana | Padrão LANN Alfa (empresas `…a1` e `…a2`) | `Bearer fake:0d000000-0000-4000-8000-00000000000a` |
| Bruno | Padrão LANN Beta (empresa `…b1`) | `Bearer fake:0d000000-0000-4000-8000-00000000000b` |

```bash
curl -H "Authorization: Bearer fake:0d000000-0000-4000-8000-00000000000a" \
  "http://127.0.0.1:8000/api/telas/fin-01?empresa=e1000000-0000-4000-8000-0000000000a1&mes=2026-09"
```

## Testes e qualidade

```bash
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pytest               # precisa do supabase start + db reset
uv run pytest -m "not db"   # só os testes que não usam banco
```

Os testes marcados `db` conectam como `api_site` em `127.0.0.1:54322` (sobrescreva com `DATABASE_URL`) e usam `postgres` só para conferir o log de acesso (`DATABASE_URL_ADMIN`).

## OpenAPI para o front

```bash
uv run python -m scripts.export_openapi            # grava api/openapi.json
uv run python -m scripts.export_openapi ../web/openapi.json
```

Não precisa de banco. Em `AMBIENTE` diferente de `dev`, `/docs` e `/openapi.json` ficam desligados no servidor.

## Docker

```bash
docker build -t overnex-api api/
docker run --rm -p 8000:8000 --env-file api/.env overnex-api
```

Sem `AMBIENTE` definido, a API sobe como `prod` e exige `ENTRA_TENANT_ID` e `ENTRA_AUDIENCE`.

## Como está organizada

```
app/
  main.py              cria o app; sem CORS; erros sem detalhe interno; GET /health
  core/config.py       Settings (DATABASE_URL, ENTRA_*, AMBIENTE, AUTH_FAKE)
  core/db.py           pool psycopg (prepare_threshold=None) + uma transação por requisição
  seguranca/auth.py    get_usuario: valida o JWT do Entra (JWKS, iss, aud, exp, tid)
  seguranca/acesso.py  checagem em 6 etapas + seg.log_acesso
  telas/contrato.py    ScreenPayload, Hero, Apoio, Visual, Alerta, Status
  telas/fin_01.py      GET /api/telas/fin-01 (exemplo, sem regra de negócio)
  farol/               status(valor, faixa), função pura
  admin/, pdf/         routers vazios (TODO)
scripts/export_openapi.py
```

### Fluxo de uma requisição de tela

1. `get_usuario` valida o token e extrai o `oid`.
2. `get_sessao` abre **uma** transação, traduz o `oid` no `seg.usuario.id` (`seg.resolver_usuario`) e executa `set_config('app.usuario_id', id, true)`. Daí em diante o RLS do banco só mostra o que é do usuário.
3. `exigir_tela("FIN-01")` roda as 6 etapas (usuário ativo → vínculo → papel → perfil libera a tela → empresa no vínculo → dado nominal). Cliente e empresas vêm do vínculo no banco; o `empresa` da query só vale se estiver nele.
4. Toda decisão grava em `seg.log_acesso`. Na negação, a transação faz commit só do log e a resposta é `403 {"detail": "Acesso negado."}`, sem dizer a etapa.
5. Com sucesso, a tela monta o `ScreenPayload`; `atualizado_em` é a `data_snapshot` da última carga `ok` de lançamentos do cliente (ou `null`).

### Conexão em produção

Use o pooler do Supabase em modo transação (porta 6543) com o usuário `api_site.<project-ref>`. Por isso o pool usa `prepare_threshold=None` e o `app.usuario_id` é sempre local à transação.

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

### Sem Docker: Postgres 17 instalado no Windows

Para rodar a API e o front locais sem Docker. Instale o Postgres 17 uma vez, já na porta e com a senha do Supabase local:

```powershell
winget install PostgreSQL.PostgreSQL.17 --override "--mode unattended --superpassword postgres --serverport 54322"
```

Depois, na raiz do repositório, crie (ou recrie) o banco com as migrations e o seed:

```powershell
powershell -ExecutionPolicy Bypass -File supabase\banco_local.ps1
```

E siga do passo 2 acima. Rodam a API e `uv run pytest` da API; ficam de fora o pgTAP, os testes de integração da carga (precisam do Storage) e o `docker build`, que o CI cobre. Nunca aponte a API com `AUTH_FAKE=1` para o banco de produção: o token fake se passaria por qualquer usuário.

Com `AUTH_FAKE=1` (só em `AMBIENTE=dev`), o token pode ser `fake:<object_id_entra>`. Usuários do seed:

| Usuário | Cliente | `Authorization` |
|---|---|---|
| Ana | Padrão LANN Alfa (empresas `…a1` e `…a2`) | `Bearer fake:0d000000-0000-4000-8000-00000000000a` |
| Bruno | Padrão LANN Beta (empresa `…b1`) | `Bearer fake:0d000000-0000-4000-8000-00000000000b` |

```bash
curl -H "Authorization: Bearer fake:0d000000-0000-4000-8000-00000000000a" \
  "http://127.0.0.1:8000/api/telas/fin-01?empresa=e1000000-0000-4000-8000-0000000000a1&mes=2026-09"

curl -H "Authorization: Bearer fake:0d000000-0000-4000-8000-00000000000a" \
  "http://127.0.0.1:8000/api/telas/visao-geral"     # sem empresa = todas do vínculo
```

Com `VISAO_GERAL_DEMO=1` (também só em `dev`), a Visão geral devolve as seções fictícias do mockup ([app/telas/visao_geral/demo.json](app/telas/visao_geral/demo.json), cópia da fixture do front). Cliente, datas e períodos continuam vindo do banco.

### Ligar o front local

O front (`overnex-frontend`) chama `/api/...` no próprio domínio, e a API não tem CORS. Em dev, o Vite repassa `/api` para a API com um proxy:

```ts
// vite.config.ts do front
server: { proxy: { '/api': 'http://127.0.0.1:8000' } }
```

O token do front em dev é `fake:<object_id_entra>` de um usuário do seed.

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
  core/config.py       Settings (DATABASE_URL, ENTRA_*, AMBIENTE, AUTH_FAKE, VISAO_GERAL_DEMO, VISAO_GERAL_CACHE_SEGUNDOS)
  core/db.py           pool psycopg (prepare_threshold=None) + uma transação por requisição
  seguranca/auth.py    get_usuario: valida o JWT do Entra (JWKS, iss, aud, exp, tid)
  seguranca/acesso.py  checagem em 6 etapas + seg.log_acesso
  sessao/router.py     GET /api/sessao: usuário, cliente, empresas, telas e períodos
  telas/contrato.py    ScreenPayload, Hero, Apoio, Visual, Alerta, Status
  telas/moldura.py     base camelCase, cliente e última carga (sessão e Visão geral)
  telas/fin_01.py      GET /api/telas/fin-01 (exemplo, sem regra de negócio)
  telas/visao_geral/   GET /api/telas/visao-geral (VG-01): contrato do front e rota
  dados/router.py      GET /api/dados/{tabela}: linhas das tabelas de dados do mart
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

### Endpoints do front: `/api/sessao` e `/api/telas/visao-geral`

Respondem em camelCase, no formato de `src/contracts/visaoGeral.ts` do front. Campo opcional sem valor sai do JSON (como `campo?:` no TypeScript); campo obrigatório que aceita nulo vai como `null` (`logoUrl`, `delta`, `modulo`).

- **`GET /api/sessao`** — etapas 1 a 3 da checagem (sem tela). Devolve `usuario`, `cliente` (com `slug`, para o front montar a URL), `papel`, `empresas` do vínculo (filtro "Empresa"), `telas` liberadas (códigos, para o menu) e `periodos` com dados: os meses com faturado no resumo mensal das tabelas `f_vendas_*` e o mês do último snapshot, do mais recente para o mais antigo (até 24). O primeiro é o padrão da Visão geral.
- **`GET /api/telas/visao-geral?mes=<AAAA-MM opcional>&empresa=<uuid opcional>`** — tela `VG-01`. Sem `empresa`, vale para todas as empresas do vínculo (etapa 5 exige ao menos uma). `mes` precisa ser um dos `periodos` de `/api/sessao` (senão 422, "Mês sem dados."); sem ele, cada seção usa o seu mês mais recente. A resposta pronta fica em memória por `VISAO_GERAL_CACHE_SEGUNDOS` (padrão 15 min), com chave no cliente, nas empresas, no mês e na última carga; o acesso é conferido e registrado a cada requisição. `dadosAte` é a `data_snapshot` e `atualizadoEm` é o `fim` (com fuso) da última fonte `ok` do cliente; os dois vêm `null` se nunca houve carga. As `secoes` vêm vazias até as regras dos indicadores existirem (ou da demo, com `VISAO_GERAL_DEMO=1`).
- `logoUrl` só sai quando `cfg.cliente.logo_url` é um caminho do próprio domínio (`/…`); fora disso vem `null`, porque a CSP do front recusa imagem de fora.

### Tabelas de dados: `GET /api/dados/{tabela}`

Entrega as linhas de uma tabela de dados do mart (as que a carga copia do Parquet, com as mesmas colunas).

```
GET /api/dados/f_estoque_nilo_maia?empresa=<uuid opcional>&apos=0&limite=1000
```

```json
{
  "tabela": "f_estoque_nilo_maia",
  "empresa_id": null,
  "atualizado_em": "2026-10-14",
  "colunas": [{"nome": "_linha", "tipo": "bigint"}, {"nome": "_empresa_id", "tipo": "uuid"}, {"nome": "EMPRESA", "tipo": "text"}],
  "linhas": [[1, "…", "1 Nilo Maia"]],
  "proximo": 1000
}
```

- **Paginação:** por `_linha`. Passe `proximo` em `apos` para a página seguinte; `null` = acabou. O limite máximo é 5000 linhas.
- **Acesso:** as mesmas 6 etapas das telas. Na etapa 4, a tabela precisa estar registrada para o cliente em `cfg.fonte_carga` e liberada a um perfil do usuário em `cfg.perfil_tabela`. Na etapa 6, `contem_dado_nominal` exige `ve_dado_nominal`.
- **Filtro:** o RLS só devolve linhas do cliente e das empresas do usuário. Linhas sem empresa (tabelas sem `coluna_empresa`) são vistas por todos os usuários do cliente.
- **Limites:** colunas e nomes de tabela nunca vêm da requisição para o SQL. Pelo caminho, só são aceitos nomes `^[a-z][a-z0-9_]*$`, e a tabela ainda precisa estar registrada para o cliente.

### Conexão em produção

Use o pooler do Supabase em modo transação (porta 6543) com o usuário `api_site.<project-ref>`. Por isso o pool usa `prepare_threshold=None` e o `app.usuario_id` é sempre local à transação.

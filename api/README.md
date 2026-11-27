# Overnex — API

FastAPI que serve o front: faz o login com e-mail e senha (Supabase Auth, pelas rotas `/api/auth/*`), confere o acesso no banco e devolve o JSON de cada tela pronto para desenhar. É a única peça que fala com o banco e com o Supabase Auth para o front, e nunca acessa o Supabase Storage.

## Subir local

Pré-requisitos: [uv](https://docs.astral.sh/uv/), [Supabase CLI](https://supabase.com/docs/guides/cli) e Docker (o `supabase start` usa Docker).

```bash
# 1. banco (na raiz do repositório)
supabase start
supabase db reset          # aplica migrations e seed (base fictícia "Padrão LANN")

# 2. API
cd api
cp .env.example .env       # AMBIENTE=dev e AUTH_FAKE=1 já vêm prontos; preencha SUPABASE_ANON_KEY
uv sync
uv run fastapi dev --port 8001   # http://127.0.0.1:8001/docs (o proxy do front aponta para a 8001)
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

A API só sobe com `SUPABASE_URL`, `SUPABASE_ANON_KEY` e `FRONT_URL` (com `AUTH_PROVEDOR=supabase`, o padrão), mesmo com o token fake. Para o login de verdade em dev, use o projeto do Supabase (a chave publishable está em Project Settings → API Keys).

Com `AUTH_FAKE=1` (só em `AMBIENTE=dev`), o token pode ser `fake:<id_autenticacao>` (`seg.usuario.id_autenticacao`). Usuários do seed:

| Usuário | Perfil | Cliente | `Authorization` |
|---|---|---|---|
| Ana | Cliente principal | Padrão LANN Alfa (empresas `…a1` e `…a2`) | `Bearer fake:0d000000-0000-4000-8000-00000000000a` |
| Bruno | Cliente usuário | Padrão LANN Beta (empresa `…b1`) | `Bearer fake:0d000000-0000-4000-8000-00000000000b` |
| Dora | Admin BORD | todos, sem vínculo (escolhe com `?cliente=`) | `Bearer fake:0d000000-0000-4000-8000-0000000000ad` |
| Caio | Consultor por carteira | Alfa e Beta (escolhe com `?cliente=`) | `Bearer fake:0d000000-0000-4000-8000-0000000000c0` |

```bash
curl -H "Authorization: Bearer fake:0d000000-0000-4000-8000-00000000000a" \
  "http://127.0.0.1:8001/api/telas/fin-01?empresa=e1000000-0000-4000-8000-0000000000a1&mes=2026-09"

curl -H "Authorization: Bearer fake:0d000000-0000-4000-8000-00000000000a" \
  "http://127.0.0.1:8001/api/telas/visao-geral"     # sem empresa = todas do vínculo

# a admin abre qualquer cliente pelo slug, e a Gestão
curl -H "Authorization: Bearer fake:0d000000-0000-4000-8000-0000000000ad" \
  "http://127.0.0.1:8001/api/telas/visao-geral?cliente=padrao-lann-beta"
curl -H "Authorization: Bearer fake:0d000000-0000-4000-8000-0000000000ad" \
  "http://127.0.0.1:8001/api/admin/gestao"
```

Com `VISAO_GERAL_DEMO=1` (também só em `dev`), a Visão geral devolve as seções fictícias do mockup ([app/telas/visao_geral/demo.json](app/telas/visao_geral/demo.json), cópia da fixture do front). Cliente, datas e períodos continuam vindo do banco.

### Ligar o front local

O front (`overnex-frontend`) chama `/api/...` no próprio domínio, e a API não tem CORS. Em dev, o Vite repassa `/api` para a API com um proxy:

```ts
// vite.config.ts do front
server: { proxy: { '/api': 'http://127.0.0.1:8000' } }
```

O token do front em dev é `fake:<id_autenticacao>` de um usuário do seed, ou o `accessToken` de `POST /api/auth/entrar` (ver [Login](#login-com-e-mail-e-senha-apiauth)). Com o proxy do Vite, o cookie do refresh fica no domínio do front, como em produção; em `http://localhost`, use `COOKIE_SEGURO=false`.

## Testes e qualidade

```bash
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pytest               # precisa do supabase start + db reset
uv run pytest -m "not db"   # só os testes que não usam banco
```

Os testes marcados `db` conectam como `api_site` em `127.0.0.1:54322` (sobrescreva com `DATABASE_URL`) e usam `postgres` para conferir o log de acesso e o SQL do CLI de cadastro (`DATABASE_URL_ADMIN`). Os testes das rotas `/api/auth/*` não precisam de banco nem do Supabase: o GoTrue é simulado com `httpx.MockTransport`.

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

Sem `AMBIENTE` definido, a API sobe como `prod`: exige `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `FRONT_URL` e `PROXY_SEGREDO` (ver [Segredo do proxy](#segredo-do-proxy)), e recusa `AUTH_FAKE` e `COOKIE_SEGURO=false`. Não passe `SUPABASE_SERVICE_ROLE_KEY` nem `DATABASE_URL_ADMIN` para o contêiner: só o CLI de cadastro e o job de convites usam a chave secreta. Para o convite pela Gestão sair na hora, a API recebe `GITHUB_CONVITES_TOKEN` e `GITHUB_CONVITES_REPO` (ver [Convite pela Gestão](#convite-pela-gestão-fila--job)).

Atrás de proxy, o limite de tentativas usa o IP do cliente que o uvicorn lê do `X-Forwarded-For`. O `--proxy-headers` do Dockerfile só confia no proxy de `127.0.0.1`: defina `FORWARDED_ALLOW_IPS` com o IP (ou a faixa) do proxy, ou `*` se só o proxy passar da API (como no App Service com o segredo do proxy, abaixo). Sem isso, todos os usuários aparecem com o IP do proxy e dividem o mesmo limite por IP.

## Publicar no Azure App Service

O front fica na Cloudflare: um Worker serve o site e repassa `/api/*` ao App Service (`https://<domínio padrão do app>`), com o cabeçalho `X-Proxy-Segredo` e o `X-Forwarded-For` trocado pelo IP de quem acessa (`CF-Connecting-IP`). Para o navegador, front e API continuam no mesmo domínio: o cookie `ovx_refresh` (`Path=/api/auth`, `SameSite=Strict`) fica no domínio do front, e a API segue sem CORS.

### 1. Criar o app (uma vez)

Portal do Azure → Criar um recurso → **Aplicativo Web**:

- **Publicar:** Código. **Pilha de runtime:** Python 3.12. **Sistema operacional:** Linux. **Região:** Brazil South (perto do banco, em São Paulo).
- **Plano:** **B1** para uso. **F1 só para teste:** não tem Always On (o app dorme depois de uns 20 min parado e o primeiro pedido espera ele subir de novo) e tem 60 min de CPU por dia (passou disso, o app para até o dia seguinte).
- **Implantação contínua:** desligada. Quem publica é o workflow do repositório; o Centro de Implantação criaria outro workflow.
- **Nome do host padrão exclusivo:** ligado (o padrão), o endereço fica `<app>-<hash>.brazilsouth-01.azurewebsites.net`, não `<app>.azurewebsites.net`. O Worker usa o **Domínio padrão** que aparece na Visão geral do app.

Depois de criado:

- **Configurações → Configuração → Configurações gerais:** "Somente HTTPS" ligado, TLS mínimo 1.2, FTP desabilitado, autenticação básica do SCM e do FTP desligadas (o login do workflow é por OIDC e não precisa delas) e **Always On** ligado (B1). O comando de inicialização o workflow grava (item 3).
- **Monitoramento → Verificação de integridade** (opcional): caminho `/health`.
- **Monitoramento → Logs do Serviço de Aplicativo:** "Log de aplicativo" em Sistema de arquivos, para ver a saída do uvicorn em **Fluxo de log**.

### 2. Variáveis do app

Em **Configurações → Variáveis de ambiente → Configurações de aplicativo**. Aqui só os nomes; os valores ficam no portal. **Crie todas antes da primeira publicação:** sem elas a API não sobe e o workflow falha no `/health`.

| Variável | O que é |
|---|---|
| `SCM_DO_BUILD_DURING_DEPLOYMENT` | `true`: o App Service instala o `requirements.txt` do pacote a cada publicação |
| `AMBIENTE` | `prod` |
| `DATABASE_URL` | o role `api_site` pelo pooler em modo transação (ver [Conexão em produção](#conexão-em-produção)) |
| `SUPABASE_URL` | URL do projeto do Supabase |
| `SUPABASE_ANON_KEY` | a chave **publishable** |
| `FRONT_URL` | URL do front, igual à Site URL do Supabase Auth |
| `PROXY_SEGREDO` | o mesmo segredo do Worker, com pelo menos 32 caracteres (`python -c "import secrets; print(secrets.token_urlsafe(48))"`) |
| `GITHUB_CONVITES_TOKEN` e `GITHUB_CONVITES_REPO` | para o convite pela Gestão sair na hora (ver [Convite pela Gestão](#convite-pela-gestão-fila--job)) |

Opcionais, com padrão no código ([app/core/config.py](app/core/config.py)): `ADMIN_DOMINIOS`, `AUTH_CONVITE_HORAS`, `AUTH_REFRESH_SEGUNDOS`, `GITHUB_CONVITES_REF`, `DB_POOL_MIN`, `DB_POOL_MAX`, `DB_TESTE_PARADA_SEGUNDOS` e `VISAO_GERAL_CACHE_SEGUNDOS`.

**Nunca** ponha no App Service a `SUPABASE_SERVICE_ROLE_KEY` nem a `DATABASE_URL_ADMIN` (são só do CLI de cadastro e dos jobs). `AUTH_FAKE`, `VISAO_GERAL_DEMO`, `PAINEIS_A_DECIDIR` e `COOKIE_SEGURO=false` a API recusa fora de dev. `WEBSITES_PORT` não precisa: nas pilhas Python o App Service espera a porta 8000, a mesma do comando. `FORWARDED_ALLOW_IPS` também não: vai no comando.

### 3. Comando de inicialização

```
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips="*" --no-server-header
```

É o `CMD` do [Dockerfile](Dockerfile) com duas diferenças: `python -m uvicorn`, porque o App Service põe as dependências instaladas no `PYTHONPATH`, não necessariamente no `PATH`; e `--forwarded-allow-ips="*"` (abaixo). Fica em `COMANDO_INICIAL` no [workflow](../.github/workflows/deploy-api.yml), que o grava no app a cada publicação: mudar no portal só vale até a próxima. Para um teste à mão, cole em Configuração → Configurações gerais → Comando de inicialização.

Um processo só (sem `--workers`) e uma instância só (sem escalar): o limite de tentativas do login é em memória, por processo, e cada processo abriria o seu pool de `DB_POOL_MIN` conexões.

**Por que `*` é seguro aqui.** Com `*`, o uvicorn usa o primeiro IP do `X-Forwarded-For`. O App Service acrescenta no fim da lista o IP de quem conectou a ele (a Cloudflare), e o Worker sobrescreve o cabeçalho com o `CF-Connecting-IP`; então o primeiro é o de quem acessa. Quem chamar o `*.azurewebsites.net` direto pode forjar o cabeçalho, mas sem o segredo leva 403 antes de qualquer rota (só `/health` responde, e ele não usa o IP).

### 4. Segredo do proxy

- A API exige o cabeçalho `X-Proxy-Segredo` igual a `PROXY_SEGREDO` ([app/core/proxy.py](app/core/proxy.py)), comparado em tempo constante. Sem ele, diferente ou repetido: `403 {"detail": "Acesso negado."}`, sem dizer o motivo, inclusive em rota que não existe. `/health` fica livre.
- `PROXY_SEGREDO` vazio desliga a checagem (dev, testes, proxy do Vite). Com `AMBIENTE=prod`, vazio ou com menos de 32 caracteres, a API não sobe.
- **Trocar o segredo:** a API aceita um só, então troque no Worker e no App Service em seguida; entre um e outro, os pedidos levam 403.

### 5. Publicação pelo GitHub Actions

[.github/workflows/deploy-api.yml](../.github/workflows/deploy-api.yml), a cada push na `main` que mexa em `api/` (ou em Actions → Publicar API → Run workflow, só a partir da `main`):

1. gera o `requirements.txt` com `uv export --no-dev --no-hashes --frozen` (não fica no repositório; está no `.gitignore`);
2. monta o pacote só com `app/` e o `requirements.txt`;
3. entra no Azure por OIDC e publica com `azure/webapps-deploy`, gravando o comando de inicialização;
4. espera o `/health` responder 200 (até uns 5 min: o App Service instala as dependências e reinicia).

**Autenticação: OIDC** (federated credential). O GitHub troca um token de curta duração por acesso ao Azure a cada execução, e só para o environment `producao` deste repositório; nada que vaze dá acesso depois. O publish profile, a alternativa, é uma senha que não vence e exige ligar a autenticação básica do SCM, que o Azure deixa desligada nos apps novos.

Configurar uma vez, no Cloud Shell do portal (Bash):

```bash
APP=<nome-do-app>; RG=<grupo-de-recursos>; REPO=<dono>/<repositorio>
SUB=$(az account show --query id -o tsv)
TENANT=$(az account show --query tenantId -o tsv)
CLIENT=$(az ad app create --display-name overnex-deploy-api --query appId -o tsv)
az ad sp create --id "$CLIENT"
# só o grupo do app: publica e configura os apps dele, mais nada
az role assignment create --assignee "$CLIENT" --role "Website Contributor" \
  --scope "/subscriptions/$SUB/resourceGroups/$RG"
az ad app federated-credential create --id "$CLIENT" --parameters "{
  \"name\": \"github-producao\",
  \"issuer\": \"https://token.actions.githubusercontent.com\",
  \"subject\": \"repo:$REPO:environment:producao\",
  \"audiences\": [\"api://AzureADTokenExchange\"]
}"
echo "AZURE_CLIENT_ID=$CLIENT  AZURE_TENANT_ID=$TENANT  AZURE_SUBSCRIPTION_ID=$SUB"
```

Sem permissão para registrar aplicativos no Entra ID, dá para usar uma identidade gerenciada atribuída pelo usuário no lugar (`az identity create` e `az identity federated-credential create`, com o mesmo `subject`); o `AZURE_CLIENT_ID` passa a ser o da identidade.

No GitHub (Settings do repositório):

- **Environments → New environment:** `producao`, com "Deployment branches and tags" só na `main`. O nome entra no `subject` acima.
- **Secrets** (do environment `producao`): `AZURE_CLIENT_ID`, `AZURE_TENANT_ID` e `AZURE_SUBSCRIPTION_ID`. Não são senhas, mas ficam fora do código.
- **Variables** (do environment ou do repositório): `AZURE_WEBAPP_NAME` e `AZURE_RESOURCE_GROUP`.

### 6. Ordem de uma publicação

1. **Migrations antes da API.** Se a API nova depende de uma migration, uma pessoa a aplica com `supabase db push` ([supabase/README.md](../supabase/README.md)) antes de o código chegar à `main`. Enquanto isso a API antiga roda contra o banco novo: migrations que só acrescentam não a quebram.
2. **Código na `main`** (PR `release/X.Y.Z` → `main`, ver [CONTRIBUTING.md](../CONTRIBUTING.md)): o workflow publica.
3. **Conferir:**
   - `curl https://<domínio padrão do app>/health` → `{"status":"ok"}`. 200 só com o banco respondendo; 503 "Banco indisponível." aponta para a `DATABASE_URL` ou o pooler. O `/health` não passa pelo Worker (ele só repassa `/api/*`).
   - Pelo Worker, sem login: `curl -i https://<front>/api/sessao` → 401 quer dizer que o segredo chegou; 403 `{"detail":"Acesso negado."}` quer dizer que o Worker não mandou o `X-Proxy-Segredo` certo.
   - Se o app não subir: Monitoramento → Fluxo de log. Configuração inválida aparece como `ValidationError`, com o nome da variável e sem o valor.

## Como está organizada

```
app/
  main.py              cria o app; sem CORS; erros sem detalhe interno; GET /health
  core/config.py       Settings (DATABASE_URL, AUTH_PROVEDOR, SUPABASE_*, FRONT_URL, COOKIE_SEGURO, ENTRA_*, AMBIENTE, AUTH_FAKE…),
                       ConfigCadastro (só o CLI: chave secreta e DATABASE_URL_ADMIN) e
                       ConfigConvites (só o job de convites: chave secreta e DATABASE_URL_CARGA)
  core/db.py           pool psycopg (prepare_threshold=None, autocommit; o begin vai junto com
                       a primeira consulta) + uma transação por requisição
  seguranca/auth.py    get_usuario: valida o JWT do Supabase (JWKS ES256, iss, aud, exp, sub, role)
                       ou, com AUTH_PROVEDOR=entra, o do Entra (JWKS, iss, aud, exp, tid)
  auth/router.py       POST /api/auth/*: entrar, renovar, sair, esqueci-senha, definir-senha
  auth/gotrue.py       cliente HTTP do Supabase Auth (GoTrue), com timeout
  auth/limite.py       limite de tentativas em memória (e-mail + IP e só IP)
  auth/cadastro.py     depois do login, confere o usuário ativo em seg.usuario (+ seg.log_acesso)
  cli/convidar_usuario.py  cadastro de usuário pela LANN (convite no Supabase + seg.*)
  cli/enviar_convites.py   job do GitHub Actions: manda os convites que a Gestão pôs na fila
  seguranca/acesso.py  checagem em 6 etapas (com o cliente escolhido) + seg.log_acesso
  seguranca/perfis.py  os 4 perfis fixos (Admin BORD, Consultor, Cliente principal e usuário)
  seguranca/setores.py painéis de um setor (EST-01: Distribuidora) e os textos do escopo
  sessao/router.py     GET /api/sessao: usuário, perfil, clientes que a pessoa pode abrir e,
                       do cliente escolhido, unidades, a árvore Negócios, telas e períodos
  negocios/            a árvore setor › grupo › unidade (arvore.py) e GET /api/negocios, os
                       valores da bandeja (faturado do mês por setor, grupo e unidade)
  admin/               Gestão (/api/admin/*, só o Admin BORD): empresas, painéis ligados,
                       matriz perfil × painel, pessoas, preferências, feriados e cargas;
                       convites.py grava o pedido de convite na fila e aciona o job
  telas/contrato.py    ScreenPayload, Hero, Apoio, Visual, Alerta, Status
  telas/moldura.py     base camelCase, cliente e última carga (sessão e Visão geral)
  telas/fin_01.py      GET /api/telas/fin-01 (exemplo, sem regra de negócio)
  telas/visao_geral/   GET /api/telas/visao-geral (VG-01): contrato do front e rota
                       e PUT .../preferencias/{id} (escolhas salvas do cliente)
  telas/paineis/       GET /api/telas/vendas|equipe-comercial|clientes (COM-01 a 03) e
                       resultado|receber-pagar|fluxo-caixa (FIN-02 a 04) e pessoas
                       (PES-01), e PUT das provisões do fluxo de caixa: contrato
                       PainelPayload, uma tela por módulo, demo/ com as fixtures do front
  telas/paralelo.py    consultas da mesma tela em paralelo, cada uma numa conexão
  dados/router.py      GET /api/dados/{tabela}: linhas das tabelas de dados do mart
  farol/               status(valor, faixa), função pura
  pdf/                 router vazio (TODO)
scripts/export_openapi.py
```

### Fluxo de uma requisição de tela

1. `get_usuario` valida o access token e extrai o `sub` (id do usuário no Supabase Auth).
2. `get_sessao` abre **uma** transação. A checagem chama `seg.contexto_acesso(sub, tela, slug)` (migrations 16 e 26), que numa ida ao banco traduz o `sub` no `seg.usuario.id` (pela coluna `id_autenticacao`), escolhe o cliente (o do `?cliente=<slug>`, se a pessoa pode abri-lo; sem slug, o único vínculo de quem tem um só), executa `set_config` de `app.usuario_id` e de `app.cliente_id` e devolve o perfil, os vínculos, se a tela está liberada no cliente escolhido e as empresas dele. Daí em diante o RLS do banco só mostra o cliente escolhido.
3. `exigir_tela("FIN-01")` roda as 6 etapas (usuário ativo → cliente escolhido → papel → tela ligada no cliente e liberada ao perfil → empresa no vínculo → dado nominal). O `cliente` e a `empresa` da query são só uma escolha entre o que o banco libera à pessoa: fora disso, 403. O Admin BORD (`seg.usuario.perfil = 'admin'`) escolhe qualquer cliente ativo, vê toda tela ligada nele, todas as empresas e o dado nominal; sem `cliente`, não abre tela nenhuma.
4. Toda decisão grava em `seg.log_acesso`. O registro fica pendente na sessão e vai ao banco junto com o commit, na mesma ida (pipeline do psycopg). Na negação, a transação faz commit só do log e a resposta é `403 {"detail": "Acesso negado."}`, sem dizer a etapa; em erro inesperado, rollback (e o log daquela requisição não fica, como antes).
5. Com sucesso, a tela monta o `ScreenPayload`; `atualizado_em` é a `data_snapshot` da última carga `ok` de lançamentos do cliente (ou `null`).

### Endpoints do front: `/api/sessao` e `/api/telas/visao-geral`

Respondem em camelCase, no formato de `src/contracts/visaoGeral.ts` do front. Campo opcional sem valor sai do JSON (como `campo?:` no TypeScript); campo obrigatório que aceita nulo vai como `null` (`logoUrl`, `delta`, `modulo`).

- **`GET /api/sessao?cliente=<slug opcional>`** — etapas 1 a 3 da checagem (sem tela). Devolve sempre `usuario` (nome e e-mail de `seg.usuario`; `nome` pode vir `null`), `perfil` (`{id, rotulo}`: `admin`, `consultor`, `cliente_principal` ou `cliente_usuario`), `gestao` (`true` para o Admin BORD e o Consultor por carteira: o front mostra a Gestão; a do consultor é só de leitura) e `clientes` (os que a pessoa pode abrir, com `slug`, `nome`, `sigla` e `logoUrl`: o seletor de empresa; para o admin, todos os ativos). Do **cliente escolhido** vêm `cliente` (com `slug`, para o front montar a URL), `papel`, `empresas` (as **unidades** liberadas, `{id, nome}`, na ordem da árvore: setor pela ordem de `cfg.setor`, grupo e nome, com ordem natural), `negocios` (a árvore da bandeja "Negócios", ver abaixo), `telas` liberadas (códigos, para o menu: ligadas no cliente, que valem nos setores das unidades liberadas e em algum perfil da pessoa; para o admin, as ligadas que valem nos setores das unidades do cliente) e `periodos` com dados: os meses com faturado no resumo mensal das tabelas `f_vendas_*` e o mês do último snapshot, do mais recente para o mais antigo (até 24). O primeiro é o padrão da Visão geral. **Sem `cliente`:** quem tem um cliente só já recebe esse; o admin, quem tem mais de um e quem não tem nenhum recebem `cliente: null`, `papel: null`, as três listas vazias e `negocios: null`. Quem está ativo e sem cliente nenhum recebe o `aviso` "Sua conta ainda não tem empresa. Fale com a BORD para liberar o acesso." (nos outros casos, `null`). `cliente` que a pessoa não pode abrir: 403. As consultas vão numa ida ao banco só, e a sessão agenda a **pré-montagem**, em segundo plano, da Visão geral e dos painéis liberados no cliente escolhido, no mês padrão e sem filtro ([app/telas/preparo.py](app/telas/preparo.py)): a primeira tela que o front abre já está pronta, ou espera a montagem em andamento em vez de começar outra. A pré-montagem faz a mesma checagem de acesso da rota, mas não grava em `seg.log_acesso` (o usuário ainda não abriu a tela); roda uma vez por usuário e cliente a cada metade do prazo do cache e não roda com o cache desligado.
- **`cliente=<slug>` vale em toda rota de tela** (`/api/telas/*`, GET e PUT) e em `/api/dados/{tabela}`. Opcional para quem tem um cliente só; obrigatório para o admin e para quem tem mais de um (sem ele, 403).
- **Unidades e a bandeja "Negócios"** (migration 35, [app/negocios/](app/negocios/)): a unidade é o `cfg.empresa` (Lupo 1), com setor, segmento, grupo e endereço; o cliente (`cfg.cliente`) é a "empresa" que se escolhe no front. `negocios` de `/api/sessao` é `{setores: [{id, rotulo, grupos: [{id, rotulo, unidades: [id]}]}], telasPorSetor: {"EST-01": ["distribuidora"]}}`: setor pelo id de `cfg.setor` (unidade sem setor em `{"id": "sem-setor", "rotulo": "Sem setor"}`, no fim), grupo com id `"<setor>/<grupo>"` (sem grupo, `"<setor>/sem-grupo"` e o rótulo "Sem grupo") e toda unidade de `empresas` em exatamente um grupo; `telasPorSetor` só com as telas de `telas` que são de um setor. Vem `null` quando as unidades formam um negócio só (um par setor + grupo, contando null como valor) ou não há cliente escolhido: o front mostra o chip "Unidade".
- **`GET /api/negocios?cliente=<slug>&mes=<AAAA-MM opcional>`** — os valores da bandeja, pedidos quando ela abre: `{rotulo, setores, grupos, unidades}`, cada mapa por id com um `Valor` em `brl`. O valor é o faturado do mês (medida `faturado` das tabelas `f_vendas_*` no resumo mensal), do setor, do grupo e da unidade **inteiros**, sem olhar o que está marcado; unidade sem venda no mês vale 0, e o faturado sem unidade não entra. Sem `mes`, o mês mais recente dos `periodos` da sessão; fora deles, 422 "Mês sem dados.". `rotulo`: "Valores: faturado de 1º a 12/10" no mês da última carga, "Valores: faturado em setembro de 2026" num mês fechado. Mapas vazios (com `rotulo` "Sem faturado para mostrar.") com um negócio só, sem cliente escolhido ou sem faturado por unidade. Só as unidades liberadas; os erros de acesso são os da sessão. Uma ida ao banco depois da checagem.
- **`?unidades=<id>,<id>`** (no lugar de `?empresa=`, desde 27/11/2026) — em toda rota de tela: a Visão geral, os painéis, os nomes por cartão e as gravações de preferência e de provisões. Os ids das unidades marcadas, separados por vírgula (`%2C`). Sem o parâmetro, todas as liberadas; marcar todas é o mesmo que não mandar (as linhas sem unidade dos dados também entram). Unidade fora do vínculo (ou de outro cliente): 403 genérico (etapa 5). Id que não é UUID com hífens, lista vazia ou mais de 500 ids: 422 `{"detail": "Filtro inválido."}`. A tela soma só as unidades pedidas (`ContextoConsolidado.unidades` e `empresas`), e o cache entra com a lista. No log de acesso, a coluna de empresa leva a unidade quando é uma só; com mais de uma, o motivo leva a contagem. As provisões do Fluxo de caixa são salvas por escopo: `todas`, o id da unidade, ou `u<resumo dos ids>` para várias. `/api/dados/*` e a FIN-01 continuam com `?empresa=`.
- **`GET /api/telas/visao-geral?mes=<AAAA-MM opcional>&unidades=<ids opcional>`** — tela `VG-01`. Sem `unidades`, vale para todas as unidades do vínculo (etapa 5 exige ao menos uma). Cada `Secao` tem o campo opcional `escopo` ("Conta só a Distribuidora"), para as seções dos módulos de um setor quando existirem (só vêm se alguma unidade pedida for do setor); hoje nenhuma seção é de setor. `mes` precisa ser um dos `periodos` de `/api/sessao` (senão 422, "Mês sem dados."); sem ele, cada seção usa o seu mês mais recente. A resposta pronta fica em memória por `VISAO_GERAL_CACHE_SEGUNDOS` (padrão 1 h), com chave no cliente, nas empresas, no mês, na última carga e na versão do cadastro do cliente (parâmetros, inclusive as escolhas salvas, feriados, fontes, plano de contas, de-para da DRE e saldo inicial de caixa, lidos na pré-leitura): carga nova ou cadastro mudado valem na hora, e só uma correção feita à mão no `mart`, sem carga nova, espera o prazo; o acesso é conferido e registrado a cada requisição. Duas requisições da mesma chave ao mesmo tempo montam a resposta uma vez só (a segunda espera a primeira). Ao montar, última carga, cliente, fontes, feriados e parâmetros vêm numa ida ao banco ([app/telas/preleitura.py](app/telas/preleitura.py)), e as seções rodam em três ramos paralelos (Vendas e Custos, Resultado, Financeiro), cada um com uma conexão própria do mesmo usuário: o tempo é o do ramo mais lento. O pool mantém 8 conexões abertas (`DB_POOL_MIN=8`; abrir uma nova custa ~0,4 s) e vai até 20. `dadosAte` é a `data_snapshot` e `atualizadoEm` é o `fim` (com fuso) da última fonte `ok` do cliente; os dois vêm `null` se nunca houve carga. As `secoes` vêm vazias até as regras dos indicadores existirem (ou da demo, com `VISAO_GERAL_DEMO=1`).
- **`PUT /api/telas/visao-geral/preferencias/{id}`**, corpo `{"valor": "3"}`: grava a escolha de um seletor da Visão geral para o cliente inteiro (hoje só `linhas-custo`, o seletor "Linhas de custo" da seção Custos: `auto` ou de `1` a `7`). Responde 204 sem corpo; 404 `Preferência não encontrada.` para id desconhecido; 422 `Opção inválida.` para valor fora das opções; 403 `Você não pode mudar esta escolha.` para papel fora de `gestor` e `admin` (provisório); 403 genérico sem acesso à `VG-01`. A escolha vai em `cfg.parametro_cliente` (chave `preferencia.<id>`, `vigente_de` = dia da escolha; a mais recente vale) e a troca em `seg.log_alteracao` (migration 19). A rota faz o commit antes de responder (o `get_sessao` só confirma depois que a resposta sai) e apaga as respostas prontas da Visão geral do cliente, em todos os meses e empresas: o front pede a tela de novo logo depois do 204. As escolhas também entram na chave do cache da tela.
- `logoUrl` só sai quando `cfg.cliente.logo_url` é um caminho do próprio domínio (`/…`); fora disso vem `null`, porque a CSP do front recusa imagem de fora.

### Painéis: Vendas, Equipe comercial, Clientes e atendimentos, Resultado, Receber e pagar, Fluxo de caixa e Pessoas

Contrato `PainelPayload` (espelho de `src/contracts/painel.ts` do front), em [app/telas/paineis/contrato.py](app/telas/paineis/contrato.py). Mesmo padrão da Visão geral: `unidades` e `mes` opcionais, 403 genérico, 422 "Mês sem dados." e resposta pronta em memória por `VISAO_GERAL_CACHE_SEGUNDOS` (chave com os filtros também). A seção Resultado da Visão geral e o painel Resultado leem o realizado e o orçado (todos os cenários) numa ida ao banco, pela mesma função (`consultas`, em [app/telas/visao_geral/resultado.py](app/telas/visao_geral/resultado.py)).

| Rota | Tela | Filtros (`?{id}={opção}`) |
|---|---|---|
| `GET /api/telas/vendas` | `COM-01` | `canal`, `linha` |
| `GET /api/telas/equipe-comercial` | `COM-02` (exige perfil com `ve_dado_nominal`: mostra nomes de vendedores) | `canal`, `supervisor` |
| `GET /api/telas/clientes` | `COM-03` | nenhum |
| `GET /api/telas/resultado` | `FIN-02` | `visao` (`mes`, `ano`, `projecao`), `cenario` (só com mais de um no orçamento); e `comparar` (`orcado` ou `mes-anterior`), que vem do chip global do front e não aparece em `filtros` |
| `GET /api/telas/receber-pagar` | `FIN-03` (exige `ve_dado_nominal`: mostra clientes, fornecedores e vendedores) | `vendedor` (se os títulos a receber têm vendedor), `atraso` (`ate-30`, `31-90`, `mais-90`; só "Quem cobrar") |
| `GET /api/telas/fluxo-caixa` | `FIN-04` (projeção de 60 dias e DFC realizado: `f_fluxo_*` pelo plano de contas da LANN, com o saldo de `cfg.saldo_inicial`; títulos de `f_receitas_*` e `f_despesas_*`) | `visao` (só o realizado; `mes`: o mês e os 6 meses até ele na tabela; `ano`: de janeiro até o mês, com a coluna do acumulado; `historico`, "Desde abril de 2025": do primeiro mês com caixa até o mês, com o acumulado de tudo, só quando o caixa começa antes de janeiro); `projecao` (`lancados`, padrão, ou `provisoes`; o filtro traz `cartao: "saldo-projetado"`, e o front mostra a chave no cabeçalho desse cartão). Sem `comparar`. A projeção sempre parte do último dia com caixa: `mes` e `visao` não a mudam |
| `PUT /api/telas/fluxo-caixa/cartoes/provisoes/periodo` | `FIN-04`, quem vê a tela (sem restrição de papel) | corpo `{"periodo": "12-meses"}` (`ultimo-mes`, `3-meses`, `6-meses` ou `12-meses`); `unidades` opcional |
| `PUT /api/telas/fluxo-caixa/cartoes/provisoes/grupos/{grupo}` | idem | corpo `{"ligado": true, "valor": 800000}` (`valor` em reais ou `null`, que volta ao sugerido); `unidades` opcional |
| `GET /api/telas/estoque` | `EST-01` (só com unidade liberada do setor Distribuidora; soma só as unidades pedidas da Distribuidora e manda `escopo` `{"setores": ["distribuidora"], "rotulo": "Este painel soma só a Distribuidora"}`; sem nenhuma unidade pedida da Distribuidora, 200 com `escopo`, cartões e KPIs vazios e o `aviso` "Estoque é um painel da Distribuidora. Volta quando ela estiver marcada em Negócios."; o retrato do dia: sem `mes`, com `frescor` pronto). O `aviso` ("Retrato do estoque de hoje, 12/10, a custo. …"), 4 `kpis` (valor em estoque, acima do ideal, abaixo do mínimo, cobertura média) e os cartões `matriz-bcg` (`bcg`), `acoes-bcg`, `real-ideal` (seção › família › produto contra o ideal, com `aberto` na seção do filtro), `saude-estoque` (zerado, abaixo do mínimo, ideal, excesso e parado), `criticos` e `excesso-parados` (`tabela` com `quadrante`, `barra`, `link` e `ordem` nas células, `ordenavel` nas colunas, `limite`, `rodape` e `link`). Regra (provisória) em [app/telas/paineis/estoque.py](app/telas/paineis/estoque.py), `montar_conteudo` | `secao` (do cadastro, `en_secao`; filtra tudo, e o real contra ideal vem aberto nela), `classe` (`estrela`, `vaca-leiteira`, `interrogacao`, `abacaxi`; na matriz só apaga pontos, sem mudar as medianas, e filtra as duas listas) e `lista` (`tudo`, `excesso`, `parados`; só a lista do dinheiro preso, com `cartao: "excesso-parados"`) |
| `GET /api/telas/pessoas` | `PES-01` (dado de folha, sem nomes: abre para os perfis marcados na matriz da Gestão; não exige `ve_dado_nominal`) | nenhum (o setor se escolhe dentro do cartão "Admissões, desligamentos e turnover", em `visoes`) |
| `GET /api/telas/pessoas/cartoes/cargos-salarios/nomes` | `PES-01` com `ve_dado_nominal` (a chave "vê nomes" da matriz da Gestão): "Cargos e salários" com uma linha por pessoa (`TabelaNomes`) | `mes` e `unidades`, como no painel |

- As opções de cada filtro saem dos dados (cadastro dos vendedores, linhas das vendas) e vêm em `filtros`, com `padrao` (`todos`/`todas`); opção fora da lista → 422 "Filtro inválido.".
- **Provisões do Fluxo de caixa** (`PUT .../cartoes/{cartao}/periodo` e `.../grupos/{grupo}`, regras em [app/telas/paineis/provisoes.py](app/telas/paineis/provisoes.py) e [projecao.py](app/telas/paineis/projecao.py)): respondem 204 sem corpo; 404 `Este cartão não tem provisões.` (tela ou cartão sem provisões) ou `Grupo de provisão não encontrado.`; 422 `Período inválido.` ou `O valor precisa ser zero ou mais.` (negativo, texto ou booleano); 403 `Você não pode mudar as provisões deste painel.` sem a `FIN-04` no perfil (as outras negações, o 403 genérico). As escolhas vão em `cfg.parametro_cliente`, como as preferências da Visão geral (chaves `preferencia.provisao.<empresa ou todas>.periodo` e `.grupo.<id>`; a migration 19 já deixa o `api_site` gravá-las), com quem digitou o valor e quando, e a troca em `seg.log_alteracao`. Valem para todos os usuários do cliente. A rota confirma antes de responder e apaga as respostas prontas de todas as telas do cliente; a versão do cadastro (parâmetros) também troca a chave do cache.
- Antes da primeira carga do cliente: 200 com `dadosAte` e `atualizadoEm` nulos e tudo vazio. O mesmo no Resultado sem `f_lancamentos_*`, no Receber e pagar sem `f_receitas_*` nem `f_despesas_*`, no Fluxo de caixa sem `f_fluxo_*` (ou sem caixa nela) e em Pessoas sem `f_folha_*`. Nesses casos, filtro da tela vindo na consulta → 422; `comparar` vale sempre.
- Cartões "A decidir" do mockup (no Resultado: ponte do EBITDA e maiores desvios; a tabela financeira e o nível 3 da DRE saem sempre desde 30/10/2026) só saem com `PAINEIS_A_DECIDIR=1` (só dev), também no demo.
- Cartão sem fonte de dados não sai (ex.: sem meta de clientes, sem o cartão de fatores; sem o papel `vendedor` nas vendas, sem canais nem Equipe).
- As regras (provisórias) estão no topo de cada módulo: [vendas.py](app/telas/paineis/vendas.py), [equipe.py](app/telas/paineis/equipe.py), [clientes.py](app/telas/paineis/clientes.py), [resultado.py](app/telas/paineis/resultado.py), [receber_pagar.py](app/telas/paineis/receber_pagar.py), [fluxo_caixa.py](app/telas/paineis/fluxo_caixa.py), [pessoas.py](app/telas/paineis/pessoas.py) e [base.py](app/telas/paineis/base.py) (fontes, papéis e o mês).
- Nomes por cartão (`NOMES` em [router.py](app/telas/paineis/router.py); hoje só "Cargos e salários" do Pessoas): o cartão traz `nomes: {"rotulo": "Mostrar nomes"}` só para quem vê dado nominal na tela, e o `ve_dado_nominal` entra na chave do cache. Ligada a chave, o front pede `GET /api/telas/{tela}/cartoes/{id}/nomes`, que confere de novo: 404 "Este cartão não tem nomes." para cartão fora de `NOMES`; 403 "Você não pode ver os nomes deste cartão." para quem tem a tela sem dado nominal (as outras negações são o 403 genérico); `Cache-Control: no-store` e nada em cache no back. Cada pedido vai para `seg.log_acesso` com `codigo_tela` = `PES-01:cargos-salarios:nomes` e os filtros no `motivo` (`filtros: mes=…`). A folha da tabela é a mesma leitura do painel (números e rótulos iguais); os nomes vêm na mesma ida ao banco.
- `explicacao` de cada cartão (o botão "i" do front): texto fixo por cartão em [explicacoes.py](app/telas/paineis/explicacoes.py); cartão sem texto sai sem a chave.
- Com `VISAO_GERAL_DEMO=1` (só dev), as rotas devolvem as fixtures do mockup (`app/telas/paineis/demo/`), com o cliente do usuário e os filtros conferidos contra os da fixture.

### Login com e-mail e senha: `/api/auth/*`

O front não usa o supabase-js nem fala com o Supabase: chama estas rotas, e a API repassa ao Supabase Auth (GoTrue) do projeto. Ninguém cria conta sozinho: só a LANN cadastra (ver [Cadastro de usuários](#cadastro-de-usuários-pela-lann)). Ter senha no Supabase não basta: o usuário precisa estar ativo em `seg.usuario`.

- **Tokens:** o access token (JWT, 1 h) vai no corpo da resposta e fica só na memória do front; as outras rotas recebem `Authorization: Bearer <accessToken>`. O refresh token fica no cookie `ovx_refresh` (`HttpOnly`, `SameSite=Strict`, `Path=/api/auth`, `Secure` quando `COOKIE_SEGURO`, `Max-Age` = `AUTH_REFRESH_SEGUNDOS`, padrão 7 dias). Ao recarregar a página, o front chama `renovar` para ter um access token de novo.
- **Todas são `POST` e exigem `Content-Type: application/json`** (senão 415). Erros em `{"detail": "..."}`, com texto pronto para o usuário. Corpo inválido (campo faltando, tipo errado) → 422 no formato de sempre.
- **Respostas com token** levam `Cache-Control: no-store`.

| Rota | Corpo | Sucesso | Erros |
|---|---|---|---|
| `entrar` | `{"email", "senha"}` | 200 `RespostaAuth` + cookie | 401 "E-mail ou senha não conferem. Confira os dois e tente de novo." (e-mail inexistente ou senha errada, mesma resposta); 403 "Acesso negado." (sem cadastro ativo; sem cookie); 429 "Foram 5 tentativas com a senha errada." (5 senhas erradas para o e-mail, deste IP, em 15 min) ou "Muitas tentativas. Tente de novo em alguns minutos." (limite por IP ou do Supabase); 503 |
| `renovar` | `{}` (ou vazio) + cookie | 200 `RespostaAuth` + cookie novo | 401 "Sessão expirada. Entre de novo." (sem cookie ou refresh recusado; cookie apagado); 401 "Sua sessão terminou por falta de uso. Entre de novo." (sessão parada há mais que o tempo da pessoa; cookie apagado e sessão encerrada no Supabase); 403 "Acesso negado." (desativado; cookie apagado); 503 (o cookie fica) |
| `sair` | `{}` (ou vazio); `Authorization` opcional | 204 sempre, cookie apagado e, com o access token, a sessão encerrada no Supabase (`/logout?scope=local` revoga os refresh tokens dela) | só 415 |
| `esqueci-senha` | `{"email"}` | 204, exista ou não o e-mail | 429 "Muitas tentativas. Tente de novo em alguns minutos."; 503 |
| `definir-senha` | `{"tokenHash", "tipo": "convite" \| "recuperacao", "senha"}` | 200 `RespostaAuth` + cookie | 400 com o que falta na senha ("A senha precisa ter uma letra maiúscula e um número." etc.); 410 "Este link não vale mais." (vencido, já usado ou inexistente); 403; 429; 503 |

`RespostaAuth`: `{"accessToken": "…", "expiraEm": "2026-10-23T15:00:00Z", "usuario": {"nome": "Ana Alfa" | null, "email": "ana@alfa.example"}, "inatividadeSegundos": 14400}`. `inatividadeSegundos` é o tempo de sessão parada da pessoa: o front conta a inatividade com ele (aviso 2 minutos antes). 503 é sempre "Autenticação indisponível." (Supabase Auth fora do ar, timeout ou erro 5xx). **Todo 429 traz `Retry-After`** com os segundos até poder tentar de novo (no limite do próprio Supabase, 60).

- **`definir-senha`** recebe o `token_hash` e o `type` do link do e-mail (`/definir-senha?token_hash=…&type=invite|recovery`; o front traduz `invite` → `convite` e `recovery` → `recuperacao`). A API confere a política de senha **antes** de gastar o link (o `/verify` do Supabase só aceita o link uma vez); depois chama `/verify` e grava a senha.
- **Política de senha:** pelo menos 8 caracteres, uma letra maiúscula (`[A-ZÀ-Ý]`) e um número (`[0-9]`), a mesma de `src/auth/senha.ts` do front. Quem decide é a API (`problemas_da_senha` em [app/auth/router.py](app/auth/router.py)); o 400 diz o que falta. Mudou lá, muda aqui (e vice-versa).
- **Limite de tentativas** (em memória, por processo): `entrar` pausa depois de 5 senhas erradas para o mesmo e-mail, do mesmo IP, em 15 min (acertar zera a contagem), e aceita 50 tentativas por IP em 5 min; `esqueci-senha` 3 por e-mail + IP em 15 min e 10 por IP em 15 min. Tentativa recusada pelo limite não conta, então o `Retry-After` não cresce a cada clique. Com mais de uma instância da API, cada uma conta as suas (ver TODO.md).
- **Limites do próprio Supabase Auth:** todas as chamadas ao GoTrue saem do IP da API, então os limites por IP dele valem para todos os usuários juntos: `/token` (login **e** renovação somados) 150 a cada 5 min; `/verify` 30 a cada 5 min; `/recover` 30 a cada 5 min (e 1 por usuário a cada 60 s); envio de e-mail 2 por hora no projeto inteiro com o SMTP padrão. Com o uso atual (poucas dezenas de usuários, uma renovação por hora cada), sobra folga; o risco é alguém esgotar a cota de login de todos, e por isso há também o limite só por IP. Dá para o Supabase contar por usuário com o header `Sb-Forwarded-For`, mas ele exige a chave secreta na API (ver TODO.md, "A decidir").
- **Sessão parada, por pessoa** (migration 28): a sessão termina depois de um tempo sem uso, que é de cada pessoa: `seg.usuario.sessao_minutos` (30 min, 1 h, 4 h, 8 h, 12 h ou 24 h, trocado pela Gestão; mais de 12 h, só Admin BORD) ou, sem ele, o padrão do perfil (Admin BORD 24 h, Consultor 8 h, clientes 4 h; `seg.sessao_padrao`). Não há mais `AUTH_INATIVIDADE_SEGUNDOS`. Uso = pedido autenticado à API ou renovação. O último uso de cada sessão do Supabase (claim `session_id` do access token) fica em `seg.sessao_uso`: `entrar` e `definir-senha` registram a sessão nova (e o primeiro acesso da pessoa), cada pedido autenticado atualiza o último uso junto com o commit (sem ida a mais ao banco) e `renovar` confere e atualiza ([app/auth/uso.py](app/auth/uso.py)). Parada há mais que o limite: `renovar` responde 401 "Sua sessão terminou por falta de uso. Entre de novo.", apaga o cookie e encerra a sessão no Supabase. Sessão vencida não volta com um pedido, e sessão sem registro também não renova. Com a aba aberta, o front derruba a sessão sozinho depois de `inatividadeSegundos` sem clique, tecla, rolagem ou mouse; o controle daqui cobre a aba fechada. Desativar alguém pela Gestão apaga as sessões abertas dele. O "Inactivity timeout" do Supabase (Authentication → Sessions) não é usado: é do plano Pro, conta só as renovações (exigiria access token de 10 min) e o limite ficaria fora da configuração da API.
- **Logs:** nunca senha nem tokens; só o `error_code` do Supabase quando ele recusa.

#### Configurar o Supabase Auth (uma vez, no painel)

Não dá para fazer por código daqui. No painel do projeto de produção (`sesculmqiwvtpparxflz`, São Paulo; o antigo `ddpicszgihwekcvyevmd` não vale mais):

1. **Authentication → Sign In / Providers → Email:** desligue "Allow new users to sign up" (ninguém cria conta sozinho; o convite pelo CLI continua funcionando). Deixe "Confirm email" ligado.
2. **Authentication → URL Configuration:** *Site URL* = a URL do front em produção, igual ao `FRONT_URL` da API. Em *Redirect URLs*, a de produção e a de dev: `https://<front>/definir-senha` e `http://localhost:5173/definir-senha`.
3. **Authentication → Emails → Templates** (em português):
   - *Invite user*: assunto "Seu acesso ao Overnex"; link `{{ .SiteURL }}/definir-senha?token_hash={{ .TokenHash }}&type=invite`
   - *Reset password*: assunto "Redefinir sua senha do Overnex"; link `{{ .SiteURL }}/definir-senha?token_hash={{ .TokenHash }}&type=recovery`

   Use `{{ .TokenHash }}`, não `{{ .ConfirmationURL }}`: o link vai direto para o front, que manda o hash para `POST /api/auth/definir-senha`. Exemplo de corpo do convite:
   ```html
   <h2>Bem-vindo ao Overnex</h2>
   <p>A LANN criou seu acesso. Clique no link abaixo para definir sua senha:</p>
   <p><a href="{{ .SiteURL }}/definir-senha?token_hash={{ .TokenHash }}&type=invite">Definir minha senha</a></p>
   <p>Se você não esperava este e-mail, ignore-o.</p>
   ```
   Os links valem pelo prazo de *Email OTP Expiration* (Authentication → Providers → Email; padrão 1 h, máximo 24 h). **Ponha 86400 s (24 h)**: é o prazo que a Gestão usa para dizer "Convite vencido" (`AUTH_CONVITE_HORAS`, padrão 24); com outro valor no painel, mude a variável junto.
4. **SMTP próprio** (Authentication → Emails → SMTP Settings): **obrigatório antes de convidar clientes.** O SMTP padrão do Supabase só entrega para e-mails do time do projeto e só 2 por hora. Depois de configurar, ajuste o limite de e-mails em Authentication → Rate Limits.
5. **Política de senha** (Authentication → Providers → Email → Password): *Minimum password length* = 8 e *Password requirements* = **No required characters**. O Supabase não tem a combinação do front (maiúscula e número, sem exigir minúscula): "Lowercase, uppercase letters and digits" recusaria `ABCDEFG1`, que o front aceita, e "Letters and digits" só conta letras sem acento (recusaria `Á1234567`). Nos dois casos a recusa viria depois do `/verify`, com o link já gasto. Por isso a regra de verdade fica na API, conferida antes do `/verify`, e o Supabase só garante o mínimo de 8. Senha posta pelo painel ou pela API administrativa não passa pela regra da API.
6. **Chaves:** a API usa a chave **publishable** (`SUPABASE_ANON_KEY`); o CLI de cadastro e o job de convites usam a **secreta** (`SUPABASE_SERVICE_ROLE_KEY`), só na máquina de quem cadastra e nos Secrets do GitHub. Os JWTs são assinados com chave assimétrica ES256 (Project Settings → JWT Keys); a API valida pelo JWKS público, sem segredo. Se um dia girar a chave, crie a nova como *standby* e espere 1 h (cache do JWKS na API) antes de torná-la a atual.
7. **Usuário de desenvolvimento** (`diretoria@lannconsult.com.br`): crie-o em Authentication → Users (*Send invitation*, ou *Create new user* com *Auto Confirm User*) e rode [supabase/producao/usuario_dev_supabase_auth.sql](../supabase/producao/usuario_dev_supabase_auth.sql), que troca o `id_autenticacao` provisório pelo id do Supabase Auth. Se o front continuar usando o token fake, ponha esse id novo em `VITE_AUTH_FAKE_OBJECT_ID` (o script mostra o id no fim).
8. **Admin BORD** (`arthur.duarte@bordgestao.com.br`): crie-o do mesmo jeito em Authentication → Users e rode [supabase/producao/admin_bord.sql](../supabase/producao/admin_bord.sql), que o cadastra com o perfil `admin` (sem vínculo: vê todos os clientes). Depois das migrations 25 a 28.

### Perfis e cliente escolhido

Cada pessoa tem **um perfil** (`seg.usuario.perfil`, o mesmo em todas as empresas dela; [app/seguranca/perfis.py](app/seguranca/perfis.py)):

| Perfil | Rótulo | Clientes | Papel no vínculo | Sessão padrão |
|---|---|---|---|---|
| `admin` | Admin BORD | todos os ativos, sem vínculo | `admin` | 24 h |
| `consultor` | Consultor por carteira | os dos vínculos | `gestor` | 8 h |
| `cliente_principal` | Cliente principal | os dos vínculos | `gestor` | 4 h |
| `cliente_usuario` | Cliente usuário | os dos vínculos | `leitor` | 4 h |

- **Painel ligado por cliente** (`cfg.cliente_tela`, migration 25): tela desligada não abre para ninguém do cliente, nem para o admin. Ligada, abre para o admin e para quem a tem no perfil.
- **Matriz por cliente:** cada cliente tem 3 perfis fixos em `cfg.perfil_acesso` (`perfil` = `consultor`, `cliente_principal` ou `cliente_usuario`; nascem com o cliente, por trigger), e a Gestão grava em `cfg.perfil_tela` deles quais painéis cada um vê. O vínculo de uma pessoa com um cliente (`seg.definir_vinculos`) leva o papel do perfil, o perfil fixo do cliente e **todas** as empresas dele. Perfis livres antigos (`perfil` nulo, como o "Financeiro" do seed) continuam valendo na checagem, mas não aparecem na Gestão.
- **`seg.usuario.tipo`** (`cliente`, `lann`) é só informativo: não dá acesso a nada.
- **RLS com o cliente escolhido** (migration 26): `seg.clientes_do_usuario()` devolve só o cliente escolhido (`app.cliente_id`). Sem escolha: o único vínculo de quem tem um só; nada para o admin e para quem tem mais de um. O nome dos clientes que a pessoa pode abrir (`cfg.cliente`) fica sempre visível, para o seletor.

### Gestão: `/api/admin/*` (Admin BORD; o consultor só lê)

Área interna da BORD (mockup Gestão M2.1.0). Quem não é admin recebe o 403 genérico (log `admin:<rota>`, negado na etapa 3), **menos o Consultor por carteira nas duas leituras abaixo** (desde 25/11/2026, migration 33):

- `GET /api/admin/gestao`: só as empresas dos vínculos dele (o RLS da migration 33 lê painéis ligados, fontes e cargas da carteira), `perfis` e `sessoes` vazios e `permissoes` `{"criarEmpresa": false, "usuarios": false}` (o front esconde "Nova empresa" e a aba Usuários);
- `GET /api/admin/clientes/{slug}`: só empresa da carteira (outra, ou slug que não existe: 403, sem dizer se existe), com `abas` = Implantação, Unidades (as dos vínculos dele) e Prontidão dos dados, `permissoes` `{"editar": false, "convidar": false}`, o `resumo` sem a contagem de usuários e os campos das outras abas vazios (nada de pessoas, matriz, preferências, feriados ou cargas).

Todas as gravações e `/api/admin/usuarios` continuam só do admin. `/api/sessao` devolve `gestao: true` também para o consultor: o front abre a Gestão no login. Slug de cliente que não existe: 404 "Empresa não encontrada.". Regra quebrada: 422 com o texto pronto em `detail`. As gravações respondem 204, confirmam antes de responder e vão para `seg.log_alteracao`. Estas rotas devolvem todos os campos sempre (`null` quando não há valor). Na tela, "empresa" é o `cfg.cliente`.

| Rota | O que faz |
|---|---|
| `GET /api/admin/gestao` | Barra lateral: `clientes` (`slug`, `nome`, os `setores` das unidades, `{id, rotulo}` sem repetir na ordem de `cfg.setor`, e o estado das `camadas`), os 4 `perfis` (descrição, sessão padrão, pessoas ativas), as `sessoes` (opções de tempo, com `soAdmin`) e `permissoes` (`criarEmpresa`, `usuarios`). Consultor: ver acima |
| `POST /api/admin/clientes` | Cria uma empresa (`cfg.cliente`), sem unidade: `{"nome", "slug": string \| null}` (setor e segmento são de cada unidade; corpo com `setor` ou `segmento` dá o 422 de formato). Sem `slug`, a API monta um a partir do nome (sem acento, minúsculas, hífen). 201 `{"slug", "nome", "mensagem"}`. 422 "Informe o nome da empresa.", "O identificador só aceita letras minúsculas, números e hífen (ex.: pj-cortes)." ou "Já existe uma empresa com o identificador …." |
| `PUT /api/admin/clientes/{slug}` | `{"nome"}`: troca o nome da empresa (204; corpo com `segmento` ou `setor` dá o 422 de formato). O log de alteração guarda o nome antes e depois. O slug não muda. 422 "Informe o nome da empresa."; 404 só para slug que não existe. Precisa da migration 31: sem ela, o banco barra o update e a rota responde 500, com o motivo no log da API |
| `GET /api/admin/clientes/{slug}` | Tudo das abas da empresa, numa resposta: `cliente` (com os `setores` das unidades), `abas` (as que a pessoa vê, na ordem, com `id` e `rotulo`; a aba `unidades`, "Unidades", logo depois de Implantação), `permissoes` (`editar`, `convidar`), `resumo`, `camadas` (Implantação: as seções do menu, cada uma com `paineis`, `{codigo, nome, nivel}` na ordem do menu, e as `pendencias`; também para o consultor), `niveis` (os 3 níveis do catálogo, `{id, rotulo}`, sempre inteiros e na ordem: o front dá a cor da etiqueta pela posição), `unidades` (`{id, nome, setor: {id, rotulo} \| null, segmento, grupo, uf, cidade, bairro, endereco}`, na ordem da árvore da bandeja; só leitura: o cadastro é por SQL, [supabase/producao/modelo_unidades.sql](../supabase/producao/modelo_unidades.sql)), `fontes` (Prontidão dos dados), `paineis` (todos os do catálogo, por seção e na ordem do menu, com `secao`, o rótulo da seção, e `nivel`, o id do nível), `acesso` (matriz perfil × painel, só os ligados; em cada linha, `ve` e `nomes`, a chave "vê nomes" por perfil, `null` nos painéis sem ela), `usuarios` da empresa, `preferencias`, `feriados`, `cargas` (as 10 últimas) e `menu` (como o Cliente principal vê, nos grupos do menu do app: Visão, Comercial, Financeiro, Pessoas e Estoque) |
| `PUT /api/admin/clientes/{slug}/unidades/{id}` | Edita uma unidade da empresa (só o Admin BORD; migration 36): `{"nome", "setor": id \| null, "segmento", "grupo", "uf", "cidade", "bairro", "endereco"}`, todos os campos sempre (texto em branco vira `null`; espaços repetidos saem; UF em maiúsculas). 204. 404 "Empresa não encontrada." ou "Unidade não encontrada." (a unidade não é dessa empresa); 422 "Informe o nome da unidade.", "Já existe uma unidade com esse nome nesta empresa." (sem diferenciar maiúsculas), "Setor inválido." ou "UF inválida.". Vai para `seg.log_alteracao` com o antes e o depois e apaga as respostas prontas das telas do cliente (setor e grupo mudam a árvore Negócios e as telas de setor). Unidade nova, apagar unidade e trocar o cliente da unidade continuam por SQL. `GET /api/admin/clientes/{slug}` traz `permissoes.unidades` (o "Editar" de cada unidade; consultor `false`) e `opcoesSetor` (as opções do campo Setor; `[]` para o consultor) |
| `PUT /api/admin/clientes/{slug}/paineis/{codigo}` | `{"ligado": true}`. Painel sem dado não liga: 422 "Este painel ainda não tem dado nesta empresa."; painel de um setor que nenhuma unidade da empresa tem também não: 422 "Este painel é só para empresas com unidade do setor Distribuidora." (desligar pode). Ligado pela primeira vez, os perfis fixos recebem a matriz padrão |
| `PUT /api/admin/clientes/{slug}/perfis/{perfil}/paineis/{codigo}` | `{"ve": true}`. `admin` não muda: 422 "Admin BORD vê tudo e não pode ser alterado." |
| `PUT /api/admin/clientes/{slug}/perfis/{perfil}/paineis/{codigo}/nomes` | `{"veNomes": true}`: a chave "vê nomes" (grava `cfg.perfil_tela.ve_dado_nominal`), só nos painéis que abrem sem nomes e têm a chave "Mostrar nomes" (hoje, `PES-01`). 422 "Este painel não tem nomes para liberar." (os outros; Equipe comercial e Receber e pagar já exigem os nomes), "Este perfil não vê o painel." e, para `admin`, "Admin BORD vê tudo e não pode ser alterado." A marca vale para todos do perfil no cliente; tirar o painel do perfil a apaga |
| `PUT /api/admin/clientes/{slug}/preferencias/{id}` | `{"valor": "3"}`: a mesma escolha salva do seletor da Visão geral (hoje, `linhas-custo`) |
| `PUT` e `DELETE /api/admin/clientes/{slug}/feriados/{data}` | `{"nome": "Padroeira"}`: feriado da empresa (`cfg.feriado`), para os dias úteis |
| `GET /api/admin/usuarios` | Todas as pessoas (`perfil`, `clientes`, `aoEntrar`, `sessaoMinutos`, `situacao`, `acessos`, `podeDesativar`, `convite`), os `filtros` com a contagem e `notaConvite` (texto da gaveta de convite) |
| `POST /api/admin/usuarios` | Convida uma pessoa nova: `{"email", "nome", "perfil", "clientes": [slug], "sessaoMinutos": 240 \| null}` (`clientes` pode vir vazio). 202 `{"id", "mensagem": "Convite na fila: sai em instantes."}`. O e-mail não sai daqui: o pedido entra na fila |
| `POST /api/admin/usuarios/{id}/convite` | Sem corpo. Manda o convite de novo a quem ainda não entrou (reenvio ou nova tentativa). 202 com o mesmo corpo |
| `PUT /api/admin/usuarios/{id}` | `{"nome", "perfil", "clientes": [slug], "sessaoMinutos": 240 \| null}` |
| `PUT /api/admin/usuarios/{id}/sessao` | `{"minutos": 480}` |
| `PUT /api/admin/usuarios/{id}/acesso` | `{"ativo": false}`; desativar encerra as sessões abertas da pessoa |
| `DELETE /api/admin/usuarios/{id}` | Apaga a pessoa, que precisa estar desativada (204). 422 "Desative o acesso antes de apagar." ou "Esta pessoa tem registros que dependem dela e não pode ser apagada. Ela continua desativada."; 404 "Pessoa não encontrada." |

- **Empresa nova** (migration 30): nasce sem unidade, com tudo desligado, e já aparece na barra lateral e no convite, com os `setores` das unidades (vazio até o cadastro delas). A empresa não tem setor nem segmento próprio (o "Ramo" saiu em 27/11/2026, migration 37): os dois são de cada unidade. O nome se edita depois (`PUT /api/admin/clientes/{slug}`); o slug não muda (está nas URLs), e a empresa não se desativa nem se apaga pela tela. Unidades (`cfg.empresa`, com setor, segmento, grupo e endereço), fontes da carga e de-para de contas continuam por script; a unidade cadastrada depois entra sozinha nos vínculos de quem já foi convidado. Até lá, a sessão dela vem com `empresas` vazio e `negocios` null, e a pessoa não vê tela nenhuma.
- **Catálogo fechado** ([app/admin/catalogo.py](app/admin/catalogo.py)): só os 9 painéis que existem na API (Visão geral, Vendas, Equipe comercial, Clientes e atendimentos, Receber e pagar, Pessoas, Resultado, Fluxo de caixa e Estoque), em 4 camadas, que são as seções do menu (desde 27/11/2026): Comercial (Visão geral, Vendas, Equipe comercial, Clientes e atendimentos), Financeiro (Resultado, Receber e pagar, Fluxo de caixa), Pessoas e, só nas empresas com unidade da Distribuidora, Estoque. Cada painel tem um nível, a etiqueta da Gestão: Universal Básico (sobe só com as tabelas do ERP), Universal Completo (Resultado e Fluxo de caixa: plano de contas, de-para, orçamento e saldo inicial) e Módulo Distribuidora (Estoque). Carteira de clientes e Compras do mockup ainda não têm tela.
- **Painéis por setor** (migrations 34 e 35, [app/seguranca/setores.py](app/seguranca/setores.py)): o setor é da unidade (`cfg.empresa.setor`, da lista fechada `cfg.setor`: Distribuidora, Indústria, Varejo, Serviços), e decide os painéis que só existem num setor. Hoje só o `EST-01` (Estoque) é da Distribuidora; os outros valem em todos. O painel de um setor vale no cliente se alguma unidade liberada à pessoa for do setor (na Gestão, alguma unidade do cliente); senão: fora de `telas` em `/api/sessao` (também para o admin), 403 genérico na tela (etapa 4), e na Gestão com a chave travada (`podeLigar: false`, `status: "neutro"`, `nota: "Só para empresas com unidade do setor Distribuidora."`), fora da matriz, do `menu` e das `camadas` (a camada sem nenhum painel do setor some). Quando vale, soma só as unidades pedidas do setor (`escopo`, acima). Mudar o setor de uma unidade não apaga a configuração do painel: ela volta se o setor voltar.
- **Painel disponível** = alguma fonte ativa do prefixo que ele exige (`f_vendas_*`, `f_folha_*`…) já teve carga `ok` (regra provisória, no catálogo). Camada (seção) **ativa** = todos os painéis dela ligados e com dado (o Financeiro só com os três); **em implantação** = algum ligado; **desligada** = nenhum ligado.
- **Matriz padrão** (ao ligar um painel pela primeira vez): Cliente principal vê tudo; Consultor não vê Pessoas; Cliente usuário não vê Pessoas, Resultado nem Fluxo de caixa. Linha nova vê dado nominal só em Equipe comercial e Receber e pagar (a tela exige); os nomes de "Cargos e salários" (dado nominal de `PES-01`) nascem desligados e se ligam pela chave "vê nomes".
- **Regras das pessoas** ([app/admin/usuarios.py](app/admin/usuarios.py)), com o texto no 422: "Admin BORD só com e-mail da BORD." (`ADMIN_DOMINIOS`, padrão `bordgestao.com.br`); "O Overnex precisa de pelo menos um Admin BORD ativo."; ninguém tira o próprio perfil de admin nem desativa a si mesmo; "Mais de 12 h, só Admin BORD."; sessão igual ao padrão do perfil fica sem valor próprio; o e-mail é o login e não muda (o `api_site` nem tem permissão de trocá-lo); o perfil admin fica sem vínculos.
- **Situação** de cada pessoa, a que mais pede ação, nesta ordem: `inativo`; de quem ainda não entrou, `fila` (convite pedido, ainda não saiu), `nao-enviado` (o envio falhou; `situacao.detalhe` traz o motivo) e `vencido` (convidada há mais de `AUTH_CONVITE_HORAS`); `sem-empresa`; `convite` (enviado, dentro do prazo); `ativo`. Com `status` no vocabulário de cor do front (`saudavel`, `atencao`, `critico`, `neutro`). O filtro `pendentes` conta `fila`, `nao-enviado`, `convite` e `vencido`.
- **Apagar pessoa** (migration 32): só quem já está desativado (`podeApagar` em cada pessoa) e nunca a si mesmo. Saem o cadastro, os vínculos e os pedidos de convite; os logs ficam, e a exclusão vai para `seg.log_alteracao` com o e-mail, o nome, o perfil e as empresas que a pessoa tinha. O e-mail fica livre para um convite novo. O login no Supabase Auth só se apaga com a chave secreta: o id vai para `seg.login_a_apagar`, a API aciona o job de convites e ele apaga (antes de mandar os convites da rodada). Não tem volta: para devolver o acesso, é convidar de novo.
- **Botão de convite:** `convite` em cada pessoa é `{"rotulo": "Reenviar convite"}` (ou `"Tentar de novo"`, depois de um envio que falhou) quando ela está ativa, ainda não entrou e não tem pedido na fila; senão, `null` (o front não mostra o botão).
- O que a Gestão lê e grava no banco está nas políticas `seg.eh_admin()` da migration 27: cadastro e controle da carga de todos os clientes, nunca dado de negócio (as tabelas de dados só abrem com o cliente escolhido). Os logs seguem sem leitura direta: o registro de acessos vem de `seg.resumo_acessos()`.

### Convite pela Gestão (fila + job)

Decisão de 13/11/2026 (opção E de [docs/convite-pela-gestao-opcoes.md](../docs/convite-pela-gestao-opcoes.md)): criar login no Supabase Auth exige a chave secreta, e ela **não entra no ambiente da API**. A tela grava o pedido e um job manda.

1. `POST /api/admin/usuarios` confere o pedido ([app/admin/convites.py](app/admin/convites.py)) e chama `seg.pedir_convite` (migration 29), que cria a pessoa **sem login** (`id_autenticacao` nulo), os vínculos e uma linha em `seg.convite_fila`. A pessoa já aparece na lista, como "Convite na fila". O reenvio (`POST .../{id}/convite`) só insere outra linha na fila.
2. Depois do commit, a API aciona o workflow [convites.yml](../.github/workflows/convites.yml) pela API do GitHub (em segundo plano: não atrasa a resposta nem a derruba se falhar). Sem o token configurado, ou se o acionamento falhar, o pedido espera a rodada do próprio workflow (de hora em hora, das 8h às 18h de Fortaleza, em dia útil).
3. O job ([app/cli/enviar_convites.py](app/cli/enviar_convites.py)) lê os pedidos pendentes, manda o convite pelo Supabase Auth (o mesmo e-mail do CLI, com o link para `{FRONT_URL}/definir-senha`) e grava o resultado: **enviado** (a pessoa ganha o id do Supabase Auth e `convidado_em`: "Convite enviado") ou **falhou**, com o motivo em texto pronto ("Convite não enviado"). Não há nova tentativa automática: quem decide é o Admin BORD, pelo botão "Tentar de novo".

- **Erros na hora (422, texto pronto):** "E-mail inválido.", "Este e-mail já tem cadastro no Overnex.", as regras da edição (perfil, "Admin BORD só com e-mail da BORD.", "Mais de 12 h, só Admin BORD.", "Empresa não encontrada: …") e, no reenvio, "O acesso desta pessoa está desativado. Reative antes de mandar o convite.", "Esta pessoa já entrou no Overnex. Para trocar a senha, ela usa "Esqueci a senha"." e "O convite desta pessoa já está na fila.". 404 "Pessoa não encontrada.".
- **Erros do envio (aparecem depois, em `situacao.detalhe`):** limite de e-mails do Supabase, serviço de login fora do ar, e-mail que já tem login criado fora do Overnex, e-mail recusado, acesso desativado antes de o convite sair.
- **Convite sem empresa é aceito:** a pessoa fica "Sem empresa" (depois de o convite sair) até alguém marcar uma; se entrar antes, vê o aviso para falar com a BORD.
- **`mensagem` do 202:** "Convite na fila: sai em instantes." com o acionamento configurado; sem ele, "Convite na fila: sai na próxima rodada de envio, em até uma hora.".
- **Configuração da API:** `GITHUB_CONVITES_TOKEN` (fine-grained personal access token do GitHub, só deste repositório, com a permissão *Actions: Read and write*; ele aciona e cancela workflows do repositório, e não lê segredos, código nem dados) e `GITHUB_CONVITES_REPO` (`dono/repositorio`). Opcionais: `GITHUB_CONVITES_WORKFLOW` (padrão `convites.yml`) e `GITHUB_CONVITES_REF` (padrão `main`). O token vence: anote a data e troque antes.
- **Configuração do job** (GitHub → Settings → Secrets and variables → Actions): usa os Secrets `DATABASE_URL_CARGA` e `SUPABASE_SECRET_KEY` e a Variable `SUPABASE_URL`, que o `diario.yml` já usa, e mais a **Variable `FRONT_URL`** (a URL do front, igual à da API). O acionamento pela API e a rodada de hora em hora só funcionam com o `convites.yml` na branch padrão (`main`).
- **Logins a apagar:** a cada rodada, antes dos convites, o job apaga no Supabase Auth o login de quem foi apagado pela Gestão (`seg.login_a_apagar`). Login que já não existe conta como apagado; o que não deu para apagar fica para a próxima rodada (e o job termina com erro, para aparecer no GitHub). Login que voltou a ter cadastro sai da fila sem ser apagado.
- **Banco do job:** o role `carga`, que em `seg` só executa as funções das duas filas (`seg.convites_na_fila()`, `seg.concluir_convite()`, `seg.logins_a_apagar()` e `seg.login_apagado()`). Ele não lê nem grava mais nada do schema.
- **Saída do job:** só o número do pedido e o resultado; nunca e-mail, nome, id ou chave.
- **Para rodar à mão:** GitHub → Actions → convites → Run workflow. Ou, de uma máquina com a chave secreta e `DATABASE_URL_CARGA` no `.env`: `uv run python -m app.cli.enviar_convites`.

### Cadastro de usuários pela LANN

Ninguém cria conta sozinho. O Admin BORD convida pela Gestão (acima). O CLI continua valendo, para quem tem a chave secreta na máquina: convida no Supabase Auth (o e-mail leva a `{FRONT_URL}/definir-senha`) e grava `seg.usuario` e os vínculos numa transação só, sem passar pela fila. Depois de criada, a pessoa é editada pela Gestão.

```bash
cd api
# pessoa de um cliente (ou de vários: repita --cliente)
uv run python -m app.cli.convidar_usuario \
  --email ana@pjcortes.com.br --nome "Ana Souza" \
  --perfil cliente_usuario --cliente pj-cortes
# Admin BORD: sem --cliente, e só com e-mail de ADMIN_DOMINIOS
uv run python -m app.cli.convidar_usuario \
  --email fulano@bordgestao.com.br --nome "Fulano" --perfil admin
# convite de novo para quem ainda não entrou (o link vence)
uv run python -m app.cli.convidar_usuario --email ana@pjcortes.com.br --reenviar
```

- `--perfil` é um dos 4 (`admin`, `consultor`, `cliente_principal`, `cliente_usuario`); `--cliente` é o `slug` de `cfg.cliente` (pode repetir; obrigatório fora do admin, proibido nele). `--sessao` (minutos: 30, 60, 240, 480, 720 ou 1440; acima de 720, só admin) é opcional: sem ele, vale o padrão do perfil.
- O vínculo leva o papel do perfil, o perfil fixo do cliente e todas as empresas dele (`seg.definir_vinculos`).
- Confere tudo antes de convidar. Se o e-mail já existe em `seg.usuario` ou no Supabase Auth, avisa e para. Se o convite falhar, nada é gravado. Se a gravação falhar depois do convite, o usuário criado no Supabase Auth é apagado (se nem isso der, o comando mostra o id para apagar à mão).
- `--reenviar` só vale para quem está ativo e ainda não entrou; para quem já entrou, o caminho é "Esqueci a senha". Serve também para quem ficou com "Convite não enviado" na Gestão: grava o id do Supabase Auth junto com a data do convite.
- Precisa, no `.env` de quem roda (nunca no da API publicada): `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (chave secreta), `FRONT_URL` e `DATABASE_URL_ADMIN` (conexão como `postgres`; em produção, a connection string de Project Settings → Database, com a senha do banco). `ADMIN_DOMINIOS` é opcional (padrão `bordgestao.com.br`).
- Enquanto o Supabase Auth não tiver SMTP próprio, o convite só chega a e-mails do time do projeto. Para o primeiro Admin BORD há o caminho sem e-mail: crie o usuário em Authentication → Users e rode [supabase/producao/admin_bord.sql](../supabase/producao/admin_bord.sql).
- Desativar e reativar alguém é pela Gestão (`PUT /api/admin/usuarios/{id}/acesso`). A pessoa perde o acesso na hora: toda requisição confere `seg.usuario.ativo` (403), as sessões abertas são apagadas e a renovação seguinte apaga o cookie. Para tirar também o cadastro e o login do Supabase, apague a pessoa pela Gestão depois de desativar (`DELETE /api/admin/usuarios/{id}`).

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
- **Acesso:** as mesmas 6 etapas das telas, com o mesmo `cliente=<slug>` opcional. Na etapa 4, a tabela precisa estar registrada para o cliente em `cfg.fonte_carga` e liberada a um perfil do usuário em `cfg.perfil_tabela` (o admin lê toda tabela registrada). Na etapa 6, `contem_dado_nominal` exige `ve_dado_nominal`.
- **Filtro:** o RLS só devolve linhas do cliente e das empresas do usuário. Linhas sem empresa (tabelas sem `coluna_empresa`) são vistas por todos os usuários do cliente.
- **Limites:** colunas e nomes de tabela nunca vêm da requisição para o SQL. Pelo caminho, só são aceitos nomes `^[a-z][a-z0-9_]*$`, e a tabela ainda precisa estar registrada para o cliente.

### Conexão em produção

Use o pooler do Supabase em modo transação (porta 6543) com o usuário `api_site.<project-ref>`. Por isso o pool usa `prepare_threshold=None` e o `app.usuario_id` e o `app.cliente_id` são sempre locais à transação.

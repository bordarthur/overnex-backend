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

A API só sobe com `SUPABASE_URL`, `SUPABASE_ANON_KEY` e `FRONT_URL` (com `AUTH_PROVEDOR=supabase`, o padrão), mesmo com o token fake. Para o login de verdade em dev, use o projeto do Supabase (a chave publishable está em Project Settings → API Keys).

Com `AUTH_FAKE=1` (só em `AMBIENTE=dev`), o token pode ser `fake:<id_autenticacao>` (`seg.usuario.id_autenticacao`). Usuários do seed:

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

Sem `AMBIENTE` definido, a API sobe como `prod`: exige `SUPABASE_URL`, `SUPABASE_ANON_KEY` e `FRONT_URL`, e recusa `AUTH_FAKE` e `COOKIE_SEGURO=false`. Não passe `SUPABASE_SERVICE_ROLE_KEY` nem `DATABASE_URL_ADMIN` para o contêiner: só o CLI de cadastro usa.

Atrás de proxy (Azure Container Apps, Static Web Apps), o limite de tentativas usa o IP do cliente que o uvicorn lê do `X-Forwarded-For`. O `--proxy-headers` do Dockerfile só confia no proxy de `127.0.0.1`: defina `FORWARDED_ALLOW_IPS` com o IP (ou a faixa) do proxy, ou `*` se a API só for alcançável por ele. Sem isso, todos os usuários aparecem com o IP do proxy e dividem o mesmo limite por IP.

## Como está organizada

```
app/
  main.py              cria o app; sem CORS; erros sem detalhe interno; GET /health
  core/config.py       Settings (DATABASE_URL, AUTH_PROVEDOR, SUPABASE_*, FRONT_URL, COOKIE_SEGURO, ENTRA_*, AMBIENTE, AUTH_FAKE…)
                       e ConfigCadastro (só o CLI: chave secreta e DATABASE_URL_ADMIN)
  core/db.py           pool psycopg (prepare_threshold=None) + uma transação por requisição
  seguranca/auth.py    get_usuario: valida o JWT do Supabase (JWKS ES256, iss, aud, exp, sub, role)
                       ou, com AUTH_PROVEDOR=entra, o do Entra (JWKS, iss, aud, exp, tid)
  auth/router.py       POST /api/auth/*: entrar, renovar, sair, esqueci-senha, definir-senha
  auth/gotrue.py       cliente HTTP do Supabase Auth (GoTrue), com timeout
  auth/limite.py       limite de tentativas em memória (e-mail + IP e só IP)
  auth/cadastro.py     depois do login, confere o usuário ativo em seg.usuario (+ seg.log_acesso)
  cli/convidar_usuario.py  cadastro de usuário pela LANN (convite no Supabase + seg.*)
  seguranca/acesso.py  checagem em 6 etapas + seg.log_acesso
  sessao/router.py     GET /api/sessao: usuário, cliente, empresas, telas e períodos
  telas/contrato.py    ScreenPayload, Hero, Apoio, Visual, Alerta, Status
  telas/moldura.py     base camelCase, cliente e última carga (sessão e Visão geral)
  telas/fin_01.py      GET /api/telas/fin-01 (exemplo, sem regra de negócio)
  telas/visao_geral/   GET /api/telas/visao-geral (VG-01): contrato do front e rota
  telas/paineis/       GET /api/telas/vendas|equipe-comercial|clientes (COM-01 a 03): contrato
                       PainelPayload, uma tela por módulo, demo/ com as fixtures do front
  telas/paralelo.py    consultas da mesma tela em paralelo, cada uma numa conexão
  dados/router.py      GET /api/dados/{tabela}: linhas das tabelas de dados do mart
  farol/               status(valor, faixa), função pura
  admin/, pdf/         routers vazios (TODO)
scripts/export_openapi.py
```

### Fluxo de uma requisição de tela

1. `get_usuario` valida o access token e extrai o `sub` (id do usuário no Supabase Auth).
2. `get_sessao` abre **uma** transação. A checagem chama `seg.contexto_acesso(sub, tela)` (migration 16), que numa ida ao banco traduz o `sub` no `seg.usuario.id` (pela coluna `id_autenticacao`), executa `set_config('app.usuario_id', id, true)` e devolve vínculos, perfis que liberam a tela e empresas. Daí em diante o RLS do banco só mostra o que é do usuário.
3. `exigir_tela("FIN-01")` roda as 6 etapas (usuário ativo → vínculo → papel → perfil libera a tela → empresa no vínculo → dado nominal). Cliente e empresas vêm do vínculo no banco; o `empresa` da query só vale se estiver nele.
4. Toda decisão grava em `seg.log_acesso`. O registro fica pendente na sessão e vai ao banco junto com o commit, na mesma ida (pipeline do psycopg). Na negação, a transação faz commit só do log e a resposta é `403 {"detail": "Acesso negado."}`, sem dizer a etapa; em erro inesperado, rollback (e o log daquela requisição não fica, como antes).
5. Com sucesso, a tela monta o `ScreenPayload`; `atualizado_em` é a `data_snapshot` da última carga `ok` de lançamentos do cliente (ou `null`).

### Endpoints do front: `/api/sessao` e `/api/telas/visao-geral`

Respondem em camelCase, no formato de `src/contracts/visaoGeral.ts` do front. Campo opcional sem valor sai do JSON (como `campo?:` no TypeScript); campo obrigatório que aceita nulo vai como `null` (`logoUrl`, `delta`, `modulo`).

- **`GET /api/sessao`** — etapas 1 a 3 da checagem (sem tela). Devolve `usuario` (nome e e-mail de `seg.usuario`, cadastrados pela LANN; `nome` pode vir `null`), `cliente` (com `slug`, para o front montar a URL), `papel`, `empresas` do vínculo (filtro "Empresa"), `telas` liberadas (códigos, para o menu) e `periodos` com dados: os meses com faturado no resumo mensal das tabelas `f_vendas_*` e o mês do último snapshot, do mais recente para o mais antigo (até 24). O primeiro é o padrão da Visão geral. As consultas vão numa ida ao banco só, e a sessão agenda a **pré-montagem**, em segundo plano, da Visão geral e dos painéis liberados ao usuário, no mês padrão e sem filtro ([app/telas/preparo.py](app/telas/preparo.py)): a primeira tela que o front abre já está pronta, ou espera a montagem em andamento em vez de começar outra. A pré-montagem faz a mesma checagem de acesso da rota, mas não grava em `seg.log_acesso` (o usuário ainda não abriu a tela); roda uma vez por usuário a cada metade do prazo do cache e não roda com o cache desligado.
- **`GET /api/telas/visao-geral?mes=<AAAA-MM opcional>&empresa=<uuid opcional>`** — tela `VG-01`. Sem `empresa`, vale para todas as empresas do vínculo (etapa 5 exige ao menos uma). `mes` precisa ser um dos `periodos` de `/api/sessao` (senão 422, "Mês sem dados."); sem ele, cada seção usa o seu mês mais recente. A resposta pronta fica em memória por `VISAO_GERAL_CACHE_SEGUNDOS` (padrão 15 min), com chave no cliente, nas empresas, no mês e na última carga; o acesso é conferido e registrado a cada requisição. Duas requisições da mesma chave ao mesmo tempo montam a resposta uma vez só (a segunda espera a primeira). Ao montar, última carga, cliente, fontes, feriados e parâmetros vêm numa ida ao banco ([app/telas/preleitura.py](app/telas/preleitura.py)), e as seções rodam em três ramos paralelos (Vendas e Custos, Resultado, Financeiro), cada um com uma conexão própria do mesmo usuário: o tempo é o do ramo mais lento. O pool mantém 8 conexões abertas (`DB_POOL_MIN=8`; abrir uma nova custa ~0,4 s) e vai até 20. `dadosAte` é a `data_snapshot` e `atualizadoEm` é o `fim` (com fuso) da última fonte `ok` do cliente; os dois vêm `null` se nunca houve carga. As `secoes` vêm vazias até as regras dos indicadores existirem (ou da demo, com `VISAO_GERAL_DEMO=1`).
- `logoUrl` só sai quando `cfg.cliente.logo_url` é um caminho do próprio domínio (`/…`); fora disso vem `null`, porque a CSP do front recusa imagem de fora.

### Painéis comerciais: Vendas, Equipe comercial, Clientes e atendimentos

Contrato `PainelPayload` (espelho de `src/contracts/painel.ts` do front), em [app/telas/paineis/contrato.py](app/telas/paineis/contrato.py). Mesmo padrão da Visão geral: `empresa` e `mes` opcionais, 403 genérico, 422 "Mês sem dados." e resposta pronta em memória por `VISAO_GERAL_CACHE_SEGUNDOS` (chave com os filtros também).

| Rota | Tela | Filtros (`?{id}={opção}`) |
|---|---|---|
| `GET /api/telas/vendas` | `COM-01` | `canal`, `linha` |
| `GET /api/telas/equipe-comercial` | `COM-02` (exige perfil com `ve_dado_nominal`: mostra nomes de vendedores) | `canal`, `supervisor` |
| `GET /api/telas/clientes` | `COM-03` | nenhum |

- As opções de cada filtro saem dos dados (cadastro dos vendedores, linhas das vendas) e vêm em `filtros`, com `padrao` (`todos`/`todas`); opção fora da lista → 422 "Filtro inválido.".
- Antes da primeira carga do cliente: 200 com `dadosAte` e `atualizadoEm` nulos e tudo vazio.
- Cartão sem fonte de dados não sai (ex.: sem meta de clientes, sem o cartão de fatores; sem o papel `vendedor` nas vendas, sem canais nem Equipe).
- As regras (provisórias) estão no topo de cada módulo: [vendas.py](app/telas/paineis/vendas.py), [equipe.py](app/telas/paineis/equipe.py), [clientes.py](app/telas/paineis/clientes.py) e [base.py](app/telas/paineis/base.py) (fontes, papéis e o mês).
- `explicacao` de cada cartão (o botão "i" do front): texto fixo por cartão em [explicacoes.py](app/telas/paineis/explicacoes.py); cartão sem texto sai sem a chave.
- Com `VISAO_GERAL_DEMO=1` (só dev), as três rotas devolvem as fixtures do mockup (`app/telas/paineis/demo/`), com o cliente do usuário e os filtros conferidos contra os da fixture.

### Login com e-mail e senha: `/api/auth/*`

O front não usa o supabase-js nem fala com o Supabase: chama estas rotas, e a API repassa ao Supabase Auth (GoTrue) do projeto. Ninguém cria conta sozinho: só a LANN cadastra (ver [Cadastro de usuários](#cadastro-de-usuários-pela-lann)). Ter senha no Supabase não basta: o usuário precisa estar ativo em `seg.usuario`.

- **Tokens:** o access token (JWT, 1 h) vai no corpo da resposta e fica só na memória do front; as outras rotas recebem `Authorization: Bearer <accessToken>`. O refresh token fica no cookie `ovx_refresh` (`HttpOnly`, `SameSite=Strict`, `Path=/api/auth`, `Secure` quando `COOKIE_SEGURO`, `Max-Age` = `AUTH_REFRESH_SEGUNDOS`, padrão 7 dias). Ao recarregar a página, o front chama `renovar` para ter um access token de novo.
- **Todas são `POST` e exigem `Content-Type: application/json`** (senão 415). Erros em `{"detail": "..."}`, com texto pronto para o usuário. Corpo inválido (campo faltando, tipo errado) → 422 no formato de sempre.
- **Respostas com token** levam `Cache-Control: no-store`.

| Rota | Corpo | Sucesso | Erros |
|---|---|---|---|
| `entrar` | `{"email", "senha"}` | 200 `RespostaAuth` + cookie | 401 "E-mail ou senha não conferem. Confira os dois e tente de novo." (e-mail inexistente ou senha errada, mesma resposta); 403 "Acesso negado." (sem cadastro ativo; sem cookie); 429 "Foram 5 tentativas com a senha errada." (5 senhas erradas para o e-mail, deste IP, em 15 min) ou "Muitas tentativas. Tente de novo em alguns minutos." (limite por IP ou do Supabase); 503 |
| `renovar` | `{}` (ou vazio) + cookie | 200 `RespostaAuth` + cookie novo | 401 "Sessão expirada. Entre de novo." (sem cookie ou refresh recusado; cookie apagado); 403 "Acesso negado." (desativado; cookie apagado); 503 (o cookie fica) |
| `sair` | `{}` (ou vazio); `Authorization` opcional | 204 sempre, cookie apagado | só 415 |
| `esqueci-senha` | `{"email"}` | 204, exista ou não o e-mail | 429 "Muitas tentativas. Tente de novo em alguns minutos."; 503 |
| `definir-senha` | `{"tokenHash", "tipo": "convite" \| "recuperacao", "senha"}` | 200 `RespostaAuth` + cookie | 400 com o que falta na senha ("A senha precisa ter uma letra maiúscula e um número." etc.); 410 "Este link não vale mais." (vencido, já usado ou inexistente); 403; 429; 503 |

`RespostaAuth`: `{"accessToken": "…", "expiraEm": "2026-10-23T15:00:00Z", "usuario": {"nome": "Ana Alfa" | null, "email": "ana@alfa.example"}}`. 503 é sempre "Autenticação indisponível." (Supabase Auth fora do ar, timeout ou erro 5xx). **Todo 429 traz `Retry-After`** com os segundos até poder tentar de novo (no limite do próprio Supabase, 60).

- **`definir-senha`** recebe o `token_hash` e o `type` do link do e-mail (`/definir-senha?token_hash=…&type=invite|recovery`; o front traduz `invite` → `convite` e `recovery` → `recuperacao`). A API confere a política de senha **antes** de gastar o link (o `/verify` do Supabase só aceita o link uma vez); depois chama `/verify` e grava a senha.
- **Política de senha:** pelo menos 8 caracteres, uma letra maiúscula (`[A-ZÀ-Ý]`) e um número (`[0-9]`), a mesma de `src/auth/senha.ts` do front. Quem decide é a API (`problemas_da_senha` em [app/auth/router.py](app/auth/router.py)); o 400 diz o que falta. Mudou lá, muda aqui (e vice-versa).
- **Limite de tentativas** (em memória, por processo): `entrar` pausa depois de 5 senhas erradas para o mesmo e-mail, do mesmo IP, em 15 min (acertar zera a contagem), e aceita 50 tentativas por IP em 5 min; `esqueci-senha` 3 por e-mail + IP em 15 min e 10 por IP em 15 min. Tentativa recusada pelo limite não conta, então o `Retry-After` não cresce a cada clique. Com mais de uma instância da API, cada uma conta as suas (ver TODO.md).
- **Limites do próprio Supabase Auth:** todas as chamadas ao GoTrue saem do IP da API, então os limites por IP dele valem para todos os usuários juntos: `/token` (login **e** renovação somados) 150 a cada 5 min; `/verify` 30 a cada 5 min; `/recover` 30 a cada 5 min (e 1 por usuário a cada 60 s); envio de e-mail 2 por hora no projeto inteiro com o SMTP padrão. Com o uso atual (poucas dezenas de usuários, uma renovação por hora cada), sobra folga; o risco é alguém esgotar a cota de login de todos, e por isso há também o limite só por IP. Dá para o Supabase contar por usuário com o header `Sb-Forwarded-For`, mas ele exige a chave secreta na API (ver TODO.md, "A decidir").
- **Logs:** nunca senha nem tokens; só o `error_code` do Supabase quando ele recusa.

#### Configurar o Supabase Auth (uma vez, no painel)

Não dá para fazer por código daqui. No painel do projeto (`ddpicszgihwekcvyevmd`):

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
   Os links valem pelo prazo de *Email OTP Expiration* (Authentication → Providers → Email; padrão 1 h, máximo 24 h). Para convite, 24 h é mais prático.
4. **SMTP próprio** (Authentication → Emails → SMTP Settings): **obrigatório antes de convidar clientes.** O SMTP padrão do Supabase só entrega para e-mails do time do projeto e só 2 por hora. Depois de configurar, ajuste o limite de e-mails em Authentication → Rate Limits.
5. **Política de senha** (Authentication → Providers → Email → Password): *Minimum password length* = 8 e *Password requirements* = **No required characters**. O Supabase não tem a combinação do front (maiúscula e número, sem exigir minúscula): "Lowercase, uppercase letters and digits" recusaria `ABCDEFG1`, que o front aceita, e "Letters and digits" só conta letras sem acento (recusaria `Á1234567`). Nos dois casos a recusa viria depois do `/verify`, com o link já gasto. Por isso a regra de verdade fica na API, conferida antes do `/verify`, e o Supabase só garante o mínimo de 8. Senha posta pelo painel ou pela API administrativa não passa pela regra da API.
6. **Chaves:** a API usa a chave **publishable** (`SUPABASE_ANON_KEY`); o CLI de cadastro usa a **secreta** (`SUPABASE_SERVICE_ROLE_KEY`), só na máquina de quem cadastra. Os JWTs são assinados com chave assimétrica ES256 (Project Settings → JWT Keys); a API valida pelo JWKS público, sem segredo. Se um dia girar a chave, crie a nova como *standby* e espere 1 h (cache do JWKS na API) antes de torná-la a atual.
7. **Usuário de desenvolvimento** (`diretoria@lannconsult.com.br`): crie-o em Authentication → Users (*Send invitation*, ou *Create new user* com *Auto Confirm User*) e rode [supabase/producao/usuario_dev_supabase_auth.sql](../supabase/producao/usuario_dev_supabase_auth.sql), que troca o `id_autenticacao` provisório pelo id do Supabase Auth. Se o front continuar usando o token fake, ponha esse id novo em `VITE_AUTH_FAKE_OBJECT_ID` (o script mostra o id no fim).

### Cadastro de usuários pela LANN

Ninguém cria conta sozinho. O CLI convida no Supabase Auth (o e-mail leva a `{FRONT_URL}/definir-senha`) e grava `seg.usuario`, `seg.usuario_cliente`, `seg.usuario_perfil` e `seg.usuario_empresa` numa transação só:

```bash
cd api
uv run python -m app.cli.convidar_usuario \
  --email ana@pjcortes.com.br --nome "Ana Souza" \
  --cliente pj-cortes --papel leitor --perfil "Desenvolvimento"
# opcionais: --perfil "Outro perfil" (repete), --empresas "Empresa A,Empresa B", --tipo lann
```

- `--cliente` é o `slug` de `cfg.cliente`; `--perfil` (pode repetir) e `--empresas` usam os nomes de `cfg.perfil_acesso` e `cfg.empresa` daquele cliente. Sem `--empresas` (ou com `--empresas todas`), todas as empresas do cliente. `--tipo` padrão `cliente` (`lann` para consultores).
- Confere tudo antes de convidar. Se o e-mail já existe em `seg.usuario` ou no Supabase Auth, avisa e para. Se o convite falhar, nada é gravado. Se a gravação falhar depois do convite, o usuário criado no Supabase Auth é apagado (se nem isso der, o comando mostra o id para apagar à mão).
- Precisa, no `.env` de quem roda (nunca no da API publicada): `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (chave secreta), `FRONT_URL` e `DATABASE_URL_ADMIN` (conexão como `postgres`; em produção, a connection string de Project Settings → Database, com a senha do banco).
- O usuário ainda não pode ter vínculo com mais de um cliente (a API nega; ver TODO.md).
- Para desativar alguém: `update seg.usuario set ativo = false where lower(email) = '…'` no SQL Editor. Ele perde o acesso na hora: toda requisição confere `seg.usuario.ativo` (403), e a renovação seguinte apaga o cookie. Para tirar também o login do Supabase, apague o usuário em Authentication → Users.

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

# Plano: o que falta para o login e os perfis com RLS

Escrito em 18/11/2026. O código do login, dos 4 perfis fixos, da checagem de acesso e do RLS com o cliente escolhido está pronto (migrations até a 32). O SMTP pelo Resend e o "esqueci a senha" funcionaram de ponta a ponta em 18/11/2026. Este plano cobre o resto, em seis etapas, mais o desenho de duas mudanças: a chave "vê nomes" na matriz e o IP de quem acessa.

Decisões já tomadas (18/11/2026): o Entra ID não volta por enquanto; os 4 perfis fixos ficam; o front vai para a Cloudflare, sem data.

## Ordem e dependências

| Etapa | Quem | Depende de | Pode começar |
|---|---|---|---|
| 1. Painel do Supabase Auth | usuário | nada | já |
| 2. Testes com banco e chave "vê nomes" | Claude | nada | feita em 18/11/2026 (falta o front) |
| 3. Segurança (senhas e chaves) | usuário | nada | já; antes da etapa 5 |
| 4. Convite pela Gestão | usuário e Claude | código na `main` | depois da 2 |
| 5. Publicação (Cloudflare + API) | usuário e Claude | front pronto para publicar; etapa 3 | em espera |
| 6. Front | sessão do front | prompts das etapas 2 e 5 | em partes |

## Etapa 1: painel do Supabase Auth (usuário)

No projeto `sesculmqiwvtpparxflz`:

1. **URL Configuration:** Site URL `http://localhost:5173`; em Redirect URLs, `http://localhost:5173/definir-senha`.
2. **Providers → Email:** "Email OTP Expiration" em 86400; "Allow new users to sign up" desligado; senha com mínimo de 8 e "No required characters".
3. **Rate Limits:** e-mails por hora abaixo do plano do Resend (no grátis, 100 por dia).

Pronto quando: um convite feito pelo CLI de cadastro chega com o link para `localhost:5173/definir-senha` e ainda vale depois de algumas horas.

## Etapa 2: testes com banco e chave "vê nomes" (Claude)

### 2a. Testes com banco

Pelo TODO, os testes `db` de login, sessão, Gestão e cliente escolhido e o pgTAP de 17 a 21 nunca rodaram. O RLS novo ainda não foi visto num banco.

1. Postgres local com o pgserver (sem Docker), as 32 migrations e o `seed.sql`.
2. `uv run pytest -m db` em `api/`: `test_admin_db`, `test_convidar_usuario`, `test_auth_uso`, `test_auth_cadastro`, `test_visao_geral` e `test_paineis`.
3. pgTAP de verdade: o pgTAP também se instala como SQL puro (`pgtap.sql`), sem `create extension`. Se funcionar no pgserver, rodar `supabase/tests/database/*.test.sql` com ele.
4. O teste que já falhava (`test_fluxo_caixa_mes_e_acumulado`, `KeyError: 'destaques'`): achar a causa.

Pronto quando: os testes passam ou cada falha está explicada no TODO.

### 2b. Chave "vê nomes" na matriz

**Problema.** A matriz da Gestão marca se um perfil vê um painel. Ver os **nomes** de Pessoas (a chave "Mostrar nomes" de "Cargos e salários") depende de outra marca, `cfg.perfil_tela.ve_dado_nominal`, que hoje só muda por SQL. Tirar e pôr Pessoas de um perfil pela matriz apaga essa marca sem aviso. Com os 4 perfis fixos, a marca vale para todos do perfil naquele cliente.

**Comportamento novo.**

- A linha de Pessoas na matriz ganha, por perfil, a chave "vê nomes". Ela só aparece onde o perfil vê o painel.
- Equipe comercial e Receber e pagar não ganham a chave: a tela inteira mostra nomes, então ver o painel já é ver os nomes (como hoje).
- Admin BORD: sempre vê nomes (decisão de 10/11/2026), fixo na tela.
- Tirar o painel do perfil apaga a marca de nomes junto; ao pôr de volta, volta sem nomes. A diferença para hoje é que isso fica visível na matriz.

**Contrato** (`app/admin/contrato.py`):

- `LinhaDaMatriz` ganha `nomes: dict[str, bool] | None`: `null` nos painéis sem chave; nos com chave, perfil → vê nomes (admin sempre `true`).
- Rota nova: `PUT /api/admin/clientes/{slug}/perfis/{perfil}/paineis/{codigo}/nomes`, corpo `{"veNomes": bool}`, 204. Respostas 422: "Este painel não tem nomes para liberar." (painel fora da lista), "Este perfil não vê o painel." (sem a linha da matriz) e "O Admin BORD sempre vê os nomes." (perfil admin). Perfil inexistente: 404, como na rota da matriz.

**Código.**

- `app/admin/catalogo.py`: no `Painel`, um campo `nomes_opcionais` (hoje só PES-01), tirado do `NOMES` de `app/telas/paineis/router.py`, para as duas listas não se separarem.
- `app/admin/clientes.py`: `SQL_MATRIZ` passa a ler `pt.ve_dado_nominal`; a montagem da linha preenche `nomes`; função `mudar_nomes`.
- **Sem migration.** As políticas da migration 27 deixam o admin inserir e apagar em `cfg.perfil_tela`, mas não atualizar. A troca vira `delete` + `insert` na mesma transação (a tabela não tem outros campos a perder). Alternativa, se preferir um `update`: migration com `grant update (ve_dado_nominal)` e uma política de update só para o admin. Fica para quando houver outra migration.
- Log em `seg.log_alteracao`, como a matriz (`cfg.perfil_tela`, `update`, antes e depois).
- Cache: nada a fazer. A chave do cache dos painéis já leva o `ve_dado_nominal` de quem pede, e a marca é lida a cada pedido.

**Testes.** `db` em `test_admin_db.py`: liga e desliga, os três 422, a linha da matriz com `nomes`, a chave "Mostrar nomes" aparecendo para o perfil marcado e a rota de nomes passando de 403 para 200. Sem banco: a montagem da matriz com `nomes`.

**Docs e front.** `api/openapi.json` regenerado, README da Gestão, CLAUDE.md (a frase "PES-01 só diretoria e RH pelo cadastro" muda) e o TODO (o item "Perfis de diretoria e RH da PJ" vira "marcar quem vê os nomes de Pessoas na PJ"). Prompt para o front: a chave na linha de Pessoas, por perfil.

## Etapa 3: segurança (usuário)

Em 26/10/2026 a senha do `postgres` do projeto novo apareceu numa mensagem de erro, e as chaves do `.env` na saída de uma ferramenta.

1. Trocar a senha do `postgres` (Project Settings → Database).
2. Gerar uma chave secreta nova e apagar a antiga (Project Settings → API Keys).
3. Trocar a senha do `api_site` (`alter role api_site with password '…'` no SQL Editor).
4. Atualizar onde cada uma está: `api/.env` (`DATABASE_URL`, `DATABASE_URL_ADMIN`, `SUPABASE_SERVICE_ROLE_KEY`), `jobs/carga/.env` e os Secrets do GitHub (`SUPABASE_SECRET_KEY`, `DATABASE_URL_CARGA` se usar a senha do `postgres`).
5. Rodar o `diario.yml` à mão e abrir uma tela pela API local para conferir.
6. Pausar o projeto antigo (`ddpicszgihwekcvyevmd`).

## Etapa 4: convite pela Gestão (usuário e Claude)

1. Levar o código para a `main` pelo fluxo do CONTRIBUTING (PR para `dev`, depois `release/X.Y.Z` para `main`). Claude prepara commits e textos dos PRs quando pedido.
2. Variável `FRONT_URL` no GitHub. Com os modelos de e-mail usando `{{ .SiteURL }}`, o link segue a Site URL; o `FRONT_URL` do job só vai no `redirect_to`.
3. Token fine-grained do GitHub (só este repositório, *Actions: Read and write*) em `GITHUB_CONVITES_TOKEN` e `GITHUB_CONVITES_REPO` no `api/.env` local. Anotar a data de vencimento.
4. Teste: convidar um e-mail de teste pela Gestão no front local, ver o job rodar e criar a senha pelo link.

## Etapa 5: publicação (em espera; Cloudflare)

O front vai para a Cloudflare. A API não roda na Cloudflare (Python com psycopg): continua a recomendação de Azure Container Apps, na região **Brazil South**, perto do banco em São Paulo.

> **Atualização de 19/11/2026:** o usuário escolheu o **Azure App Service** (Aplicativo Web Linux, Python 3.12, Brazil South, modelo "Código") no lugar do Container Apps. O workflow (`.github/workflows/deploy-api.yml`, sem imagem nem registry) e o segredo do proxy estão prontos; o passo a passo está em [api/README.md](../api/README.md#publicar-no-azure-app-service). Os itens 1 e 2 abaixo valem com essa troca; o resto continua.

**Mesmo domínio.** A API não tem CORS e o cookie `ovx_refresh` é do domínio do front, então `/api/*` precisa sair do mesmo domínio. Na Cloudflare, um Worker na rota `<domínio>/api/*` repassa o pedido à API. Uma Origin Rule faz o mesmo, mas sem como mandar o IP de quem acessa (abaixo).

**O que preparar quando o front tiver data:**

1. Workflow que gera a imagem do `api/Dockerfile` e a publica (GitHub Actions → Azure Container Registry → Container Apps).
2. Worker do `/api/*`, com o IP de quem acessa e o segredo do proxy (ver "IP de quem acessa").
3. Variáveis da API: `AMBIENTE=prod`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `FRONT_URL`, `DATABASE_URL` (`api_site`), `GITHUB_CONVITES_*` e as do proxy. Nunca `SUPABASE_SERVICE_ROLE_KEY` nem `DATABASE_URL_ADMIN`.
4. Uma réplica só, enquanto o limite de tentativas for em memória.
5. Site URL do Supabase Auth na URL de produção (os e-mails pedidos pela API local passam a levar para produção).
6. Admin BORD (`admin_bord.sql`), pessoas, painéis e matriz pela Gestão.
7. Depois de tudo no ar: migration que tira as funções de sessão de 3 argumentos.

## Etapa 6: front

- Contar como uso o tempo na tela sem pedido à API (com atividade e o último pedido há mais de ~10 min, chamar `renovar`). Prompt a escrever.
- Chave "vê nomes" na matriz (etapa 2b).
- Conferir o que dos prompts já entregues (login, empresa escolhida, Gestão, convite, empresa nova) já está no front.

## IP de quem acessa

### Onde o IP importa

1. **Limites da própria API** (`app/auth/limite.py`): 5 senhas erradas por e-mail e IP, 50 logins por IP, 3 "esqueci a senha" por e-mail e IP. Hoje o IP vem de `request.client.host`, que o uvicorn monta com o `X-Forwarded-For` dos proxies em `FORWARDED_ALLOW_IPS`.
2. **Limites do Supabase Auth:** todas as chamadas saem do IP da API e somam numa cota só (`/token` 150, `/verify` 30 e `/recover` 30 a cada 5 min).

### O problema com a Cloudflare

Pelo Worker, a API recebe o pedido vindo da Cloudflare. O IP de quem acessa chega ao Worker (`CF-Connecting-IP`), mas não à API, a menos que o Worker o mande. E a API não pode confiar num cabeçalho de IP que qualquer um consiga mandar direto a ela.

### Desenho

> **Atualização de 19/11/2026:** feito com outro desenho, mais simples. O Worker manda `X-Proxy-Segredo` (o segredo) e **sobrescreve** o `X-Forwarded-For` com o `CF-Connecting-IP`. A API recusa com 403 todo pedido sem o segredo certo (menos `/health`), então o uvicorn pode ler o `X-Forwarded-For` de qualquer origem (`--forwarded-allow-ips="*"`, que usa o primeiro IP da lista; o App Service acrescenta o dele no fim). Não há `X-Overnex-IP` e o IP não depende de código da API; o App Service não precisa limitar a entrada aos IPs da Cloudflare, porque sem o segredo nada passa. O item 4 (regra de limite da Cloudflare) continua. Código em `api/app/core/proxy.py`. O desenho original fica abaixo, para registro.

1. O Worker manda dois cabeçalhos: `X-Overnex-IP` (o `CF-Connecting-IP`) e `X-Overnex-Proxy` (um segredo compartilhado, nos secrets do Worker e da API).
2. A API ganha `PROXY_SEGREDO` na configuração. Com o segredo certo, o IP do limite vem de `X-Overnex-IP`; sem ele ou com outro, vem da conexão, como hoje. Comparação em tempo constante; nem o segredo nem o IP vão para o log.
3. Container Apps aceitando só pedidos da Cloudflare (lista de IPs da Cloudflare no ingress), para ninguém pular o Worker.
4. Regra de limite da Cloudflare em `/api/auth/*` (o plano grátis tem uma regra de rate limiting): corta abuso antes de chegar à API e ao Supabase.

Os itens 1 e 2 são código do Worker e da API; ficam para a etapa 5.

### Mandar o IP ao Supabase Auth

| Opção | Como | A favor | Contra |
|---|---|---|---|
| A. Não mandar (recomendada agora) | subir os limites em Authentication → Rate Limits se o aviso "Limite do Supabase Auth atingido" aparecer no log | nada a construir; a chave secreta continua fora da API | um abuso que passe pelas camadas acima ainda gasta a cota de todos |
| B. Chave secreta na API | a API chama o GoTrue com a chave secreta e `Sb-Forwarded-For: <IP>` | cota por pessoa | a API publicada passa a ter poder de administrador (cria e apaga logins, lê e grava tudo sem RLS); quem invadir a API leva tudo |
| C. Edge Function intermediária | uma Edge Function do Supabase guarda a chave secreta, aceita só as chamadas de login (`/token`, `/verify`, `/recover`, `/logout`, `PUT /user`) e só da API (segredo próprio), e repassa com `Sb-Forwarded-For` | cota por pessoa; quem invadir a API só consegue o que a API já faz | mais uma peça para publicar e manter (`supabase functions deploy`) e mais uma ida e volta em cada login e renovação |

**Recomendação:** A agora, junto com o desenho acima (o IP certo nos limites da API e a regra da Cloudflare). Se o aviso de limite aparecer mesmo com os limites do Supabase mais altos, ir para C. B só se C não for possível.

Para C, se um dia for: `app/auth/gotrue.py` passa a apontar para a URL da função (`SUPABASE_AUTH_URL`) com o cabeçalho do IP; a função fica em `supabase/functions/auth-proxy/`. Nenhuma mudança no contrato do front.

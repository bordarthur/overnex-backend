# Migração do Supabase para São Paulo (sa-east-1)

Roteiro para trocar o projeto de produção (`ddpicszgihwekcvyevmd`, us-east-1) por um projeto novo em **sa-east-1**. O Supabase não muda a região de um projeto: cria-se outro e migra-se o que não dá para recriar.

Por quê: medido em 26/10/2026 a partir de Fortaleza, abrir uma conexão leva ~105–120 ms até us-east-1 e ~68–79 ms até sa-east-1. O ganho de verdade vem quando a API for hospedada na mesma região do banco (Azure Brazil South): cada ida ao banco cai para poucos milissegundos.

## O que vai e como

| O quê | Como chega ao projeto novo |
|---|---|
| Estrutura (schemas `cfg`, `seg`, `mart`, roles `api_site` e `carga`, RLS, funções) | `supabase db push` das 16 migrations |
| Dados de `cfg` (cliente, empresas, mapa, fontes, parâmetros, feriados, de-para da DRE, perfis, painel) | cópia dos dados (`pg_dump --data-only`) |
| Dados de `seg` (usuários, vínculos, perfis, empresas, logs de acesso) | cópia dos dados |
| Usuários do Auth (`auth.users`, `auth.identities`), com o mesmo id e a mesma senha | cópia dos dados. Com o mesmo id, `seg.usuario.id_autenticacao` e o `VITE_AUTH_FAKE_OBJECT_ID` do front continuam valendo. As sessões não vão: todos entram de novo |
| Dados de `mart` (tabelas da PJ, resumo mensal, histórico de cargas) | **não copiar**: o `diario.yml` refaz tudo a partir do Azure SQL (snapshot → bucket → carga) |
| Arquivos do bucket `overnex-bucket` | **não copiar**: o snapshot regrava. Só criar o bucket |
| Senhas dos roles `api_site` e `carga` | definir de novo (as migrations criam os roles sem senha) |
| Chaves (publishable e secret), URL do projeto, connection strings | novas: trocar em todo lugar da lista abaixo |
| Configuração do Auth no painel | refazer à mão (passo 3) |

## Onde trocar (inventário de 26/10/2026)

| Lugar | Variáveis |
|---|---|
| `api/.env` (máquina de quem roda a API e o CLI de cadastro) | `DATABASE_URL` (role `api_site`, pooler em **modo transação**, porta 6543), `SUPABASE_URL`, `SUPABASE_ANON_KEY` (publishable). Para o CLI: `SUPABASE_SERVICE_ROLE_KEY` (secret) e `DATABASE_URL_ADMIN` (role `postgres`) |
| `jobs/carga/.env` (carga rodada à mão) | `SUPABASE_URL`, `SUPABASE_SECRET_KEY` e, se houver, `DATABASE_URL_CARGA` (role `carga`, pooler em **modo sessão**, porta 5432) |
| `jobs/snapshot` (se rodar à mão) | `SUPABASE_URL`, `SUPABASE_SECRET_KEY` |
| GitHub → Settings → Secrets and variables → Actions | Variables `SUPABASE_URL` e `SUPABASE_BUCKET` (o mesmo nome, `overnex-bucket`); Secrets `SUPABASE_SECRET_KEY` e `DATABASE_URL_CARGA`. Os do Azure SQL (`SQL_*`, `AZURE_*`) não mudam |
| Ambiente da API publicada (quando existir) | `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_ANON_KEY` |
| Docs do repositório | `api/.env.example` (URL de produção), `api/README.md` (id do projeto no passo do Auth), `CLAUDE.md` (latência e região), `TODO.md` (itens que citam us-east-1) |
| Supabase CLI | `supabase link --project-ref <novo>` |
| Front | nada. Ele só fala com a API; o `VITE_AUTH_FAKE_OBJECT_ID` só muda se os usuários do Auth forem recriados em vez de copiados |
| `supabase/config.toml` | nada (é do Supabase local) |

## Passo a passo

### 0. Antes

- [ ] Projeto atual com todas as migrations aplicadas: `supabase migration list --linked` deve listar as 16 como aplicadas. A cópia dos dados só funciona entre bancos com a mesma estrutura.
- [ ] Ferramentas: `pg_dump` e `psql` na **versão 17** (a do servidor) ou maior; o Postgres 17 do Windows (ver `api/README.md`) já traz os dois.
- [ ] Hora: depois da carga das 9h e de a conferir. Desligue o `diario.yml` (Actions → snapshot-dataflow → Disable workflow) até o fim do passo 6, para nada gravar no projeto antigo durante a cópia.
- [ ] Conferir o plano e o preço em sa-east-1 (o projeto novo precisa caber no plano; hoje o banco tem ~200–250 MB).

### 1. Projeto novo

- [ ] Criar o projeto em **South America (São Paulo)**, mesma versão do Postgres do atual. Guarde a senha do `postgres`.
- [ ] **Project Settings → JWT Keys:** assinatura com chave **assimétrica ES256**. A API valida o token pelo JWKS público e não aceita o segredo HS256 legado. Se o projeto nascer com o legado, migre para as chaves novas antes de criar usuários.
- [ ] **Project Settings → API Keys:** anote a publishable (`sb_publishable_…`) e a secret (`sb_secret_…`).
- [ ] **Data API:** deixe exposto só o `public`, como no atual: a API não usa PostgREST, e `cfg`, `seg` e `mart` não devem ficar expostos.
- [ ] **Storage:** criar o bucket `overnex-bucket`, **privado**.

### 2. Estrutura e senhas dos roles

```bash
supabase link --project-ref <novo>
supabase db push            # aplica as 16 migrations
```

No SQL Editor do projeto novo (senhas novas; não reaproveite as antigas):

```sql
alter role api_site with login password '<segredo>';
alter role carga    with login password '<segredo>';
```

### 3. Auth no painel

Refazer, no projeto novo, os passos de [api/README.md](../api/README.md#configurar-o-supabase-auth-uma-vez-no-painel):

- [ ] Sign up desligado e "Confirm email" ligado.
- [ ] Site URL e Redirect URLs (`/definir-senha` de produção e de dev).
- [ ] Modelos de Convite e de Recuperação em português, com `{{ .TokenHash }}`; Email OTP Expiration.
- [ ] SMTP próprio, se o atual já tiver, e o limite de e-mails em Rate Limits.
- [ ] Senha com mínimo de 8 e "No required characters".

### 4. Copiar os dados de `cfg`, `seg` e do Auth

Conexões como `postgres`, pelo pooler em **modo sessão** (porta 5432; a connection string está em Project Settings → Database). Antes, confira que o `postgres` passa pelo RLS (`select rolbypassrls from pg_roles where rolname = current_user;` deve dar `true`); senão o `pg_dump` para com erro de row-level security.

```bash
# do projeto ANTIGO
pg_dump "$ANTIGO" --data-only --no-owner --no-privileges --schema=cfg --schema=seg -f cfg_seg.sql
pg_dump "$ANTIGO" --data-only --no-owner --no-privileges \
        --table=auth.users --table=auth.identities -f auth.sql

# no projeto NOVO, numa transação só (sem disparar triggers nem conferir FKs na ordem)
psql "$NOVO" --single-transaction -v ON_ERROR_STOP=1 \
     -c "set session_replication_role = replica" -f auth.sql -f cfg_seg.sql
```

Os arquivos `.sql` têm dados de clientes e o hash das senhas: guarde-os fora do repositório e apague-os depois.

- [ ] Conferir as contagens nos dois projetos:

```sql
select 'cfg.cliente' t, count(*) from cfg.cliente union all
select 'cfg.empresa', count(*) from cfg.empresa union all
select 'cfg.fonte_carga', count(*) from cfg.fonte_carga union all
select 'cfg.parametro_cliente', count(*) from cfg.parametro_cliente union all
select 'cfg.conta_dre', count(*) from cfg.conta_dre union all
select 'cfg.perfil_tela', count(*) from cfg.perfil_tela union all
select 'seg.usuario', count(*) from seg.usuario union all
select 'seg.log_acesso', count(*) from seg.log_acesso union all
select 'auth.users', count(*) from auth.users;
```

### 5. Trocar as configurações

- [ ] GitHub: Variables `SUPABASE_URL` e `SUPABASE_BUCKET`; Secrets `SUPABASE_SECRET_KEY` e `DATABASE_URL_CARGA` (`carga.<novo-ref>` no host `…sa-east-1.pooler.supabase.com`, porta **5432**).
- [ ] `api/.env`: `DATABASE_URL` (`api_site.<novo-ref>`, porta **6543**), `SUPABASE_URL`, `SUPABASE_ANON_KEY`; para o CLI, `SUPABASE_SERVICE_ROLE_KEY` e `DATABASE_URL_ADMIN`.
- [ ] `jobs/carga/.env` (e o do snapshot, se usar).

### 6. Encher o mart

- [ ] Reativar o `diario.yml` e rodá-lo à mão (Run workflow). O snapshot regrava o bucket e a carga refaz as 25 tabelas e o resumo mensal.
- [ ] Conferir `mart.carga_fonte` (status e mensagem das 25 fontes) e o tamanho do banco.
- [ ] Se o código dos painéis comerciais já estiver em `main` e a parte 2 do `pj_cortes_telas_comerciais.sql` já tiver rodado, a carga grava também o `faturado_vendedor` (vem junto com o `cfg` copiado).

### 7. Conferir

- [ ] Login pelo front (`/api/auth/entrar`) com o usuário de desenvolvimento: o token sai do projeto novo e a API o aceita pelo JWKS.
- [ ] `/api/sessao`, Visão geral e os três painéis abrem; os números batem com os do projeto antigo no mesmo snapshot.
- [ ] Medir de novo os tempos (os mesmos do diagnóstico de 26/10/2026).
- [ ] "Esqueci a senha" envia o e-mail e o link abre `/definir-senha`.

### 8. Depois

- [ ] Projeto antigo: **pausar**, não apagar, por algumas semanas (volta atrás = trocar as configurações de novo). Antes de pausar, gire a chave secret e a senha do `api_site` dele.
- [ ] Atualizar as docs da lista acima (id do projeto, região, latência no `CLAUDE.md`).
- [ ] Hospedar a API no Azure **Brazil South**, junto do banco (item "Onde hospedar" do TODO).
- [ ] Apagar o projeto antigo quando tudo estiver estável.

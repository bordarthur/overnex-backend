# TODO — Overnex backend

Pendências depois das rodadas 1 (API + banco) e 2 (carga + agendamento).

## Antes de ligar em produção

- [ ] **Desativar o workflow antigo** `snapshot-dataflow.yml` no repositório `dataflow`. Se ele continuar ativo junto com `.github/workflows/diario.yml`, o snapshot roda duas vezes por dia.
- [ ] **Definir as senhas dos roles** no Supabase de produção. Os roles nascem sem LOGIN; ver [supabase/README.md](supabase/README.md).
  ```sql
  alter role api_site with login password '<segredo>';
  alter role carga    with login password '<segredo>';
  ```
- [ ] **Cadastrar o Secret `DATABASE_URL_CARGA`** no GitHub, com o pooler em modo sessão (porta 5432). Os demais segredos e variáveis o snapshot já usa; ver [jobs/carga/README.md](jobs/carga/README.md).
- [ ] **Decidir se usamos um GitHub environment** para os segredos. Hoje os jobs `snapshot` e `carga` leem os segredos do repositório. Se criarmos um environment, colocar o mesmo `environment:` nos dois jobs.
- [ ] **Validar com Docker**, que não foi possível no ambiente de desenvolvimento:
  - `supabase start && supabase db reset && supabase test db`
  - `cd api && uv run pytest`
  - `cd jobs/carga && uv run pytest` (usa o Storage local de verdade)
  - `docker build -t overnex-api api/`

## Regras de negócio

- [ ] **Aplicar `cfg.regra_lancamento` na carga** (reclassificar/excluir) antes da agregação. O ponto está marcado em [jobs/carga/carga/lancamento.py](jobs/carga/carga/lancamento.py).
- [ ] **FIN-01 com regra real.** Hoje a tela mostra a soma direta do mart, com rótulos "Descritivo N" e sem status do farol no hero; ver [api/app/telas/fin_01.py](api/app/telas/fin_01.py).
- [ ] **Usar `cfg.parametro` e `cfg.painel`.** As tabelas existem e têm dados no seed, mas nada as lê ainda.

## API

- [ ] **Admin**: cadastros de clientes, empresas, perfis, usuários e vínculos, e regras, restritos ao papel `admin` do próprio cliente; ver [api/app/admin/router.py](api/app/admin/router.py). O banco já tem os grants de escrita, mas faltam as políticas RLS de escrita, então hoje o RLS nega.
- [ ] **Gravar `seg.log_alteracao`** (por trigger ou pela API) nas alterações de cadastro. Hoje só existem a tabela e a política de insert.
- [ ] **PDF das telas** a partir do mesmo `ScreenPayload`, com a mesma checagem de acesso; ver [api/app/pdf/router.py](api/app/pdf/router.py).
- [ ] **Usuário com mais de um cliente** (consultor LANN). Hoje a etapa 2 da checagem de acesso nega quem tem mais de um vínculo; ver [api/app/seguranca/acesso.py](api/app/seguranca/acesso.py).

## Carga

- [ ] **Execução interrompida** (timeout ou runner morto) deixa `mart.carga` e `mart.carga_fonte` em `rodando`. Marcar essas linhas como `erro` na execução seguinte.
- [ ] **Avisar quando a carga falha.** Hoje o único aviso é o e-mail padrão de falha do GitHub Actions.
- [ ] **Ler o Parquet em lotes.** Hoje a carga lê o arquivo inteiro em memória.
- [ ] **Tipos de fonte além de `lancamento`**: novo valor no check de `cfg.fonte_carga.tipo` e um módulo de carga por tipo.

## Infraestrutura

- [ ] **Mover o agendamento para um Container Apps Job no Azure.** Só o agendamento muda: o CLI (`uv run python -m carga`) fica igual.

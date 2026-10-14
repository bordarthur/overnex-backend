# TODO — Overnex backend

Pendências depois das rodadas 1 (API + banco), 2 (carga + agendamento) e 3 (tabelas de dados).

## Antes de ligar em produção

- [ ] **Desativar o workflow antigo** `snapshot-dataflow.yml` no repositório `dataflow`. Se ele continuar ativo junto com `.github/workflows/diario.yml`, o snapshot roda duas vezes por dia.
- [ ] **Definir as senhas dos roles** no Supabase de produção. Os roles nascem sem LOGIN; ver [supabase/README.md](supabase/README.md).
  ```sql
  alter role api_site with login password '<segredo>';
  alter role carga    with login password '<segredo>';
  ```
- [ ] **Cadastrar o Secret `DATABASE_URL_CARGA`** no GitHub, com o pooler em modo sessão (porta 5432). Os demais segredos e variáveis o snapshot já usa; ver [jobs/carga/README.md](jobs/carga/README.md).
- [ ] **Decidir se usamos um GitHub environment** para os segredos. Hoje os jobs `snapshot` e `carga` leem os segredos do repositório. Se criarmos um environment, colocar o mesmo `environment:` nos dois jobs.
- [ ] **Confirmar o espaço do banco antes de ativar as tabelas de dados.** São ~7 milhões de linhas no bucket da Nilo Maia (vendas 2,0 mi, receitas 1,3 mi, lançamentos 0,8 mi…), na ordem de 1,5–2,5 GB no Postgres, com picos maiores durante a troca diária. O plano gratuito do Supabase tem 500 MB e, se passar, o banco fica só leitura. Rodar o [supabase/producao/nilo_maia_tabelas.sql](supabase/producao/nilo_maia_tabelas.sql) depois do `db push` (ele ativa só lançamentos) e ativar as demais aos poucos, olhando **Settings → Usage**.
- [ ] **Validar com Docker**, que não foi possível no ambiente de desenvolvimento:
  - `supabase start && supabase db reset && supabase test db`
  - `cd api && uv run pytest`
  - `cd jobs/carga && uv run pytest` (usa o Storage local de verdade)
  - `docker build -t overnex-api api/`

## Regras de negócio

- [ ] **Aplicar `cfg.regra_lancamento` na carga** (reclassificar/excluir) antes da agregação. O ponto está marcado em [jobs/carga/carga/lancamento.py](jobs/carga/carga/lancamento.py).
- [ ] **FIN-01 com regra real, lendo `mart.f_lancamentos_agrupados_nilo_maia`.** O arquivo de lançamentos da Nilo Maia não está no formato `Empresa`/`Data`/`Id_Descritivo`/`Valor` e passou a ser carregado como tabela de dados, então `mart.lancamento` fica vazio para esse cliente. Falta decidir qual data (competência ou caixa), qual valor (realizado ou previsto) e como a conta vira linha do demonstrativo; ver [api/app/telas/fin_01.py](api/app/telas/fin_01.py).
- [ ] **Datas como texto nas tabelas de dados.** O DW manda as datas como texto (`Data`, `DATA_COMPETENCIA`, `en_datemi`…), e elas estão em `text` no mart. Para filtrar e ordenar por data no banco, é preciso saber o formato e criar colunas `date` (só adição).
- [ ] **Tabelas sem coluna de empresa** (15 das 21, ex. vendas, receitas, compras): todo usuário do cliente vê todas as linhas. Se alguma tiver filial ou empresa em outra coluna (ex. `CODFILIAL`), dá para mapear e separar por empresa.
- [ ] **Usar `cfg.parametro` e `cfg.painel`.** As tabelas existem e têm dados no seed, mas nada as lê ainda.

## API

- [ ] **Admin**: cadastros de clientes, empresas, perfis, usuários e vínculos, e regras, restritos ao papel `admin` do próprio cliente; ver [api/app/admin/router.py](api/app/admin/router.py). O banco já tem os grants de escrita, mas faltam as políticas RLS de escrita, então hoje o RLS nega.
- [ ] **Gravar `seg.log_alteracao`** (por trigger ou pela API) nas alterações de cadastro. Hoje só existem a tabela e a política de insert.
- [ ] **PDF das telas** a partir do mesmo `ScreenPayload`, com a mesma checagem de acesso; ver [api/app/pdf/router.py](api/app/pdf/router.py).
- [ ] **Endpoints de tela agregados para as tabelas grandes.** `/api/dados/{tabela}` entrega linha a linha (até 5000 por página); vendas tem 2 milhões de linhas. As telas devem pedir dados já agregados por endpoints próprios, como o FIN-01.
- [ ] **Liberar as tabelas aos perfis** (`cfg.perfil_tabela`) quando os usuários e perfis de produção forem cadastrados.
- [ ] **Usuário com mais de um cliente** (consultor LANN). Hoje a etapa 2 da checagem de acesso nega quem tem mais de um vínculo; ver [api/app/seguranca/acesso.py](api/app/seguranca/acesso.py).

## Carga

- [ ] **Execução interrompida** (timeout ou runner morto) deixa `mart.carga` e `mart.carga_fonte` em `rodando`. Marcar essas linhas como `erro` na execução seguinte.
- [ ] **Avisar quando a carga falha.** Hoje o único aviso é o e-mail padrão de falha do GitHub Actions.
- [ ] **Ler o Parquet em lotes.** Hoje a carga lê o arquivo inteiro em memória.
- [ ] **Arquivo vazio** falha (não há `_data_snapshot` para validar) e mantém o mart. Para tabelas que podem ficar vazias de verdade, decidir se o certo é esvaziar a tabela no mart.

## Infraestrutura

- [ ] **Mover o agendamento para um Container Apps Job no Azure.** Só o agendamento muda: o CLI (`uv run python -m carga`) fica igual.

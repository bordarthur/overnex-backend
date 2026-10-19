# TODO — Overnex backend

Atualizado em 19/10/2026.

## Feito

- **Rodada 1 — banco e API:** schemas `cfg`/`seg`/`mart`, roles `api_site` e `carga`, RLS em tudo, seed "Padrão LANN" e testes pgTAP. API FastAPI com login do Entra ID, checagem de acesso em 6 etapas, FIN-01 de exemplo, `/health` e Dockerfile.
- **Rodada 2 — carga e agendamento:** carga diária do Storage para o mart, com validação de data e troca em uma transação. Workflow `diario.yml`: snapshot às 9h e, se der certo, a carga.
- **Rodada 3 — tabelas de dados:** as 21 tabelas do bucket da Nilo Maia têm tabela no mart com as mesmas colunas (migration 5). A carga genérica (tipo `tabela`) preenche essas tabelas, e o front lê por `GET /api/dados/{tabela}`. O inventário do bucket (`python -m carga.inspecionar`) gera o SQL de tabelas novas.
- **Janela de datas** (migration 6): as tabelas de movimento guardam só os últimos meses. Em produção: 3 meses nas médias, 1 mês sugerido para receitas e vendas.
- **Proteção contra disco cheio:** a carga para logo no início se o banco estiver só leitura, e para a carga inteira se o disco encher no meio.
- **Produção:**
  - migrations 1 a 6 aplicadas;
  - cliente, empresas e mapa da Nilo Maia cadastrados;
  - `nilo_maia_tabelas.sql` e `nilo_maia_janela.sql` rodados;
  - Secret `DATABASE_URL_CARGA` e senha do role `carga` funcionando;
  - banco recuperado do modo só leitura de 14/10 (tabelas grandes esvaziadas com `truncate` e recarregadas com janela).
- **Git:** branch padrão `main` no GitHub e `master` apagada. Repositório `dataflow` arquivado (o snapshot roda só pelo `diario.yml`).
- **Rodada 4 — API para o front (sem login):** `GET /api/sessao` (usuário, cliente com `slug`, empresas, telas liberadas e períodos) e `GET /api/telas/visao-geral` (VG-01) com o contrato do front em Pydantic, em camelCase ([api/app/telas/visao_geral/contrato.py](api/app/telas/visao_geral/contrato.py)). `empresa` opcional: sem ela, a tela vale para todas as empresas do vínculo. `dadosAte` e `atualizadoEm` vêm da última carga `ok`. Seed com VG-01 liberada para Ana e Bruno, e `VISAO_GERAL_DEMO=1` (só dev) devolve as seções do mockup. `openapi.json` regenerado.
- **Plano do Supabase:** continua no gratuito (500 MB) por enquanto. Em 16/10 o uso chegou perto de 500 MB, e as tabelas com janela passaram para 1 mês (o mês do snapshot).

## Conferir em produção

- [ ] **Tamanho do banco com a janela de 1 mês.** Depois do `truncate` e da primeira carga com 1 mês, conferir:
  ```sql
  select pg_size_pretty(pg_database_size(current_database())) as banco;
  ```
  No plano gratuito, manter abaixo de ~400 MB para ter folga no pico da troca diária.
- [ ] **Vendas no mês anterior fechado** (migration 7): `f_vendas_nilo_maia` tinha 0 linhas porque o DW não tem vendas do mês vigente (`en_datemi`). Depois do `db push`, configurar `meses_fechados = true` nela e conferir se ela enche. Avaliar o mesmo para as outras tabelas com janela.
- [ ] **Vendas de setembro só até 19/09.** `f_vendas_nilo_maia` encheu em 19/10, mas a última venda em `en_datemicao` é de 19/09: o faturado de setembro (R$ 21,35 mi, 56% da meta de R$ 38 mi) parece incompleto. Conferir se o DW manda só os últimos 30 dias, se a janela corta algo e o tamanho do banco.

## Antes de publicar a API

- [ ] **Senha do role `api_site`** no Supabase (`alter role api_site with login password '…'`).
- [ ] **App registration no Entra ID** para a API (`ENTRA_TENANT_ID`, `ENTRA_AUDIENCE`). Os usuários dos clientes entram como convidados no tenant, ou o app passa a aceitar vários tenants (isso pede ajuste na validação do token).
- [ ] **Usuários e perfis de produção:** `seg.usuario`, `seg.usuario_cliente`, `seg.usuario_perfil` e `seg.usuario_empresa`, além de `cfg.perfil_tela` e `cfg.perfil_tabela`. Sem isso, toda tela responde 403.
- [ ] **Onde hospedar** (recomendado: Azure Container Apps, com a imagem gerada no GitHub Actions) e como front e API dividem o mesmo domínio (`/api` → API), já que a API não tem CORS.
- [ ] **Usuário de desenvolvimento da Nilo Maia** ([supabase/producao/nilo_maia_usuario_dev.sql](supabase/producao/nilo_maia_usuario_dev.sql)): o `object_id_entra` é provisório, para o token fake da API local. Trocar pelo object id real do Entra quando o login entrar.
- [ ] **VG-01 em `cfg.perfil_tela` de produção** para os perfis que veem a Visão geral (sem isso, a tela responde 403 e some de `/api/sessao`).
- [ ] **Validar com Docker**, o que não foi possível na máquina de desenvolvimento. Os testes `db` da rodada 4 ([api/tests/test_visao_geral.py](api/tests/test_visao_geral.py)) ainda não rodaram contra o banco. Se não houver Docker, criar um workflow de CI no GitHub Actions que rode tudo isso a cada push:
  - `supabase start && supabase db reset && supabase test db`;
  - `uv run pytest` em `api/` e em `jobs/carga/`;
  - `docker build` da API.

## Para o front consumir (Visão geral)

O front (`overnex-frontend`) já desenha a Visão geral a partir de um JSON fixo; o contrato está em `src/contracts/visaoGeral.ts` e o exemplo completo em `src/api/fixtures/visao-geral.json`, no repositório do front. O front não calcula nada: todo número, status, ponteiro, delta e texto precisa vir pronto da API.

- [ ] **No front:** proxy `/api` → `http://127.0.0.1:8000` no `vite.config.ts`, cliente HTTP no lugar do `criarMockClient` (token `fake:<object_id>` em dev), filtros a partir de `/api/sessao` e tipos gerados do `api/openapi.json` (apagar o contrato escrito à mão). Atenção: `dadosAte` e `atualizadoEm` podem vir `null` (cliente sem carga), o que o contrato escrito à mão não previa.
- [ ] **Seção Vendas com regras provisórias** em [api/app/telas/visao_geral/vendas.py](api/app/telas/visao_geral/vendas.py): 5 cartões (meta, faturado e atingimento com farol; clientes e ticket sem) e o velocímetro "Vendas contra a meta" (mês, acumulado do ano e tendência de 6 meses, vindos de `mart.resumo_mensal`). Confirmar com o negócio: faturado = soma de `en_VALTOT` por `en_datemicao`, meta = `f_metas.Valor` da competência, pedidos = `en_numdoc` distintos, acumulado do ano = meses com faturado e meta. Sem projeção de fechamento: o mês mostrado é fechado. Demais seções ainda vazias.
- [ ] **Aplicar a migration 8 em produção e rodar a carga.** `mart.resumo_mensal` e `cfg.parametro_cliente` (migration 8) e o resumo mensal da carga ([jobs/carga/carga/resumo.py](jobs/carga/carga/resumo.py)) estão prontos; o velocímetro de vendas só aparece depois do `supabase db push` e da primeira carga com o código novo. Outras tabelas (receitas, inadimplência…) ganham resumo com um modelo novo em `MODELOS`.
- [ ] **Regra de cada indicador** (depende de decisão de negócio): tabela de origem, conta, meta ou orçado de comparação e faixas do farol. Indicadores do mockup e tabelas candidatas:
  - Vendas: vendas contra a meta, clientes que compraram, ticket por pedido, linhas por cliente → `f_vendas`, `f_metas`, `f_pedidos`.
  - Custos: CMV por linha de produto → `f_vendas` (custo) ou `f_lancamentos_agrupados`, com `f_orcamento`.
  - Resultado: margem EBITDA, despesas fixas e folha sobre a receita → `f_lancamentos_agrupados`, `f_orcamento`; dívida líquida / EBITDA vem de cadastro (`origem: "cadastro"`).
  - Financeiro: cobertura de caixa, prazo médio de recebimento, ciclo de caixa, contas a pagar lançadas e cartão de inadimplência → `f_saldos_bancarios`, `f_contas_a_receber`, `f_contas_a_pagar`, `f_pagas`, `f_recebidas`, `f_inadimplencia`, `f_estoque`.
- [ ] **Faixas do farol de cada cliente** em `cfg.parametro_cliente` (chave `farol.vendas_meta`, valor `{"atencao": 100, "critico": 90, "min": 80, "max": 110, "meta": 100}`), lidas por [api/app/farol/parametro.py](api/app/farol/parametro.py). Sem parâmetro, vale o padrão provisório do código (os limites do mockup). Definir os da Nilo Maia.
- [ ] **Textos da tela:** o resumo de cada seção (ex.: "A meta escorrega por positivação…"), a referência de cada indicador e o texto do cartão de inadimplência (`Trecho[]`). Decidir se saem de regras fixas na API ou se ficam fora da primeira versão.
- [ ] **Seções por cliente.** Quais seções cada cliente vê e em que ordem, e o campo `modulo` (ex.: Compras e Estoque pertencem ao módulo Distribuidora). Candidato: `cfg.painel`.
- [ ] **Logo do cliente servido pela própria API.** Hoje `logoUrl` só sai quando `cfg.cliente.logo_url` é um caminho do próprio domínio (`/…`); fora disso vem `null`.
- [ ] **Sigla do cliente:** hoje sai das iniciais do nome ("Padrão LANN Alfa" → "PA"). Se precisar de outra, criar a coluna `cfg.cliente.sigla`.
- [ ] **Períodos anteriores:** `/api/sessao` só lista o mês do último snapshot, e a Visão geral não recebe período. Com o histórico mensal, listar os meses fechados e aceitar `mes` na tela.
- [ ] **Mesmo domínio em produção.** O front sobe como Azure Static Web Apps (`staticwebapp.config.json`, CSP `connect-src 'self'`) e a API não tem CORS: `/api` precisa ser servido pelo mesmo domínio (backend vinculado do Static Web Apps ou um proxy na frente dos dois). Complementa o item "Onde hospedar".

## Regras de negócio

- [ ] **FIN-01 com regra real, lendo `mart.f_lancamentos_agrupados_nilo_maia`.** Hoje ele lê `mart.lancamento`, que fica vazio para a Nilo Maia. Falta decidir qual data (competência ou caixa), qual valor (realizado ou previsto) e como a conta vira linha do demonstrativo. Atenção: o FIN-01 mostra 12 meses, e lançamentos está com janela de 3; ver [api/app/telas/fin_01.py](api/app/telas/fin_01.py).
- [ ] **Aplicar `cfg.regra_lancamento`** (reclassificar/excluir) na carga. O ponto está marcado em [jobs/carga/carga/lancamento.py](jobs/carga/carga/lancamento.py).
- [ ] **Datas como texto nas tabelas de dados.** O DW manda as datas como texto (`Data`, `DATA_COMPETENCIA`, `en_datemi`…), e elas estão em `text` no mart. A janela já entende esses formatos; para filtrar e ordenar por data no banco e na API, criar colunas `date` (só adição).
- [ ] **Tabelas sem coluna de empresa** (15 das 21, como vendas, receitas e compras): todo usuário do cliente vê todas as linhas. Se `CODFILIAL` ou outra coluna identificar a empresa, dá para mapear e separar.
- [ ] **Usar `cfg.parametro` e `cfg.painel`.** Existem e têm dados no seed, mas nada os lê ainda.

## API

- [ ] **Endpoints de tela agregados.** `/api/dados/{tabela}` entrega linha a linha, até 5000 por página. As telas devem pedir dados já agregados, em endpoints próprios como o FIN-01.
- [ ] **Admin:** cadastros de clientes, empresas, perfis, usuários e vínculos, e regras, restritos ao papel `admin` do próprio cliente; ver [api/app/admin/router.py](api/app/admin/router.py). Os grants de escrita existem, mas faltam as políticas RLS de escrita.
- [ ] **Gravar `seg.log_alteracao`** nas alterações de cadastro (por trigger ou pela API).
- [ ] **PDF das telas** a partir do mesmo `ScreenPayload`; ver [api/app/pdf/router.py](api/app/pdf/router.py).
- [ ] **Usuário com mais de um cliente** (consultor LANN). Hoje a etapa 2 da checagem nega quem tem mais de um vínculo; ver [api/app/seguranca/acesso.py](api/app/seguranca/acesso.py).

## Carga

- [ ] **Trocar `delete` por `truncate` nas tabelas de um cliente só.** O `delete` + `insert` diário faz cada tabela ocupar até o dobro do tamanho dos dados. Com `truncate`, o espaço antigo é liberado na hora; durante a troca, quem estiver lendo espera alguns segundos em vez de ver os dados antigos.
- [ ] **Execução interrompida** (timeout ou runner morto) deixa `mart.carga` e `mart.carga_fonte` em `rodando`. Marcar essas linhas como `erro` na execução seguinte.
- [ ] **Avisar quando a carga falha.** Hoje o único aviso é o e-mail padrão de falha do GitHub Actions.
- [ ] **Ler o Parquet em lotes.** Hoje cada arquivo é lido inteiro em memória (o de vendas tem 35 MB compactado).
- [ ] **Arquivo vazio** falha e mantém o mart. Para tabelas que podem ficar vazias de verdade, decidir se o certo é esvaziar a tabela.

## Infraestrutura

- [ ] **Mover o agendamento para um Container Apps Job no Azure.** Só o agendamento muda; o CLI (`uv run python -m carga`) fica igual.

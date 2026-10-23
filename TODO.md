# TODO — Overnex backend

Atualizado em 22/10/2026.

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
- **Troca da Nilo Maia pela PJ Cortes (21/10):** as tabelas da Nilo Maia saíram do mart ([nilo_maia_remover_tabelas.sql](supabase/producao/nilo_maia_remover_tabelas.sql)), e o snapshot passou a copiar as 25 tabelas da PJ. Código: `colunas_modelo` em `cfg.fonte_carga` (migration 11) para o DW da PJ, que usa outros nomes de coluna (`en_valortotal`, `en_dataemissao`…), e a Visão geral somando todas as tabelas `f_vendas_*` do cliente (a atual e a Agile, do sistema antigo, até 08/03/2025). CMV da PJ aberto por grupo do produto (`it_grupo`). Tabelas da PJ na migration 12.
- **Seções Resultado e Financeiro da Visão geral (21/10), com regras provisórias:** [resultado.py](api/app/telas/visao_geral/resultado.py), [financeiro.py](api/app/telas/visao_geral/financeiro.py) e os velocímetros contra orçado ou média em [relativo.py](api/app/telas/visao_geral/relativo.py). Testes sem banco em `test_resultado.py` e `test_financeiro.py`; o SQL foi conferido só com o parser do Postgres (`pglast`), sem rodar contra um banco.
- **Períodos anteriores na Visão geral (22/10):** `/api/sessao` lista os meses com faturado no resumo mensal das tabelas `f_vendas_*`, mais o mês do último snapshot, do mais recente para o mais antigo (até 24; `dadosAte` = data do snapshot no mês dele, senão o último dia do mês). `GET /api/telas/visao-geral?mes=AAAA-MM` usa o mês escolhido: Vendas e Custos no mês (fechado: sem projeção nem "A realizar"), Resultado no mês se fechado (no mês do snapshot, o último fechado antes dele), Financeiro com a posição no último dia do mês (no mês do snapshot, na data dele). Formato inválido ou mês fora da lista → 422 ("Mês sem dados."). Sem `mes`, nada muda. Limites:
  - mês fora da janela das tabelas de vendas (hoje 3 meses na PJ; 13 depois do `pj_cortes_vendas_13_meses.sql` e de uma carga): Vendas e Custos saem do resumo mensal, e os cartões do dia a dia ("Faturado até", "Lucro bruto até") viram o total do mês; clientes e produtos por cliente somam as tabelas (cliente que comprou nas duas conta duas vezes);
  - a lista vem das vendas: cliente sem resumo de vendas só tem o mês do snapshot;
  - os testes `db` novos (`tests/test_visao_geral.py`) não rodaram sem Docker; conferido à mão contra o banco da PJ (out, ago, mai/2026 e dez/2025, e os dois 422).
- **Visão geral mais rápida (22/10):** o tempo era quase todo latência (banco em `us-east-1`, ~120 ms por consulta a partir do Brasil), não consulta pesada. Fontes, parâmetros (`cfg.parametro_cliente`) e feriados do cliente agora são lidos uma vez por requisição, e as checagens de "tabela/coluna existe" ficam em memória ([app/core/cache.py](api/app/core/cache.py)): de ~70 para ~30 consultas, de 9,8 s para ~5 s a partir do Brasil. A resposta pronta fica em memória por `VISAO_GERAL_CACHE_SEGUNDOS` (padrão 15 min; chave: cliente, empresas, mês e última carga): repetir a tela ou voltar a um mês já visto leva ~1 s (só as checagens de acesso). O Financeiro lê cada tabela de títulos uma vez só (carteira, série de inadimplência e pagos na mesma consulta, `_ler`), em vez de 5 leituras: mês novo em ~3,5–4 s; saída conferida idêntica em 7 meses. Parâmetro mudado no banco sem carga nova vale depois do prazo. O que falta: hospedar a API na mesma região do banco (item "Onde hospedar"), que deve levar a tela para 1–2 s.
- **Resultado só com tabela de lançamentos (22/10):** desde que as vendas entraram na DRE, um cliente só com vendas ganhava a seção com EBITDA = faturado. Voltou a regra antiga: sem `f_lancamentos_*`, a seção não sai.
## Conferir em produção

- [ ] **Tamanho do banco com a janela de 1 mês.** Depois do `truncate` e da primeira carga com 1 mês, conferir:
  ```sql
  select pg_size_pretty(pg_database_size(current_database())) as banco;
  ```
  No plano gratuito, manter abaixo de ~400 MB para ter folga no pico da troca diária.
- [ ] **Subir a PJ Cortes**, nesta ordem:
  1. `supabase db push` (aplica as migrations 7 a 12 que faltarem);
  2. [pj_cortes_cadastro.sql](supabase/producao/pj_cortes_cadastro.sql) e [pj_cortes_usuario_dev.sql](supabase/producao/pj_cortes_usuario_dev.sql) no SQL Editor. Se o cadastro já tinha rodado antes de 21/10 à noite, rodar também [pj_cortes_titulos_sem_janela.sql](supabase/producao/pj_cortes_titulos_sem_janela.sql) (a seção Financeiro precisa de `f_receitas_pj` e `f_despesas_pj` inteiras);
  3. rodar o workflow `diario.yml` (o bucket foi esvaziado em 21/10: o snapshot precisa rodar antes da carga);
  4. conferir `mart.carga_fonte.mensagem` das 25 fontes. As colunas de data da janela (receitas, recebidas, despesas, pagas, borderô, 645, folha) não puderam ser testadas: se alguma vier em formato desconhecido, a fonte falha com exemplos e é só ajustar ou tirar a janela dela;
  5. tamanho do banco (abaixo de ~400 MB). Atenção: `f_receitas_pj` e `f_despesas_pj` agora entram sem janela;
  6. abrir a Visão geral com a API e conferir Resultado e Financeiro (itens abaixo, em "Para o front consumir").
- [ ] **Vendas da PJ com 13 meses no mart e devoluções na DRE.** Rodar [pj_cortes_vendas_13_meses.sql](supabase/producao/pj_cortes_vendas_13_meses.sql) no SQL Editor (janela de 13 meses em `f_vendas_pj` e `colunas_modelo` da `f_devolucao_pj_agile`), depois o `diario.yml`, e conferir o tamanho do banco (196 MB em 22/10; estimativa +40 MB). Sem o script, a devolução da Agile fica fora da DRE com aviso no log.
- [ ] **Datas da PJ como `date` (migration 13).** Em 21/10 a Visão geral levava 52 s, 46 s no Financeiro, convertendo datas em texto a cada leitura; com a série materializada caiu para 23 s. Ordem:
  1. código novo da carga e da API em `main` (a carga converte texto → `date` quando a coluna do mart é `date`; a API lê `date` direto). A carga antiga gravaria `01/08/2026` como 8 de janeiro numa coluna `date`;
  2. aplicar a [migration 13](supabase/migrations/20261021000013_datas_pj_cortes.sql) (reescreve as 24 colunas, conversão DD/MM/AAAA);
  3. rodar o `diario.yml` e conferir `mart.carga_fonte.mensagem` das 25 fontes;
  4. medir de novo a Visão geral e o tamanho do banco (deve cair: `date` tem 4 bytes, o texto tinha 20);
  5. avisar o front: em `GET /api/dados/<tabela>` essas colunas passam a vir como `date` (`2026-08-01`), não mais `01/08/2026 00:00:00`.
- [ ] **Medidas do .pbix da PJ.** O usuário vai trazer as medidas, tabelas e colunas calculadas do Power BI da PJ: revisar as regras provisórias de Vendas, Custos, Resultado e Financeiro com elas (ex.: devolução abatendo o faturado, `f_devolucao_pj` e `f_devolucao_pj_agile`).
- [ ] **'2 PJ Cortes'** só aparece no estoque (`en_filial` 2) e hoje aponta para a mesma empresa. Confirmar se é outra empresa.

## Antes de publicar a API

- [ ] **Senha do role `api_site`** no Supabase (`alter role api_site with login password '…'`).
- [ ] **App registration no Entra ID** para a API (`ENTRA_TENANT_ID`, `ENTRA_AUDIENCE`). Os usuários dos clientes entram como convidados no tenant, ou o app passa a aceitar vários tenants (isso pede ajuste na validação do token).
- [ ] **Usuários e perfis de produção:** `seg.usuario`, `seg.usuario_cliente`, `seg.usuario_perfil` e `seg.usuario_empresa`, além de `cfg.perfil_tela` e `cfg.perfil_tabela`. Sem isso, toda tela responde 403.
- [ ] **Onde hospedar** (recomendado: Azure Container Apps, com a imagem gerada no GitHub Actions) e como front e API dividem o mesmo domínio (`/api` → API), já que a API não tem CORS.
- [ ] **Usuário de desenvolvimento da PJ Cortes** ([supabase/producao/pj_cortes_usuario_dev.sql](supabase/producao/pj_cortes_usuario_dev.sql), que tira o usuário da Nilo Maia): o `object_id_entra` é provisório, para o token fake da API local. Trocar pelo object id real do Entra quando o login entrar.
- [ ] **VG-01 em `cfg.perfil_tela` de produção** para os perfis que veem a Visão geral (sem isso, a tela responde 403 e some de `/api/sessao`).
- [ ] **Validar com Docker**, o que não foi possível na máquina de desenvolvimento. Os testes `db` da rodada 4 ([api/tests/test_visao_geral.py](api/tests/test_visao_geral.py)) ainda não rodaram contra o banco. Se não houver Docker, criar um workflow de CI no GitHub Actions que rode tudo isso a cada push:
  - `supabase start && supabase db reset && supabase test db`;
  - `uv run pytest` em `api/` e em `jobs/carga/`;
  - `docker build` da API.

## Para o front consumir (Visão geral)

O front (`overnex-frontend`) já desenha a Visão geral a partir de um JSON fixo; o contrato está em `src/contracts/visaoGeral.ts` e o exemplo completo em `src/api/fixtures/visao-geral.json`, no repositório do front. O front não calcula nada: todo número, status, ponteiro, delta e texto precisa vir pronto da API.

- [ ] **No front:** proxy `/api` → `http://127.0.0.1:8000` no `vite.config.ts`, cliente HTTP no lugar do `criarMockClient` (token `fake:<object_id>` em dev), filtros a partir de `/api/sessao` e tipos gerados do `api/openapi.json` (apagar o contrato escrito à mão). Atenção: `dadosAte` e `atualizadoEm` podem vir `null` (cliente sem carga), o que o contrato escrito à mão não previa.
- [ ] **Seção Vendas com regras provisórias** em [api/app/telas/visao_geral/vendas.py](api/app/telas/visao_geral/vendas.py): 4 cartões (meta, faturado até a última venda contra a meta até a data, projeção de fechamento e a realizar por dia útil, em dias úteis — [projecao.py](api/app/telas/visao_geral/projecao.py)) e 4 velocímetros. "Vendas contra a meta" usa a projeção no mês e o realizado no ano; "Clientes que compraram", "Ticket por pedido" e "Produtos por cliente" comparam com a média dos 6 meses anteriores, ou com a meta em `referencia` de `farol.<indicador>` em `cfg.parametro_cliente` ([contra_referencia.py](api/app/telas/visao_geral/contra_referencia.py)). Confirmar com o negócio: faturado = soma de `en_VALTOT` por `en_datemicao`, pedidos = `en_numdoc` distintos, produtos por cliente = pares `en_codcli` × `en_codigo` distintos ÷ clientes, dia útil = segunda a sexta fora de feriado, status de "A realizar" = ritmo atual ÷ ritmo necessário. "Produtos por cliente" só aparece depois de uma carga com o código novo (medida `clientes_produtos`).
- [ ] **Seção Custos com regras provisórias** em [api/app/telas/visao_geral/custos.py](api/app/telas/visao_geral/custos.py): CMV = `en_precus` (custo total da linha) ÷ `en_VALTOT`, aberto por `en_ramo` (ramo do cliente, não linha de produto). Cartões: CMV do mês, margem bruta, lucro bruto e efeito no lucro do mês (na projeção). Velocímetros: os 3 ramos com mais faturado e "demais ramos", contra o CMV dos 6 meses anteriores, com faixas relativas em p.p. (`farol.cmv`: +0,3 atenção, +1,5 crítico). Confirmar com o negócio: a referência (não há orçado por ramo), as faixas e se o faturado bruto serve de base (o mockup usa receita líquida).
- [ ] **DRE da PJ na seção Resultado, como a do Power BI.** Desde 22/10 o realizado soma: lançamentos manuais (com o sinal do `Tipo`: E soma, S subtrai), receita bruta e CMV das vendas (resumo mensal: faturado e `en_custototal`, histórico inteiro), devoluções (`f_devolucao_*`) nas deduções e despesas do contas a pagar (`f_despesas_*`, −`en_valor`, na emissão) pelo de-para [cfg.conta_dre](supabase/migrations/20261022000014_conta_dre.sql). Conferido com o Power BI em 2025 (de-para numa tabela temporária, pelo código real): receita bruta 227,19 × 227,20 mi, receita líquida 224,38 × 224,29, margem de contribuição 12,59 × 11,02, EBITDA 6,46 × 5,18. Para subir: aplicar a migration 14 e rodar [pj_cortes_contas_dre.sql](supabase/producao/pj_cortes_contas_dre.sql) (sem eles, as despesas ficam fora e o EBITDA sai alto). O que falta para bater:
  - a lista exata de "diversos IDs 320/330/340/350/400" e dos "~11 IDs e 7 centros de custo" que a DRE do Power BI exclui (pedir a DAX da `Lancamentos Agrupados`) e marcar em `fora_da_dre`; hoje só estão as exclusões conhecidas (compra de mercadoria, ICMS, IRPJ/CSLL, comissões 201014 e pessoal 350.01);
  - CMV da `Tabela_CMV` (mantido o custo das vendas por decisão do usuário: 2025 CMV 204,9 × 202,6 mi), folha gerencial (não dá para subir ainda), inadimplência (fora por enquanto);
  - `en_datcomp` no arquivo de despesas: o Power BI usa a competência a partir de 2026; aqui é a emissão;
  - confirmar os lançamentos manuais grandes de 2026 sem descrição (160.01.04 E: −4,2 mi em fev, −13,7 mi em jun, −31,0 mi em jul; 180.01.02 S: −1,2 mi em fev, −3,9 mi em jun, −8,8 mi em jul): pela regra do Power BI derrubam o lucro líquido desses meses.
  Orçamento da PJ corrompido no dataflow (linhas triplicadas, parte sem a vírgula decimal): corrigir lá antes de usar o cenário "Meta" como orçado. Depois, cadastrar em `cfg.parametro_cliente` a chave `orcamento.cenario` com `{"cenario": "Meta"}` (sem ela, com vários cenários e nenhum "Base", não há orçado, e o log avisa uma vez por processo).
- [ ] **Seção Resultado com regras provisórias** em [api/app/telas/visao_geral/resultado.py](api/app/telas/visao_geral/resultado.py), a partir de `f_lancamentos_*` (`Data_de_Competencia`, `Valor_Regime_de_Competencia`, `Id_Descritivo`) e `f_orcamento_*` (menos o comercial e o de ticket). Suposição a conferir com os dados reais: o `Id_Descritivo` segue o plano gerencial do mockup (3 primeiros dígitos = grupo da DRE: 100 receita bruta, 110 deduções, 120 custos, 130 variáveis, 140 fixas, 150 D&A, 160/170 financeiro, 180/190 não operacional, 200 IR; 140.01 = folha). Se for outro formato, a seção não aparece. No orçado, cada grupo entra pelo valor absoluto da soma do mês; no realizado, com sinal (item acima). Mês: o último fechado antes da carga. Referência: orçado do cenário "Base" (ou do único cenário), senão a média de 6 meses. Velocímetros: margem EBITDA, despesas fixas e folha sobre a receita líquida, com faixas relativas em p.p. (`farol.margem_ebitda`, `farol.despesas_fixas`, `farol.folha_receita`, padrão do mockup). Cartões: EBITDA, lucro líquido (antes das participações, grupo 210), ponto de equilíbrio e margem de segurança. Fora: "EBITDA projetado" do mês corrente e "Dívida líquida / EBITDA" (sem fonte de dívida).
- [ ] **Seção Financeiro com regras provisórias** em [api/app/telas/visao_geral/financeiro.py](api/app/telas/visao_geral/financeiro.py), a partir de `f_receitas_*` e `f_despesas_*` (emissão, vencimento, pagamento, `en_valor`). Título em aberto = emitido e sem pagamento na data; modelo D+2 igual ao Power BI da PJ (conciliado em 21/10: a receber futuro, atrasado geral e atrasado do ano batem exatos; a pagar atrasado geral com R$ 5 de diferença): a vencer = vencimento a partir de hoje, vencido = vencimento + 2 dias úteis até hoje, e o meio fica "em compensação" (fora dos dois cartões, dentro da carteira); faixas de atraso em dias corridos desde o vencimento. Falta a DAX para confirmar dias úteis × corridos no D+2 e o filtro dos cartões "(Fornecedores)"; pagamento antes de 1901 conta como sem pagamento, e anterior à emissão (adiantamento) conta como pago, com 0 dias no prazo médio; baixa parcial (`en_valorpago`) fica de fora. Cartões: a receber a vencer e vencido, a pagar em 30 dias (em % da média paga dos 6 meses fechados, `farol.pagar_lancado`) e a pagar vencido. Cartão de inadimplência com faixas de atraso e a série no fim de cada um dos 12 meses fechados (`farol.inadimplencia`, em p.p. da média). Velocímetros: prazo médio de recebimento e de pagamento (emissão → pagamento, ponderado pelo valor) contra a média de 6 meses. Confirmar: se `f_receitas_pj` e `f_despesas_pj` são títulos (e não outra coisa), o tratamento de baixa parcial e as faixas. Fora, por falta de fonte: saldo em conta, cobertura de caixa e ciclo de caixa (precisa de prazo de estoque a custo).
- [ ] **Aplicar a migration 9 em produção** (`cfg.feriado`) e cadastrar os feriados estaduais e municipais da Nilo Maia (e o Carnaval, se a empresa não trabalha). Sem ela, a API usa só os feriados nacionais.
- [ ] **Aplicar as migrations 9 e 10 em produção e rodar a carga com o código novo.** A 10 (`dimensao` em `mart.resumo_mensal`) e a carga nova dão o histórico de custo e de custo por ramo (seção Custos) e o `clientes_produtos` (velocímetro "Produtos por cliente"). Sem a 10, a carga grava o resumo sem as medidas por ramo e avisa.
- [ ] **Regra de cada indicador** (depende de decisão de negócio): tabela de origem, conta, meta ou orçado de comparação e faixas do farol. Indicadores do mockup e tabelas candidatas:
  - Vendas: vendas contra a meta, clientes que compraram, ticket por pedido, linhas por cliente → `f_vendas`, `f_metas`, `f_pedidos`.
  - Custos: CMV por linha de produto → `f_vendas` (custo) ou `f_lancamentos_agrupados`, com `f_orcamento`.
  - Resultado: feito com regra provisória (item acima); falta a fonte de dívida líquida para "Dívida líquida / EBITDA" (`origem: "cadastro"`).
  - Financeiro: feito com regra provisória (item acima); falta fonte de saldo bancário para "Saldo em conta" e "Cobertura de caixa", e custo do estoque para o "Ciclo de caixa".
- [ ] **Faixas do farol de cada cliente** em `cfg.parametro_cliente` (chave `farol.vendas_meta`, valor `{"atencao": 100, "critico": 90, "min": 80, "max": 110, "meta": 100}`), lidas por [api/app/farol/parametro.py](api/app/farol/parametro.py). Sem parâmetro, vale o padrão provisório do código (os limites do mockup). Definir os da Nilo Maia.
- [ ] **Textos da tela:** o resumo de cada seção (ex.: "A meta escorrega por positivação…"), a referência de cada indicador e o texto do cartão de inadimplência (`Trecho[]`). Decidir se saem de regras fixas na API ou se ficam fora da primeira versão.
- [ ] **Seções por cliente.** Quais seções cada cliente vê e em que ordem, e o campo `modulo` (ex.: Compras e Estoque pertencem ao módulo Distribuidora). Candidato: `cfg.painel`.
- [ ] **Logo do cliente servido pela própria API.** Hoje `logoUrl` só sai quando `cfg.cliente.logo_url` é um caminho do próprio domínio (`/…`); fora disso vem `null`.
- [ ] **Sigla do cliente:** hoje sai das iniciais do nome ("Padrão LANN Alfa" → "PA"). Se precisar de outra, criar a coluna `cfg.cliente.sigla`.
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

- [ ] **Configurar o GitHub para colaboradores**, como em [CONTRIBUTING.md](CONTRIBUTING.md): branch `dev`, GitHub App (`APP_ID`, `APP_PRIVATE_KEY`), rulesets em `main` e `dev` com os checks `api` e `carga` do [ci.yml](.github/workflows/ci.yml) e colaboradores com papel Write. O CI ainda não roda os testes com banco (ver "Validar com Docker").

- [ ] **Mover o agendamento para um Container Apps Job no Azure.** Só o agendamento muda; o CLI (`uv run python -m carga`) fica igual.

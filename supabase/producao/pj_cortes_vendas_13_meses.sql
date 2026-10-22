-- PJ Cortes — vendas com 13 meses no mart e as colunas da devolução da Agile (produção).
--
-- NÃO é migration. Só é preciso se o pj_cortes_cadastro.sql rodou antes de 22/10/2026, quando
-- f_vendas_pj ainda tinha janela de 3 meses e f_devolucao_pj_agile não tinha colunas_modelo.
-- Pode rodar mais de uma vez.
--
-- - f_vendas_pj: janela de 13 meses (o mês da carga e os 12 anteriores), em vez de 3. O
--   histórico mensal da Visão geral (resumo mensal) já vinha do arquivo inteiro; a janela
--   decide só quantas linhas de venda ficam no mart. Estimativa: +85 mil linhas, ~40 MB;
-- - f_devolucao_pj_agile: data e valor com os nomes do sistema antigo, para as devoluções
--   entrarem nas deduções da DRE (seção Resultado).
--
-- Depois, rode a carga (workflow diario.yml) e confira o tamanho do banco (abaixo de ~400 MB
-- no plano gratuito).

begin;

update cfg.fonte_carga f
   set meses_retidos = 13
  from cfg.cliente c
 where c.id = f.cliente_id
   and c.slug = 'pj-cortes'
   and f.tabela_destino = 'f_vendas_pj';

update cfg.fonte_carga f
   set colunas_modelo = '{"data": "en_datemi", "valor": "en_VALTOT"}'::jsonb
  from cfg.cliente c
 where c.id = f.cliente_id
   and c.slug = 'pj-cortes'
   and f.tabela_destino = 'f_devolucao_pj_agile';

commit;

-- Para ver:
--   select f.tabela_destino, f.coluna_data, f.meses_retidos, f.colunas_modelo
--     from cfg.fonte_carga f join cfg.cliente c on c.id = f.cliente_id
--    where c.slug = 'pj-cortes' and f.tabela_destino in ('f_vendas_pj', 'f_devolucao_pj_agile');
--
--   select pg_size_pretty(pg_database_size(current_database())) as banco;

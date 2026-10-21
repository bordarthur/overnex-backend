-- PJ Cortes — tira a janela de datas dos títulos a receber e a pagar (produção).
--
-- NÃO é migration. Só é preciso se o pj_cortes_cadastro.sql rodou ANTES de 21/10/2026 à
-- noite, quando ele ainda punha janela de 3 meses em f_receitas_pj e f_despesas_pj. A seção
-- Financeiro da Visão geral precisa do arquivo inteiro: títulos antigos ainda em aberto e
-- 12 meses de histórico. Pode rodar mais de uma vez.
--
-- Depois, rode a carga (workflow diario.yml) para as duas tabelas voltarem inteiras, e
-- confira o tamanho do banco (abaixo de ~400 MB no plano gratuito).

begin;

update cfg.fonte_carga f
   set coluna_data = null,
       meses_retidos = null
  from cfg.cliente c
 where c.id = f.cliente_id
   and c.slug = 'pj-cortes'
   and f.tabela_destino in ('f_receitas_pj', 'f_despesas_pj');

commit;

-- Para ver:
--   select f.tabela_destino, f.coluna_data, f.meses_retidos
--     from cfg.fonte_carga f join cfg.cliente c on c.id = f.cliente_id
--    where c.slug = 'pj-cortes' and f.tabela_destino in ('f_receitas_pj', 'f_despesas_pj');

-- PJ Cortes — setor, segmento e grupo da unidade (produção).
--
-- NÃO é migration: rode no SQL Editor do Supabase DEPOIS da migration 35 (`supabase db
-- push`). Pode rodar mais de uma vez.
--
-- A PJ tem uma unidade só ("PJ Cortes"): um negócio só, então o front mostra o chip "Unidade"
-- e não a bandeja "Negócios". Distribuidora libera o painel Estoque (EST-01) quando ele for
-- ligado na Gestão.
--
-- A CONFIRMAR com o usuário (sugestão do pedido do front, 27/11/2026): setor Distribuidora,
-- segmento "Frigorífico" e grupo "PJ Cortes". O cliente não tem segmento próprio (o "Ramo"
-- saiu na migration 37): o segmento é só da unidade.
--
-- '2 PJ Cortes' (en_filial 2, só no estoque) continua apontando para esta unidade (mapa do
-- pj_cortes_cadastro.sql). Se for outra unidade, descomente o passo 2: ele cria a unidade e
-- troca o mapa dessa linha (os dados dela só mudam de unidade na próxima carga).

begin;

-- 1. a unidade
update cfg.empresa e
   set setor = 'distribuidora', segmento = 'Frigorífico', grupo = 'PJ Cortes'
  from cfg.cliente c
 where c.id = e.cliente_id
   and c.slug = 'pj-cortes'
   and e.nome = 'PJ Cortes';

-- 2. só se '2 PJ Cortes' for outra unidade (nome a confirmar)
-- insert into cfg.empresa (cliente_id, nome, setor, segmento, grupo)
-- select c.id, 'PJ Cortes 2', 'distribuidora', 'Frigorífico', 'PJ Cortes'
--   from cfg.cliente c
--  where c.slug = 'pj-cortes'
-- on conflict (cliente_id, nome) do nothing;
-- update cfg.mapa_empresa m
--    set empresa_id = e.id
--   from cfg.empresa e
--   join cfg.cliente c on c.id = e.cliente_id and c.slug = 'pj-cortes'
--  where m.cliente_id = c.id
--    and m.empresa_origem = '2 PJ Cortes'
--    and e.nome = 'PJ Cortes 2';

-- conferência
select e.nome, e.setor, e.segmento, e.grupo
  from cfg.empresa e
  join cfg.cliente c on c.id = e.cliente_id and c.slug = 'pj-cortes'
 order by e.nome;

commit;

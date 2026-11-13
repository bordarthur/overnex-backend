-- Migration 24 — tira a 645 e o borderô da PJ Cortes do Supabase.
--
-- Por quê: as duas só serviam ao fluxo de caixa, que agora vem pronto da view f_Fluxo_PJ
-- (a view junta a 645 e o borderô dentro do Azure, com o histórico inteiro de lá, e chega
-- em mart.f_fluxo_pj, migration 23). Desde 06/11/2026 dbo.f_645_PJ e dbo.f_Bordero_PJ nem
-- estão na variável TABELAS do snapshot: a carga das duas lia um arquivo velho. Nenhuma tela
-- da API lê essas tabelas.
--
-- Remove, nesta ordem: o histórico de carga das duas fontes (mart.carga_fonte), as
-- permissões de perfil (cfg.perfil_tabela), as fontes (cfg.fonte_carga) e as tabelas
-- mart.f_645_pj e mart.f_bordero_pj (criadas na migration 12). Não tem volta: para tê-las
-- de novo, uma migration recria as tabelas, a fonte volta ao cadastro e a carga roda.
--
-- Pode ir para produção a qualquer hora: a API não depende dela.

delete from mart.carga_fonte cf
 using cfg.fonte_carga f
 where cf.fonte_id = f.id
   and f.tabela_destino in ('f_645_pj', 'f_bordero_pj');

delete from cfg.perfil_tabela
 where tabela in ('f_645_pj', 'f_bordero_pj');

delete from cfg.fonte_carga
 where tabela_destino in ('f_645_pj', 'f_bordero_pj');

drop table mart.f_645_pj;
drop table mart.f_bordero_pj;

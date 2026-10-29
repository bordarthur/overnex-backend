-- PJ Cortes — fonte dos lançamentos agrupados da DRE (produção).
--
-- NÃO é migration: rode no SQL Editor do Supabase. Pode rodar mais de uma vez.
--
-- ORDEM:
-- 1. migration 18 (mart.f_lancamentos_agrupados_pj);
-- 2. a view dbo.f_Lancamentos_Agrupados_PJ criada no Azure SQL
--    (jobs/snapshot/azure/f_lancamentos_agrupados_pj.sql) e acrescentada à variável
--    TABELAS do GitHub;
-- 3. este script;
-- 4. o workflow diario.yml (snapshot e carga). Depois, conferir
--    mart.carga_fonte.mensagem da fonte nova.
--
-- Sem janela de datas: a DRE usa até 24 meses (e o acumulado do ano anterior). Sem coluna
-- de empresa, como os lançamentos: a PJ tem uma empresa só, e a view escreve o nome dela
-- de mais de um jeito. Sem dado nominal (só conta, data e valor).
--
-- Com a fonte cadastrada e a primeira carga feita, a DRE da Visão geral e do painel
-- Resultado passa a vir desta tabela (o orçado continua de f_orcamento_pj).

begin;

insert into cfg.fonte_carga
    (cliente_id, tipo, caminho_bucket, tabela_destino, coluna_empresa, contem_dado_nominal,
     colunas_modelo, ativo)
select c.id,
       'tabela',
       'dataflow/dbo_f_lancamentos_agrupados_pj/dbo_f_lancamentos_agrupados_pj.parquet',
       'f_lancamentos_agrupados_pj',
       null,
       false,
       '{}'::jsonb,
       true
  from cfg.cliente c
 where c.slug = 'pj-cortes'
on conflict do nothing;

-- conferência
do $$
begin
    if not exists (
        select 1
          from cfg.fonte_carga f
          join cfg.cliente c on c.id = f.cliente_id and c.slug = 'pj-cortes'
         where f.tabela_destino = 'f_lancamentos_agrupados_pj' and f.ativo
    ) then
        raise exception 'esperava a fonte f_lancamentos_agrupados_pj ativa na PJ Cortes';
    end if;
end
$$;

commit;

-- Depois da carga:
--   select date_trunc('month', "Data")::date as mes, left("Id_Descritivo", 3) as grupo,
--          sum("Valor") as valor
--     from mart.f_lancamentos_agrupados_pj
--    group by 1, 2 order by 1 desc, 2;

-- PJ Cortes — fluxo de caixa: saldo inicial e fonte da view f_Fluxo_PJ (produção).
--
-- NÃO é migration: rode no SQL Editor do Supabase. Pode rodar mais de uma vez.
--
-- ORDEM:
-- 1. migrations 22 (cfg.saldo_inicial) e 23 (mart.f_fluxo_pj), com `supabase db push`;
-- 2. a view dbo.f_Fluxo_PJ no Azure SQL, acrescentada à variável TABELAS do GitHub;
-- 3. este script;
-- 4. o workflow diario.yml (snapshot e carga). Depois, conferir mart.carga_fonte.mensagem
--    da fonte nova e as consultas do fim deste script.
--
-- A view junta a 645 e o borderô dentro do Azure, com o histórico inteiro de lá: as duas
-- não vêm mais para o Supabase (migration 24).
--
-- Parte 1 — saldo inicial. O mesmo do Power BI (tabela Saldo_inicial): −2.103.419,90 no
-- início de 01/04/2025, primeiro dia do fluxo. Para TROCAR o valor, mude a linha abaixo e
-- rode de novo (a linha da mesma data é substituída). Para reancorar o saldo numa data
-- conferida com o banco, acrescente outra linha com a data nova: ela vale dali para
-- frente, e a de 01/04/2025 continua valendo antes dela.
--
-- Parte 2 — fonte f_fluxo_pj. Sem janela de datas: o saldo de cada mês soma todo o caixa
-- desde o saldo inicial. Sem coluna de empresa, como os lançamentos agrupados: a PJ tem
-- uma empresa só. Com dado nominal: Descricao e Descricao2 são o histórico da 645.

begin;

-- parte 1
insert into cfg.saldo_inicial (cliente_id, data, saldo, origem)
select c.id, v.data, v.saldo, v.origem
  from cfg.cliente c,
       (values
        (date '2025-04-01', -2103419.90, 'Saldo_inicial do Power BI da PJ')
       ) v (data, saldo, origem)
 where c.slug = 'pj-cortes'
on conflict (cliente_id, data) do update
   set saldo = excluded.saldo,
       origem = excluded.origem;

-- parte 2
insert into cfg.fonte_carga
    (cliente_id, tipo, caminho_bucket, tabela_destino, coluna_empresa, contem_dado_nominal,
     colunas_modelo, ativo)
select c.id,
       'tabela',
       'dataflow/dbo_f_fluxo_pj/dbo_f_fluxo_pj.parquet',
       'f_fluxo_pj',
       null,
       true,
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
          from cfg.saldo_inicial s
          join cfg.cliente c on c.id = s.cliente_id and c.slug = 'pj-cortes'
         where s.data = date '2025-04-01'
    ) then
        raise exception 'esperava o saldo inicial de 01/04/2025 da PJ Cortes';
    end if;
    if not exists (
        select 1
          from cfg.fonte_carga f
          join cfg.cliente c on c.id = f.cliente_id and c.slug = 'pj-cortes'
         where f.tabela_destino = 'f_fluxo_pj' and f.ativo
           and f.coluna_data is null and f.meses_retidos is null
    ) then
        raise exception 'esperava a fonte f_fluxo_pj ativa e sem janela na PJ Cortes';
    end if;
end
$$;

commit;

-- Para ver o saldo:
--   select s.data, s.saldo, s.origem
--     from cfg.saldo_inicial s join cfg.cliente c on c.id = s.cliente_id
--    where c.slug = 'pj-cortes' order by s.data;
--
-- Depois da carga, caixa e saldo final por mês (compare com a DFC do Power BI; o saldo
-- aqui inclui o caixa sem de-para, que a DFC do Power BI deixa de fora: achado 3):
--   with mes as (
--       select date_trunc('month', "Data")::date as mes,
--              sum("Valor") as caixa,
--              sum("Valor") filter (where coalesce("Id_Descritivo", '') = '') as sem_de_para
--         from mart.f_fluxo_pj
--        group by 1
--   )
--   select mes, caixa, sem_de_para,
--          -2103419.90 + sum(caixa) over (order by mes) as saldo_final
--     from mes order by mes;
--
-- Por bloco da view:
--   select "Origem", count(*) as linhas, sum("Valor") as valor,
--          count(*) filter (where coalesce("Id_Descritivo", '') = '') as sem_de_para
--     from mart.f_fluxo_pj group by 1 order by 1;

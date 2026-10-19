-- mart.resumo_mensal.dimensao: um total por dimensão; vazio é o total sem abertura.
begin;

create extension if not exists pgtap with schema extensions;

select plan(3);

select col_default_is('mart', 'resumo_mensal', 'dimensao', '''''::text', 'dimensão vazia por padrão');

insert into mart.resumo_mensal (cliente_id, empresa_id, tabela, medida, dimensao, mes, valor, data_snapshot)
values
    ('c1000000-0000-4000-8000-00000000000a', null, 'f_vendas_x', 'custo_ramo', 'MERCEARIA', '2026-09-01', 10, '2026-10-12'),
    ('c1000000-0000-4000-8000-00000000000a', null, 'f_vendas_x', 'custo_ramo', 'CONSTRUTORA', '2026-09-01', 20, '2026-10-12');

select is((select count(*)::int from mart.resumo_mensal where medida = 'custo_ramo'), 2,
          'a mesma medida e mês com dimensões diferentes');
select throws_ok(
    $$insert into mart.resumo_mensal (cliente_id, empresa_id, tabela, medida, dimensao, mes, valor, data_snapshot)
      values ('c1000000-0000-4000-8000-00000000000a', null, 'f_vendas_x', 'custo_ramo', 'MERCEARIA',
              '2026-09-01', 1, '2026-10-12')$$,
    '23505', null, 'um total por dimensão');

select * from finish();
rollback;

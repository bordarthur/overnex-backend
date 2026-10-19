-- mart.resumo_mensal e cfg.parametro_cliente: isolamento por cliente e por empresa e
-- permissões dos roles.
begin;

create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to api_site, carga;

select plan(9);

-- dados de teste: A com uma linha sem empresa e uma da A2; B com uma linha
insert into mart.resumo_mensal (cliente_id, empresa_id, tabela, medida, mes, valor, data_snapshot)
values
    ('c1000000-0000-4000-8000-00000000000a', null, 'f_vendas_x', 'faturado', '2026-09-01', 100, '2026-10-12'),
    ('c1000000-0000-4000-8000-00000000000a', 'e1000000-0000-4000-8000-0000000000a2',
     'f_vendas_x', 'faturado', '2026-09-01', 50, '2026-10-12'),
    ('c1000000-0000-4000-8000-00000000000b', null, 'f_vendas_x', 'faturado', '2026-09-01', 70, '2026-10-11');

insert into cfg.parametro_cliente (cliente_id, chave, valor, vigente_de) values
    ('c1000000-0000-4000-8000-00000000000a', 'farol.vendas_meta', '{"atencao": 100}', '2026-01-01'),
    ('c1000000-0000-4000-8000-00000000000b', 'farol.vendas_meta', '{"atencao": 95}', '2026-01-01');

select throws_ok(
    $$insert into mart.resumo_mensal (cliente_id, empresa_id, tabela, medida, mes, valor, data_snapshot)
      values ('c1000000-0000-4000-8000-00000000000a', null, 'f_vendas_x', 'faturado',
              '2026-09-01', 1, '2026-10-12')$$,
    '23505', null, 'um total por cliente, empresa (inclusive nula), tabela, medida e mês');

-- Ana (cliente A, empresas A1 e A2)
set local role api_site;
set local app.usuario_id = 'd1000000-0000-4000-8000-00000000000a';

select is((select count(*)::int from mart.resumo_mensal), 2, 'A lê as linhas do próprio cliente');
select is((select count(*)::int from cfg.parametro_cliente), 1, 'A lê só o parâmetro do próprio cliente');
select throws_ok($$delete from mart.resumo_mensal$$, '42501', null, 'api_site não apaga o resumo');

reset role;

-- Ana sem a A2: perde a linha da A2, mantém a do cliente inteiro
delete from seg.usuario_empresa
 where usuario_id = 'd1000000-0000-4000-8000-00000000000a'
   and empresa_id = 'e1000000-0000-4000-8000-0000000000a2';

set local role api_site;
set local app.usuario_id = 'd1000000-0000-4000-8000-00000000000a';
select is((select count(*)::int from mart.resumo_mensal), 1, 'sem a A2, sobra só a linha sem empresa');
reset role;

-- sem usuário na sessão: nada
set local role api_site;
select is((select count(*)::int from mart.resumo_mensal), 0, 'sem app.usuario_id, nenhum resumo');
select is((select count(*)::int from cfg.parametro_cliente), 0, 'sem app.usuario_id, nenhum parâmetro');
reset role;

-- carga escreve no resumo e não lê parâmetros
set local role carga;
select lives_ok($$delete from mart.resumo_mensal where tabela = 'f_vendas_x'$$, 'carga apaga o resumo');
select throws_ok($$select * from cfg.parametro_cliente$$, '42501', null, 'carga não lê cfg.parametro_cliente');
reset role;

select * from finish();
rollback;

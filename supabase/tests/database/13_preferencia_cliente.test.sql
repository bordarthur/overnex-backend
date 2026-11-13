-- cfg.parametro_cliente: o api_site grava só as escolhas ('preferencia.*') do próprio
-- cliente; farol e os outros parâmetros continuam fora do alcance dele.
begin;

create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to api_site;

select plan(9);

insert into cfg.parametro_cliente (cliente_id, chave, valor, vigente_de) values
    ('c1000000-0000-4000-8000-00000000000a', 'farol.cmv', '{"atencao": 1}', '2026-01-01'),
    ('c1000000-0000-4000-8000-00000000000b', 'preferencia.linhas-custo', '"3"', '2026-01-01');

-- Ana (cliente A)
set local role api_site;
set local app.usuario_id = 'd1000000-0000-4000-8000-00000000000a';

select lives_ok(
    $$insert into cfg.parametro_cliente (cliente_id, chave, valor, vigente_de)
      values ('c1000000-0000-4000-8000-00000000000a', 'preferencia.linhas-custo', '"2"',
              '2026-10-28')
      on conflict (cliente_id, chave, vigente_de) do update set valor = excluded.valor$$,
    'A grava a escolha do próprio cliente');
select lives_ok(
    $$insert into cfg.parametro_cliente (cliente_id, chave, valor, vigente_de)
      values ('c1000000-0000-4000-8000-00000000000a', 'preferencia.linhas-custo', '"auto"',
              '2026-10-28')
      on conflict (cliente_id, chave, vigente_de) do update set valor = excluded.valor$$,
    'A troca a escolha do mesmo dia');
select is(
    (select valor #>> '{}' from cfg.parametro_cliente
      where chave = 'preferencia.linhas-custo' and vigente_de = '2026-10-28'),
    'auto', 'vale a última escolha do dia');
select throws_ok(
    $$insert into cfg.parametro_cliente (cliente_id, chave, valor, vigente_de)
      values ('c1000000-0000-4000-8000-00000000000b', 'preferencia.linhas-custo', '"1"',
              '2026-10-28')$$,
    '42501', null, 'A não grava escolha de outro cliente');
select throws_ok(
    $$insert into cfg.parametro_cliente (cliente_id, chave, valor, vigente_de)
      values ('c1000000-0000-4000-8000-00000000000a', 'farol.vendas_meta', '{"atencao": 1}',
              '2026-10-28')$$,
    '42501', null, 'A não grava parâmetro que não é escolha');

update cfg.parametro_cliente set valor = '{"atencao": 9}' where chave = 'farol.cmv';
update cfg.parametro_cliente set valor = '"7"' where chave = 'preferencia.linhas-custo'
   and cliente_id = 'c1000000-0000-4000-8000-00000000000b';
select throws_ok(
    $$update cfg.parametro_cliente set chave = 'farol.x' where chave = 'preferencia.linhas-custo'$$,
    '42501', null, 'api_site só troca o valor');
select throws_ok($$delete from cfg.parametro_cliente$$, '42501', null, 'api_site não apaga parâmetro');

reset role;

select is((select valor #>> '{atencao}' from cfg.parametro_cliente where chave = 'farol.cmv'),
          '1', 'o update da A não mexe no farol');
select is((select valor #>> '{}' from cfg.parametro_cliente
            where cliente_id = 'c1000000-0000-4000-8000-00000000000b'),
          '3', 'o update da A não mexe na escolha de outro cliente');

select * from finish();
rollback;

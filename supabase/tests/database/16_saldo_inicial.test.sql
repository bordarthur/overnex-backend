-- cfg.saldo_inicial: cada cliente lê só o próprio saldo; o api_site não cadastra nem troca.
begin;

create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to api_site;

select plan(6);

insert into cfg.saldo_inicial (cliente_id, data, saldo, origem) values
    ('c1000000-0000-4000-8000-00000000000a', '2025-04-01', -2103419.90, 'teste'),
    ('c1000000-0000-4000-8000-00000000000a', '2026-01-01', 150000.00, 'conferido com o banco'),
    ('c1000000-0000-4000-8000-00000000000b', '2025-01-01', 10.00, null);

select throws_ok($$insert into cfg.saldo_inicial (cliente_id, data, saldo)
                   values ('c1000000-0000-4000-8000-00000000000a', '2025-04-01', 1)$$,
                 '23505', null, 'um saldo por cliente e data');

set local role api_site;
set local app.usuario_id = 'd1000000-0000-4000-8000-00000000000a';
select is((select count(*)::int from cfg.saldo_inicial), 2, 'A lê só os próprios saldos');
select is((select saldo from cfg.saldo_inicial where data = '2025-04-01'), -2103419.90::numeric,
          'valor guardado sem arredondar');
select throws_ok($$insert into cfg.saldo_inicial (cliente_id, data, saldo)
                   values ('c1000000-0000-4000-8000-00000000000a', '2026-06-01', 1)$$,
                 '42501', null, 'api_site não cadastra saldo');
select throws_ok($$update cfg.saldo_inicial set saldo = 0$$,
                 '42501', null, 'api_site não troca saldo');
reset role;

set local role api_site;
select is((select count(*)::int from cfg.saldo_inicial), 0, 'sem app.usuario_id, nenhum saldo');
reset role;

select * from finish();
rollback;

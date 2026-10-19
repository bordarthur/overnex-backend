-- cfg.feriado: cada cliente lê só os próprios feriados.
begin;

create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to api_site;

select plan(3);

insert into cfg.feriado (cliente_id, data, nome) values
    ('c1000000-0000-4000-8000-00000000000a', '2026-03-19', 'São José (CE)'),
    ('c1000000-0000-4000-8000-00000000000b', '2026-02-16', 'Carnaval');

set local role api_site;
set local app.usuario_id = 'd1000000-0000-4000-8000-00000000000a';
select is((select count(*)::int from cfg.feriado), 1, 'A lê só o próprio feriado');
select throws_ok($$insert into cfg.feriado (cliente_id, data, nome)
                   values ('c1000000-0000-4000-8000-00000000000a', '2026-06-24', 'x')$$,
                 '42501', null, 'api_site não cadastra feriado');
reset role;

set local role api_site;
select is((select count(*)::int from cfg.feriado), 0, 'sem app.usuario_id, nenhum feriado');
reset role;

select * from finish();
rollback;

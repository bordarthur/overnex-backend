-- cfg.conta_dre: cada cliente lê só o próprio de-para de contas.
begin;

create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to api_site;

select plan(4);

insert into cfg.conta_dre (cliente_id, conta, id_descritivo, descricao, fora_da_dre) values
    ('c1000000-0000-4000-8000-00000000000a', '100002', '100.02.03', 'Fretes Retorno', null),
    ('c1000000-0000-4000-8000-00000000000b', '100001', '120.01.01', 'CMV', 'entra pelo CMV');

select throws_ok($$insert into cfg.conta_dre (cliente_id, conta)
                   values ('c1000000-0000-4000-8000-00000000000a', '999')$$,
                 '23514', null, 'conta sem id_descritivo precisa do motivo em fora_da_dre');

set local role api_site;
set local app.usuario_id = 'd1000000-0000-4000-8000-00000000000a';
select is((select count(*)::int from cfg.conta_dre), 1, 'A lê só o próprio de-para');
select throws_ok($$insert into cfg.conta_dre (cliente_id, conta, id_descritivo)
                   values ('c1000000-0000-4000-8000-00000000000a', '1', '140.01.01')$$,
                 '42501', null, 'api_site não cadastra conta');
reset role;

set local role api_site;
select is((select count(*)::int from cfg.conta_dre), 0, 'sem app.usuario_id, nenhuma conta');
reset role;

select * from finish();
rollback;

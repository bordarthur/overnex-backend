-- cfg.plano_conta: plano global, que qualquer usuário da API lê e ninguém da API altera.
begin;

create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to api_site;

select plan(6);

delete from cfg.plano_conta;
insert into cfg.plano_conta
    (id_descritivo, descritivo, id_nivel_1, nivel_1, id_nivel_2, nivel_2, fator, caixa,
     competencia)
values ('140.01.01', 'Salarios ( D )', '140.00', '(-) DESPESAS FIXAS', '140.01',
        'Despesas com Pessoal', -1, 'FCO', 'CO/CA'),
       ('400.01.01', 'Emprestimo Concedido', '400.00', '(-) EMPRÉSTIMOS', '400.01',
        'Emprestimo Concedido', -1, 'FCF', 'CA');

select throws_ok($$insert into cfg.plano_conta
                     (id_descritivo, descritivo, id_nivel_1, nivel_1, id_nivel_2, nivel_2, fator)
                   values ('110.03;14', 'x', '110.00', 'x', '110.03', 'x', 1)$$,
                 '23514', null, 'id_descritivo só com números e pontos');
select throws_ok($$insert into cfg.plano_conta
                     (id_descritivo, descritivo, id_nivel_1, nivel_1, id_nivel_2, nivel_2, fator)
                   values ('110.03.14', 'x', '110.00', 'x', '110.03', 'x', 2)$$,
                 '23514', null, 'fator só +1 ou -1');
select throws_ok($$insert into cfg.plano_conta
                     (id_descritivo, descritivo, id_nivel_1, nivel_1, id_nivel_2, nivel_2, fator,
                      competencia)
                   values ('110.03.14', 'x', '110.00', 'x', '110.03', 'x', 1, 'XX')$$,
                 '23514', null, 'competência só CO, CA ou CO/CA');

set local role api_site;
set local app.usuario_id = 'd1000000-0000-4000-8000-00000000000a';
select is((select count(*)::int from cfg.plano_conta), 2, 'usuário do cliente A lê o plano');
select throws_ok($$delete from cfg.plano_conta$$, '42501', null, 'api_site não altera o plano');
reset role;

set local role api_site;
select is((select count(*)::int from cfg.plano_conta), 2, 'o plano é global: lê sem usuário');
reset role;

select * from finish();
rollback;

-- Migration 32: apagar pessoa pela Gestão. Só o admin (Dora) apaga, só quem está desativado
-- e nunca a si mesmo; vínculos e pedidos de convite vão junto; o login vai para a fila do
-- job (role carga), que não apaga login que voltou a ter cadastro; a API não lê essa fila; e
-- quem tem registro que depende dele (regra de lançamento) não se apaga.
begin;

create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to api_site, carga;

select plan(18);

-- Eva: pessoa do cliente A, com login; pediu um convite (como se fosse admin) e tem um
-- pedido para ela
insert into seg.usuario (id, id_autenticacao, email, nome, tipo, perfil)
values ('d1000000-0000-4000-8000-0000000000e0', '0d000000-0000-4000-8000-0000000000e0',
        'eva@alfa.example', 'Eva Alfa', 'cliente', 'cliente_usuario');
select seg.definir_vinculos(
    'd1000000-0000-4000-8000-0000000000e0',
    array['c1000000-0000-4000-8000-00000000000a']::uuid[]);
insert into seg.convite_fila (usuario_id, pedido_por)
values ('d1000000-0000-4000-8000-0000000000c0', 'd1000000-0000-4000-8000-0000000000e0'),
       ('d1000000-0000-4000-8000-0000000000e0', 'd1000000-0000-4000-8000-0000000000ad');

-- ---------------------------------------------------------------------------
-- Dora (admin)
-- ---------------------------------------------------------------------------
set local role api_site;
set local app.usuario_id = 'd1000000-0000-4000-8000-0000000000ad';

select throws_ok(
    $$select seg.apagar_usuario('d1000000-0000-4000-8000-0000000000e0')$$,
    'P0001', null, 'pessoa ativa não se apaga');
select throws_ok(
    $$select seg.apagar_usuario('d1000000-0000-4000-8000-0000000000ad')$$,
    'P0001', null, 'ninguém apaga a si mesmo');
select throws_ok(
    $$select seg.apagar_usuario('d1000000-0000-4000-8000-0000000000ff')$$,
    'P0001', null, 'pessoa que não existe');
select throws_ok(
    $$delete from seg.usuario where id = 'd1000000-0000-4000-8000-0000000000e0'$$,
    '42501', null, 'a API continua sem delete direto em seg.usuario');

update seg.usuario set ativo = false, desativado_em = now()
 where id = 'd1000000-0000-4000-8000-0000000000e0';

select lives_ok(
    $$select seg.apagar_usuario('d1000000-0000-4000-8000-0000000000e0')$$,
    'admin apaga quem está desativado');
select is((select count(*)::int from seg.usuario
            where id = 'd1000000-0000-4000-8000-0000000000e0'),
          0, 'a pessoa saiu do cadastro');

-- o Bruno é autor de uma regra de lançamento do seed: a FK não deixa apagar
update seg.usuario set ativo = false, desativado_em = now()
 where id = 'd1000000-0000-4000-8000-00000000000b';
select throws_ok(
    $$select seg.apagar_usuario('d1000000-0000-4000-8000-00000000000b')$$,
    '23503', null, 'quem tem registro que depende dele não se apaga');
select is((select count(*)::int from seg.usuario_cliente
            where usuario_id = 'd1000000-0000-4000-8000-0000000000e0'),
          0, 'com os vínculos');
select results_eq(
    $$select usuario_id, pedido_por from seg.convite_fila$$,
    $$values ('d1000000-0000-4000-8000-0000000000c0'::uuid, null::uuid)$$,
    'o pedido para ela foi junto; o que ela pediu ficou, sem autor');
select throws_ok('select count(*) from seg.login_a_apagar', '42501', null,
                 'a API não lê a fila de logins');
select throws_ok('select * from seg.logins_a_apagar()', '42501', null,
                 'nem pela função do job');

-- ---------------------------------------------------------------------------
-- Ana (Cliente principal de A) não apaga ninguém
-- ---------------------------------------------------------------------------
set local app.usuario_id = 'd1000000-0000-4000-8000-00000000000a';

select throws_ok(
    $$select seg.apagar_usuario('d1000000-0000-4000-8000-0000000000c0')$$,
    '42501', null, 'Ana não apaga usuário');

-- ---------------------------------------------------------------------------
-- job (role carga)
-- ---------------------------------------------------------------------------
reset role;

-- um login na fila que voltou a ter cadastro (o do Caio): não é para apagar
insert into seg.login_a_apagar (id_autenticacao)
values ('0d000000-0000-4000-8000-0000000000c0');

set local role carga;

select results_eq(
    $$select * from seg.logins_a_apagar()$$,
    $$values ('0d000000-0000-4000-8000-0000000000e0'::uuid)$$,
    'o job recebe o login da Eva, e não o de quem ainda tem cadastro');
select throws_ok('select count(*) from seg.login_a_apagar', '42501', null,
                 'carga não lê a fila direto');
select throws_ok(
    $$select seg.apagar_usuario('d1000000-0000-4000-8000-0000000000c0')$$,
    '42501', null, 'carga não apaga usuário');
select lives_ok(
    $$select seg.login_apagado('0d000000-0000-4000-8000-0000000000e0')$$,
    'o job tira da fila o login que apagou');
select is((select count(*)::int from seg.logins_a_apagar()), 0, 'a fila ficou vazia');

reset role;

select is((select count(*)::int from seg.login_a_apagar), 0,
          'e o login que tinha cadastro saiu da fila sem ser apagado');

select * from finish();
rollback;

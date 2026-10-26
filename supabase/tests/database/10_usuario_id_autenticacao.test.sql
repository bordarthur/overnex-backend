-- Migration 15: seg.usuario com id_autenticacao (id do Supabase Auth) e nome;
-- seg.resolver_usuario procura pelo id do provedor.
begin;

create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to api_site;

select plan(9);

select has_column('seg', 'usuario', 'id_autenticacao', 'seg.usuario.id_autenticacao');
select hasnt_column('seg', 'usuario', 'object_id_entra', 'object_id_entra saiu de seg.usuario');
select col_not_null('seg', 'usuario', 'id_autenticacao', 'id_autenticacao continua not null');
select col_is_unique('seg', 'usuario', 'id_autenticacao', 'id_autenticacao é único');
select has_column('seg', 'usuario', 'nome', 'seg.usuario.nome');
select has_column('seg', 'log_acesso', 'id_autenticacao', 'seg.log_acesso.id_autenticacao');
select is(
    (select count(*)::int
       from pg_constraint
      where conrelid = 'seg.usuario'::regclass and contype = 'f'),
    0,
    'seg.usuario sem FK (nem para auth.users)'
);

set local role api_site;

select results_eq(
    $$select id, ativo from seg.resolver_usuario('0d000000-0000-4000-8000-00000000000a')$$,
    $$values ('d1000000-0000-4000-8000-00000000000a'::uuid, true)$$,
    'resolver_usuario acha a Ana pelo id_autenticacao'
);
select is_empty(
    $$select * from seg.resolver_usuario('0d000000-0000-4000-8000-0000000000ff')$$,
    'id_autenticacao desconhecido: nenhuma linha'
);

select * from finish();
rollback;

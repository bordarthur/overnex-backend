-- Migration 37: o "Ramo" (cfg.cliente.segmento) saiu. A coluna não existe mais, e o api_site
-- só atualiza o nome do cliente; o segmento continua na unidade (cfg.empresa).
begin;

create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to api_site;

select plan(6);

select hasnt_column('cfg', 'cliente', 'segmento', 'cfg.cliente não tem mais o segmento');
select has_column('cfg', 'empresa', 'segmento', 'o segmento continua na unidade');

-- as colunas de cfg.cliente em que o api_site tem update (de tabela ou de coluna)
select is(
    array(select a.attname::text
            from pg_attribute a
           where a.attrelid = 'cfg.cliente'::regclass
             and a.attnum > 0
             and not a.attisdropped
             and has_column_privilege('api_site', a.attrelid, a.attnum, 'UPDATE')
           order by a.attnum),
    array['nome'],
    'o api_site atualiza só o nome do cliente');

-- ---------------------------------------------------------------------------
-- Dora (admin): cria e renomeia só com nome e slug
-- ---------------------------------------------------------------------------
set local role api_site;
set local app.usuario_id = 'd1000000-0000-4000-8000-0000000000ad';
set local app.cliente_id = '';

select lives_ok(
    $$insert into cfg.cliente (nome, slug) values ('Nova Delta', 'nova-delta')$$,
    'admin cria a empresa só com nome e slug');
select lives_ok(
    $$update cfg.cliente set nome = 'Nova Delta Ltda' where slug = 'nova-delta'$$,
    'admin troca o nome');
select throws_ok(
    $$update cfg.cliente set nome = 'X', segmento = 'Atacado' where slug = 'nova-delta'$$,
    '42703', null, 'o segmento do cliente não existe mais');

reset role;

select * from finish();
rollback;

-- Migration 34: a lista fechada de setores. Qualquer usuário a lê, ninguém a muda pela API.
-- O setor saiu do cliente e foi para a unidade na migration 35 (teste 25_unidades).
begin;

create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to api_site;

select plan(4);

-- ---------------------------------------------------------------------------
-- a lista de setores: todos leem, ninguém grava pela API
-- ---------------------------------------------------------------------------
set local role api_site;
set local app.usuario_id = 'd1000000-0000-4000-8000-00000000000a';

select results_eq(
    $$select id, rotulo from cfg.setor order by ordem$$,
    $$values ('distribuidora', 'Distribuidora'), ('industria', 'Indústria'),
             ('varejo', 'Varejo'), ('servicos', 'Serviços')$$,
    'Ana lê os 4 setores');
select throws_ok(
    $$insert into cfg.setor (id, rotulo) values ('padaria', 'Padaria')$$,
    '42501', null, 'a API não cria setor');
select throws_ok(
    $$update cfg.setor set rotulo = 'Outro' where id = 'varejo'$$,
    '42501', null, 'a API não troca setor');
select throws_ok(
    $$delete from cfg.setor where id = 'varejo'$$,
    '42501', null, 'a API não apaga setor');

reset role;

select * from finish();
rollback;

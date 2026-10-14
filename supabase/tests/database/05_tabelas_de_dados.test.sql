-- Tabelas de dados (mart.<tabela> com as colunas do Parquet): isolamento por cliente e
-- por empresa, permissões dos roles e preparo padrão.
begin;

create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to api_site, carga;

select plan(18);

-- preparo padrão aplicado às 21 tabelas da migration 5
select is(
    (select count(*)::int
       from pg_class c join pg_namespace n on n.oid = c.relnamespace
      where n.nspname = 'mart' and c.relkind = 'r'
        and c.relrowsecurity and c.relforcerowsecurity
        and exists (select 1 from pg_attribute a
                     where a.attrelid = c.oid and a.attname = '_linha')),
    21, 'as 21 tabelas de dados têm RLS ligado e forçado');
select has_column('mart', 'f_vendas_nilo_maia', 'en_VALTOT', 'nome da coluna preservado (maiúsculas)');
select col_type_is('mart', 'f_vendas_nilo_maia', 'en_VALTOT', 'numeric', 'double vira numeric');
select col_type_is('mart', 'f_balanco_nilo_maia', '_data_snapshot', 'date', 'date continua date');

-- Ana (cliente A, empresas A1 e A2)
set local role api_site;
set local app.usuario_id = 'd1000000-0000-4000-8000-00000000000a';

select is((select count(*)::int from mart.f_estoque_nilo_maia), 3, 'A lê as 3 linhas do próprio cliente');
select is((select count(*)::int from mart.f_estoque_nilo_maia
            where _cliente_id <> 'c1000000-0000-4000-8000-00000000000a'), 0, 'A não lê linha de B');
select is((select count(*)::int from cfg.perfil_tabela), 1, 'A lê só a permissão do próprio perfil');
select throws_ok($$insert into mart.f_estoque_nilo_maia (_linha, _cliente_id)
                   values (99, 'c1000000-0000-4000-8000-00000000000a')$$,
                 '42501', null, 'api_site não escreve em tabela de dados');

reset role;

-- Ana sem a empresa A2: perde as linhas de A2, mantém as do cliente inteiro (_empresa_id null)
delete from seg.usuario_empresa
 where usuario_id = 'd1000000-0000-4000-8000-00000000000a'
   and empresa_id = 'e1000000-0000-4000-8000-0000000000a2';
insert into mart.f_estoque_nilo_maia (_linha, _cliente_id, _empresa_id, "EMPRESA")
values (10, 'c1000000-0000-4000-8000-00000000000a', null, 'Padrão LANN Alfa');

set local role api_site;
set local app.usuario_id = 'd1000000-0000-4000-8000-00000000000a';
select is((select count(*)::int from mart.f_estoque_nilo_maia
            where _empresa_id = 'e1000000-0000-4000-8000-0000000000a2'), 0,
          'sem vínculo com A2, as linhas de A2 somem');
select is((select count(*)::int from mart.f_estoque_nilo_maia where _empresa_id is null), 1,
          'linhas do cliente inteiro (sem empresa) continuam visíveis');
reset role;

-- sem app.usuario_id: nada
set local role api_site;
set local app.usuario_id = '';
select is((select count(*)::int from mart.f_estoque_nilo_maia), 0, 'sem usuário, nenhuma linha');
reset role;

-- carga: escreve em tabela de dados; empresa precisa ser do mesmo cliente
set local role carga;
select lives_ok($$delete from mart.f_estoque_nilo_maia
                   where _cliente_id = 'c1000000-0000-4000-8000-00000000000b'$$,
                'carga apaga linhas de tabela de dados');
select lives_ok($$insert into mart.f_estoque_nilo_maia (_linha, _cliente_id, _empresa_id)
                  values (1, 'c1000000-0000-4000-8000-00000000000b',
                          'e1000000-0000-4000-8000-0000000000b1')$$,
                'carga insere em tabela de dados');
select throws_ok($$insert into mart.f_estoque_nilo_maia (_linha, _cliente_id, _empresa_id)
                   values (2, 'c1000000-0000-4000-8000-00000000000b',
                           'e1000000-0000-4000-8000-0000000000a1')$$,
                 '23503', null, 'empresa de outro cliente é recusada');
select throws_ok('select count(*) from cfg.perfil_tabela', '42501', null,
                 'carga não lê cfg.perfil_tabela');
reset role;

-- janela de datas (migration 6): coluna e meses andam juntos, e só no tipo tabela
select throws_ok(
    $$update cfg.fonte_carga set meses_retidos = 3 where tipo = 'tabela'$$,
    '23514', null, 'meses_retidos sem coluna_data é recusado');
select throws_ok(
    $$update cfg.fonte_carga set coluna_data = 'Data', meses_retidos = 3 where tipo = 'lancamento'$$,
    '23514', null, 'janela só vale para o tipo tabela');

-- a função de preparo não é chamável pelos roles da aplicação
set local role api_site;
select throws_ok($$select mart.preparar_tabela_dados('f_vendas_nilo_maia')$$, '42501', null,
                 'api_site não executa mart.preparar_tabela_dados');
reset role;

select * from finish();
rollback;

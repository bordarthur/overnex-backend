-- Migration 35: setor, segmento, grupo e endereço da unidade (cfg.empresa). O setor saiu do
-- cliente; seg.contexto_acesso devolve o setor de cada unidade liberada e os setores delas;
-- o consultor lê as unidades da carteira sem escolher um cliente.
begin;

create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to api_site;

select plan(17);

-- ---------------------------------------------------------------------------
-- a tabela
-- ---------------------------------------------------------------------------
select hasnt_column('cfg', 'cliente', 'setor', 'o setor saiu do cliente');
select has_column('cfg', 'empresa', 'setor', 'a unidade tem setor');
select has_column('cfg', 'empresa', 'grupo', 'e grupo');
select throws_ok(
    $$update cfg.empresa set setor = 'padaria' where id = 'e1000000-0000-4000-8000-0000000000b1'$$,
    '23503', null, 'setor fora da lista não passa');
select throws_ok(
    $$update cfg.empresa set uf = 'ce' where id = 'e1000000-0000-4000-8000-0000000000b1'$$,
    '23514', null, 'UF só com 2 letras maiúsculas');
select throws_ok(
    $$update cfg.empresa set grupo = '  ' where id = 'e1000000-0000-4000-8000-0000000000b1'$$,
    '23514', null, 'grupo em branco não passa (sem grupo é null)');

-- ---------------------------------------------------------------------------
-- seg.contexto_acesso: o setor de cada unidade e os setores das liberadas
-- ---------------------------------------------------------------------------
-- a Alfa ganha uma unidade de Varejo que a Ana não tem no vínculo
insert into cfg.empresa (id, cliente_id, nome, setor, grupo)
values ('e1000000-0000-4000-8000-0000000000a3', 'c1000000-0000-4000-8000-00000000000a',
        'Alfa Loja', 'varejo', 'Loja');
-- o trigger da migration 30 pôs a unidade nova nos vínculos; a Ana fica sem ela
delete from seg.usuario_empresa
 where usuario_id = 'd1000000-0000-4000-8000-00000000000a'
   and empresa_id = 'e1000000-0000-4000-8000-0000000000a3';

set local role api_site;

select is((select setores from seg.contexto_acesso('0d000000-0000-4000-8000-00000000000a', 'VG-01')),
          array['distribuidora']::text[], 'Ana: só o setor das unidades liberadas a ela');
select is((select jsonb_agg(x ->> 'setor' order by x ->> 'empresa_id')
             from seg.contexto_acesso('0d000000-0000-4000-8000-00000000000a', 'VG-01') f,
                  jsonb_array_elements(f.empresas) x),
          '["distribuidora", "distribuidora"]'::jsonb, 'cada unidade da Ana vem com o setor');
select is((select setores
             from seg.contexto_acesso('0d000000-0000-4000-8000-0000000000ad', 'VG-01', 'padrao-lann-alfa')),
          array['distribuidora', 'varejo']::text[], 'admin na Alfa: os setores de todas as unidades');
select is((select setores from seg.contexto_acesso('0d000000-0000-4000-8000-00000000000b', 'VG-01')),
          '{}'::text[], 'Bruno (Beta, unidade sem setor): vazio');
select is((select jsonb_array_length(empresas)
             from seg.contexto_acesso('0d000000-0000-4000-8000-00000000000b', 'VG-01')),
          1, 'a unidade sem setor continua liberada');
select is((select setores from seg.contexto_acesso('0d000000-0000-4000-8000-0000000000ad', 'VG-01')),
          '{}'::text[], 'admin sem cliente escolhido: vazio');
select is((select setores from seg.contexto_acesso('0d000000-0000-4000-8000-0000000000ff', 'VG-01')),
          '{}'::text[], 'usuário desconhecido: vazio');

-- (quem edita a unidade pela API, desde a migration 36: o teste 26_editar_unidade)

-- ---------------------------------------------------------------------------
-- o consultor (Caio, vínculo com Alfa e Beta) lê as unidades da carteira sem escolher um
-- ---------------------------------------------------------------------------
set local app.usuario_id = 'd1000000-0000-4000-8000-0000000000c0';
select results_eq(
    $$select nome from cfg.empresa order by nome$$,
    $$values ('Alfa Comércio'), ('Alfa Indústria'), ('Alfa Loja'), ('Beta Serviços')$$,
    'consultor sem cliente escolhido: as unidades dos vínculos');

-- sem a unidade nova no vínculo, ele não a vê
reset role;
delete from seg.usuario_empresa
 where usuario_id = 'd1000000-0000-4000-8000-0000000000c0'
   and empresa_id = 'e1000000-0000-4000-8000-0000000000a3';
set local role api_site;
set local app.usuario_id = 'd1000000-0000-4000-8000-0000000000c0';
select results_eq(
    $$select count(*)::int from cfg.empresa where cliente_id = 'c1000000-0000-4000-8000-00000000000a'$$,
    $$values (2)$$,
    'consultor não vê unidade fora do vínculo');

-- com um cliente escolhido, o consultor só vê as unidades dele (o RLS do cliente escolhido)
set local app.cliente_id = 'c1000000-0000-4000-8000-00000000000b';
select results_eq(
    $$select nome from cfg.empresa$$,
    $$values ('Beta Serviços')$$,
    'consultor com a Beta escolhida: só a unidade da Beta');
set local app.cliente_id = '';

-- a Ana (não consultora), sem cliente escolhido e com um vínculo só: as dela
set local app.usuario_id = 'd1000000-0000-4000-8000-00000000000a';
select results_eq(
    $$select count(*)::int from cfg.empresa$$,
    $$values (2)$$,
    'cliente comum continua só com as unidades liberadas');

reset role;

select * from finish();
rollback;

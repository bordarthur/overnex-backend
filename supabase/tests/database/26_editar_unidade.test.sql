-- Migration 36: só o admin edita a unidade, e só nas colunas da tela; ninguém cria, apaga ou
-- muda o cliente da unidade pela API.
begin;

create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to api_site;

select plan(8);

set local role api_site;

-- ---------------------------------------------------------------------------
-- Dora (admin) edita
-- ---------------------------------------------------------------------------
set local app.usuario_id = 'd1000000-0000-4000-8000-0000000000ad';
set local app.cliente_id = '';

select lives_ok(
    $$update cfg.empresa set nome = 'Alfa Fábrica', setor = 'industria', grupo = null,
                             uf = 'PE', cidade = 'Recife', bairro = 'Boa Vista',
                             endereco = 'Rua 1', segmento = 'Carnes'
       where id = 'e1000000-0000-4000-8000-0000000000a1'$$,
    'admin troca as colunas da tela');
select is((select setor from cfg.empresa where id = 'e1000000-0000-4000-8000-0000000000a1'),
          'industria', 'e a troca vale');
select throws_ok(
    $$update cfg.empresa set cliente_id = 'c1000000-0000-4000-8000-00000000000b'
       where id = 'e1000000-0000-4000-8000-0000000000a1'$$,
    '42501', null, 'o cliente da unidade não muda pela API');
select throws_ok(
    $$insert into cfg.empresa (cliente_id, nome) values ('c1000000-0000-4000-8000-00000000000a', 'Nova')$$,
    '42501', null, 'unidade nova não sai pela API');
select throws_ok(
    $$delete from cfg.empresa where id = 'e1000000-0000-4000-8000-0000000000a2'$$,
    '42501', null, 'nem apagar unidade');

-- ---------------------------------------------------------------------------
-- Caio (consultor) e Ana (cliente): o update não acha a linha
-- ---------------------------------------------------------------------------
set local app.usuario_id = 'd1000000-0000-4000-8000-0000000000c0';
update cfg.empresa set nome = 'Do consultor' where id = 'e1000000-0000-4000-8000-0000000000a2';
set local app.usuario_id = 'd1000000-0000-4000-8000-00000000000a';
set local app.cliente_id = 'c1000000-0000-4000-8000-00000000000a';
update cfg.empresa set nome = 'Da Ana' where id = 'e1000000-0000-4000-8000-0000000000a2';

reset role;

select is((select nome from cfg.empresa where id = 'e1000000-0000-4000-8000-0000000000a2'),
          'Alfa Comércio', 'consultor e cliente não mudam a unidade');
select is((select count(*)::int from cfg.empresa where cliente_id = 'c1000000-0000-4000-8000-00000000000a'),
          2, 'nenhuma unidade nova');
select is((select cliente_id from cfg.empresa where id = 'e1000000-0000-4000-8000-0000000000a1'),
          'c1000000-0000-4000-8000-00000000000a'::uuid, 'a unidade continua no cliente dela');

select * from finish();
rollback;

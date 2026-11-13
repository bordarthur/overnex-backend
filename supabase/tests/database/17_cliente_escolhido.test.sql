-- Migration 26: o cliente escolhido na requisição (app.cliente_id). O RLS só mostra o
-- cliente escolhido; o consultor (dois vínculos) e o admin (sem vínculo) não leem dado
-- nenhum sem escolher, e o slug só vale entre os clientes que a pessoa pode abrir.
-- Seed: Ana (cliente A), Bruno (B), Caio (consultor, A e B), Dora (admin).
-- mart.lancamento: 96 linhas de A (2 empresas) e 48 de B.
begin;

create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to api_site;

select plan(34);

set local role api_site;

-- Caio, sem slug: dois vínculos, nenhum cliente escolhido, nenhum dado
select is((select cliente_id from seg.contexto_acesso('0d000000-0000-4000-8000-0000000000c0', 'VG-01')),
          null::uuid, 'consultor sem slug: nenhum cliente escolhido');
select is(current_setting('app.cliente_id', true), '', 'consultor sem slug: app.cliente_id vazio');
select is((select jsonb_array_length(vinculos)
             from seg.contexto_acesso('0d000000-0000-4000-8000-0000000000c0', 'VG-01')),
          2, 'consultor: os dois vínculos nos fatos');
select is((select count(*)::int from seg.clientes_do_usuario()), 0,
          'consultor sem escolha: clientes_do_usuario() vazio');
select is((select count(*)::int from mart.lancamento), 0, 'consultor sem escolha: nenhum lançamento');
select is((select count(*)::int from cfg.cliente), 2,
          'consultor sem escolha: lê o nome dos dois clientes (seletor)');

-- Caio escolhe A
select is((select cliente_id
             from seg.contexto_acesso('0d000000-0000-4000-8000-0000000000c0', 'VG-01', 'padrao-lann-alfa')),
          'c1000000-0000-4000-8000-00000000000a'::uuid, 'consultor escolhe A');
select results_eq('select * from seg.clientes_do_usuario()',
                  $$values ('c1000000-0000-4000-8000-00000000000a'::uuid)$$,
                  'clientes_do_usuario() = só A');
select is((select count(*)::int from mart.lancamento), 96, 'consultor em A: só os lançamentos de A');
select is((select count(*)::int from cfg.empresa), 2, 'consultor em A: as 2 empresas de A');
select is((select count(*)::int from cfg.cliente), 2, 'consultor em A: continua lendo o nome dos dois');
select is((select perfis_tela
             from seg.contexto_acesso('0d000000-0000-4000-8000-0000000000c0', 'VG-01', 'padrao-lann-alfa')),
          '{"c1000000-0000-4000-8000-00000000000a": false}'::jsonb,
          'perfil fixo de consultor libera a VG-01 em A');
select is((select perfis_tela
             from seg.contexto_acesso('0d000000-0000-4000-8000-0000000000c0', 'FIN-02', 'padrao-lann-alfa')),
          '{}'::jsonb, 'FIN-02 está ligada em A, mas fora do perfil do consultor');

-- Caio escolhe B
select is((select cliente_id
             from seg.contexto_acesso('0d000000-0000-4000-8000-0000000000c0', 'VG-01', 'padrao-lann-beta')),
          'c1000000-0000-4000-8000-00000000000b'::uuid, 'consultor escolhe B');
select is((select count(*)::int from mart.lancamento), 48, 'consultor em B: só os lançamentos de B');
select is((select count(*)::int from mart.lancamento
            where empresa_id <> 'e1000000-0000-4000-8000-0000000000b1'), 0,
          'consultor em B: nada de A');

-- slug que não existe
select is((select cliente_id
             from seg.contexto_acesso('0d000000-0000-4000-8000-0000000000c0', 'VG-01', 'nao-existe')),
          null::uuid, 'slug inexistente: nenhum cliente escolhido');
select is((select count(*)::int from mart.lancamento), 0, 'slug inexistente: nenhum lançamento');

-- Ana (um vínculo): sem slug, A; pedindo B, nada de B
select is((select cliente_id from seg.contexto_acesso('0d000000-0000-4000-8000-00000000000a', 'VG-01')),
          'c1000000-0000-4000-8000-00000000000a'::uuid, 'um vínculo, sem slug: o cliente do vínculo');
select is(current_setting('app.cliente_id', true), 'c1000000-0000-4000-8000-00000000000a',
          'um vínculo, sem slug: app.cliente_id definido');
select is((select cliente_id
             from seg.contexto_acesso('0d000000-0000-4000-8000-00000000000a', 'VG-01', 'padrao-lann-beta')),
          null::uuid, 'slug fora do vínculo: nenhum cliente escolhido');
select is((select count(*)::int from mart.lancamento
            where empresa_id = 'e1000000-0000-4000-8000-0000000000b1'), 0,
          'slug fora do vínculo: nada de B');
select is((select count(*)::int from cfg.cliente), 1, 'Ana só lê o nome do próprio cliente');

-- Dora (admin), sem slug: perfil admin, sem vínculos, nenhum dado
select is((select perfil from seg.contexto_acesso('0d000000-0000-4000-8000-0000000000ad', 'VG-01')),
          'admin', 'admin: perfil nos fatos');
select is((select vinculos from seg.contexto_acesso('0d000000-0000-4000-8000-0000000000ad', 'VG-01')),
          '[]'::jsonb, 'admin: sem vínculos');
select is((select count(*)::int from mart.lancamento), 0, 'admin sem escolha: nenhum lançamento');
select is((select count(*)::int from cfg.cliente), 2, 'admin: lê o nome de todos os clientes');

-- Dora escolhe B
select is((select cliente_id
             from seg.contexto_acesso('0d000000-0000-4000-8000-0000000000ad', 'VG-01', 'padrao-lann-beta')),
          'c1000000-0000-4000-8000-00000000000b'::uuid, 'admin escolhe B');
select is((select count(*)::int from mart.lancamento), 48, 'admin em B: só os lançamentos de B');
select is((select empresas
             from seg.contexto_acesso('0d000000-0000-4000-8000-0000000000ad', 'VG-01', 'padrao-lann-beta')),
          '[{"cliente_id": "c1000000-0000-4000-8000-00000000000b", "empresa_id": "e1000000-0000-4000-8000-0000000000b1"}]'::jsonb,
          'admin em B: todas as empresas de B');
select is((select perfis_tela
             from seg.contexto_acesso('0d000000-0000-4000-8000-0000000000ad', 'VG-01', 'padrao-lann-beta')),
          '{"c1000000-0000-4000-8000-00000000000b": true}'::jsonb,
          'admin: tela ligada no cliente, com dado nominal');
select is((select perfis_tela
             from seg.contexto_acesso('0d000000-0000-4000-8000-0000000000ad', 'XX-99', 'padrao-lann-beta')),
          '{}'::jsonb, 'admin: tela que não está ligada no cliente não abre');

-- painel desligado no cliente: não abre nem para o admin nem para quem tem a tela no perfil
reset role;
update cfg.cliente_tela set ligada = false
 where cliente_id = 'c1000000-0000-4000-8000-00000000000b' and codigo_tela = 'VG-01';
set local role api_site;

select is((select perfis_tela
             from seg.contexto_acesso('0d000000-0000-4000-8000-0000000000ad', 'VG-01', 'padrao-lann-beta')),
          '{}'::jsonb, 'painel desligado: não abre para o admin');
select is((select perfis_tela from seg.contexto_acesso('0d000000-0000-4000-8000-00000000000b', 'VG-01')),
          '{}'::jsonb, 'painel desligado: não abre para o Bruno, que tem a tela no perfil');

reset role;

select * from finish();
rollback;

-- Migration 16: seg.contexto_acesso define app.usuario_id e devolve os fatos da checagem,
-- sob o RLS de api_site (Ana, cliente A, do seed).
begin;

create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to api_site;

select plan(10);

set local role api_site;

-- usuário desconhecido: nada, e app.usuario_id vazio
select results_eq(
    $$select usuario_id, ativo, vinculos, perfis_tela, empresas
        from seg.contexto_acesso('0d000000-0000-4000-8000-0000000000ff', 'VG-01')$$,
    $$values (null::uuid, false, '[]'::jsonb, '{}'::jsonb, '[]'::jsonb)$$,
    'id desconhecido: sem usuário e listas vazias'
);
select is(current_setting('app.usuario_id', true), '', 'desconhecido: app.usuario_id vazio');

-- Ana (cada chamada define app.usuario_id de novo; sem tabela temporária: api_site não cria)
select is((select ativo from seg.contexto_acesso('0d000000-0000-4000-8000-00000000000a', 'VG-01')),
          true, 'ativo');
select is(current_setting('app.usuario_id', true), 'd1000000-0000-4000-8000-00000000000a',
          'define app.usuario_id da Ana');
select is((select usuario_id from seg.contexto_acesso('0d000000-0000-4000-8000-00000000000a', 'VG-01')), 'd1000000-0000-4000-8000-00000000000a'::uuid, 'usuario_id');
select is((select vinculos from seg.contexto_acesso('0d000000-0000-4000-8000-00000000000a', 'VG-01')),
          '[{"cliente_id": "c1000000-0000-4000-8000-00000000000a", "papel": "gestor"}]'::jsonb,
          'um vínculo, cliente A, gestor');
select is((select perfis_tela from seg.contexto_acesso('0d000000-0000-4000-8000-00000000000a', 'VG-01')),
          '{"c1000000-0000-4000-8000-00000000000a": false}'::jsonb,
          'perfil do cliente A libera a VG-01, sem dado nominal');
-- ordem por nome: Alfa Comércio (a2) antes de Alfa Indústria (a1)
select is((select empresas from seg.contexto_acesso('0d000000-0000-4000-8000-00000000000a', 'VG-01')),
          '[{"cliente_id": "c1000000-0000-4000-8000-00000000000a", "empresa_id": "e1000000-0000-4000-8000-0000000000a2"},
            {"cliente_id": "c1000000-0000-4000-8000-00000000000a", "empresa_id": "e1000000-0000-4000-8000-0000000000a1"}]'::jsonb,
          'as duas empresas do cliente A, por nome');

select is(
    (select perfis_tela from seg.contexto_acesso('0d000000-0000-4000-8000-00000000000a', 'XX-99')),
    '{}'::jsonb,
    'tela que nenhum perfil libera: perfis_tela vazio'
);
select is(
    (select perfis_tela from seg.contexto_acesso('0d000000-0000-4000-8000-00000000000a', null)),
    '{}'::jsonb,
    'sem tela (sessão, tabela): perfis_tela vazio'
);

select * from finish();
rollback;

-- Migration 27: o que a Gestão lê e grava. Só o admin (Dora) lê todas as pessoas e grava
-- cadastro; ninguém troca e-mail nem cria ou apaga usuário pela API; os logs continuam sem
-- leitura direta; e quem não é admin (Ana) não ganha nada.
begin;

create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to api_site;

select plan(31);

-- ---------------------------------------------------------------------------
-- Dora (admin), sem cliente escolhido
-- ---------------------------------------------------------------------------
set local role api_site;
set local app.usuario_id = 'd1000000-0000-4000-8000-0000000000ad';

select ok(seg.eh_admin(), 'Dora é admin');
select is((select count(*)::int from seg.usuario), 4, 'admin lê todas as pessoas');
select is((select count(*)::int from seg.usuario_cliente), 4, 'admin lê todos os vínculos');
select is((select count(*)::int from cfg.cliente_tela), 18, 'admin lê os painéis ligados de todos os clientes');
select is((select count(*)::int from mart.lancamento), 0,
          'admin sem cliente escolhido não lê dado de negócio');
select is((select count(*)::int from seg.resumo_acessos()), 4, 'admin lê o resumo de acessos de todos');
select throws_ok('select count(*) from seg.log_acesso', '42501', null,
                 'nem o admin lê seg.log_acesso direto');

-- seg.usuario: troca só nome, perfil, ativo, sessão e desativado_em
select lives_ok(
    $$update seg.usuario set sessao_minutos = 720, nome = 'Bruno B.'
       where id = 'd1000000-0000-4000-8000-00000000000b'$$,
    'admin troca nome e sessão de outra pessoa');
select throws_ok(
    $$update seg.usuario set email = 'outro@beta.example'
       where id = 'd1000000-0000-4000-8000-00000000000b'$$,
    '42501', null, 'o e-mail é o login: nem o admin troca');
select throws_ok(
    $$insert into seg.usuario (id_autenticacao, email, tipo)
      values ('0d000000-0000-4000-8000-0000000000ee', 'novo@beta.example', 'cliente')$$,
    '42501', null, 'a API não insere usuário direto (só por seg.pedir_convite, migration 29)');
select throws_ok(
    $$delete from seg.usuario where id = 'd1000000-0000-4000-8000-00000000000b'$$,
    '42501', null, 'a API não apaga usuário');
select throws_ok(
    $$update seg.usuario set sessao_minutos = 1440
       where id = 'd1000000-0000-4000-8000-00000000000b'$$,
    '23514', null, 'mais de 12 h de sessão, só para o perfil admin');
select throws_ok(
    $$update seg.usuario set sessao_minutos = 45
       where id = 'd1000000-0000-4000-8000-00000000000b'$$,
    '23514', null, 'tempo de sessão fora da lista');

-- vínculos: Bruno (Cliente usuário) passa a ter A e B
select lives_ok(
    $$select seg.definir_vinculos(
          'd1000000-0000-4000-8000-00000000000b',
          array['c1000000-0000-4000-8000-00000000000a',
                'c1000000-0000-4000-8000-00000000000b']::uuid[])$$,
    'admin troca os clientes de uma pessoa');
select is((select count(*)::int from seg.usuario_cliente
            where usuario_id = 'd1000000-0000-4000-8000-00000000000b' and papel = 'leitor'),
          2, 'dois vínculos, com o papel do perfil (leitor)');
select is((select count(*)::int
             from seg.usuario_perfil up
             join cfg.perfil_acesso p on p.id = up.perfil_id
            where up.usuario_id = 'd1000000-0000-4000-8000-00000000000b'
              and p.perfil = 'cliente_usuario'),
          2, 'perfil fixo de Cliente usuário em cada cliente (o livre saiu)');
select is((select count(*)::int from seg.usuario_empresa
            where usuario_id = 'd1000000-0000-4000-8000-00000000000b'),
          3, 'todas as empresas dos dois clientes');

-- painéis, matriz e feriados
select lives_ok(
    $$insert into cfg.cliente_tela (cliente_id, codigo_tela)
      values ('c1000000-0000-4000-8000-00000000000a', 'EST-01')$$,
    'admin liga um painel no cliente');
select lives_ok(
    $$insert into cfg.perfil_tela (perfil_id, codigo_tela)
      select id, 'PES-01' from cfg.perfil_acesso
       where cliente_id = 'c1000000-0000-4000-8000-00000000000a' and perfil = 'cliente_principal'$$,
    'admin põe o painel num perfil fixo');
select throws_ok(
    $$insert into cfg.perfil_tela (perfil_id, codigo_tela)
      values ('f1000000-0000-4000-8000-00000000000a', 'PES-01')$$,
    '42501', null, 'a matriz não mexe em perfil livre');
select lives_ok(
    $$insert into cfg.feriado (cliente_id, data, nome)
      values ('c1000000-0000-4000-8000-00000000000a', '2026-08-15', 'Padroeira')$$,
    'admin cadastra feriado do cliente');
select throws_ok(
    $$insert into cfg.empresa (cliente_id, nome)
      values ('c1000000-0000-4000-8000-00000000000a', 'Nova')$$,
    '42501', null, 'empresa não se cadastra pela API');
select lives_ok(
    $$select seg.encerrar_sessoes('d1000000-0000-4000-8000-00000000000b')$$,
    'admin encerra as sessões de uma pessoa');

-- ---------------------------------------------------------------------------
-- Ana (Cliente principal de A): nada disso
-- ---------------------------------------------------------------------------
set local app.usuario_id = 'd1000000-0000-4000-8000-00000000000a';

select ok(not seg.eh_admin(), 'Ana não é admin');
select is((select count(*)::int from seg.usuario), 1, 'Ana lê só o próprio registro');
select is((select count(*)::int from seg.resumo_acessos()), 0, 'Ana não lê o resumo de acessos');
select throws_ok(
    $$select seg.encerrar_sessoes('d1000000-0000-4000-8000-00000000000b')$$,
    '42501', null, 'Ana não encerra sessões');
select throws_ok(
    $$insert into cfg.cliente_tela (cliente_id, codigo_tela)
      values ('c1000000-0000-4000-8000-00000000000a', 'XX-01')$$,
    '42501', null, 'Ana não liga painel');
select throws_ok(
    $$insert into cfg.feriado (cliente_id, data, nome)
      values ('c1000000-0000-4000-8000-00000000000a', '2026-09-07', 'Outro')$$,
    '42501', null, 'Ana não cadastra feriado');
select throws_ok(
    $$select seg.definir_vinculos(
          'd1000000-0000-4000-8000-00000000000a',
          array['c1000000-0000-4000-8000-00000000000b']::uuid[])$$,
    '42501', null, 'Ana não se dá vínculo com outro cliente');

-- o update da Ana em outra pessoa não acha a linha (RLS)
update seg.usuario set nome = 'Invadido' where id = 'd1000000-0000-4000-8000-00000000000b';

reset role;

select is((select nome from seg.usuario where id = 'd1000000-0000-4000-8000-00000000000b'),
          'Bruno B.', 'o update da Ana não mexeu em outra pessoa');

select * from finish();
rollback;

-- O usuário do cliente A (Ana) não lê nenhuma linha do cliente B em mart e cfg.
begin;

create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to api_site;

select plan(25);

set local role api_site;
set local app.usuario_id = 'd1000000-0000-4000-8000-00000000000a';

-- sanidade: A enxerga os próprios dados (senão os "zero de B" abaixo não provam nada)
select is((select count(*)::int from mart.lancamento), 96, 'A lê os 96 lançamentos das suas 2 empresas');
select is((select count(*)::int from mart.carga_fonte), 1, 'A lê a própria linha de carga_fonte');
select is((select count(*)::int from cfg.cliente), 1, 'A lê só o próprio cliente');
select is((select count(*)::int from cfg.empresa), 2, 'A lê só as próprias empresas');

-- mart
select is((select count(*)::int from mart.lancamento
            where empresa_id = 'e1000000-0000-4000-8000-0000000000b1'), 0,
          'mart.lancamento: nada da empresa de B');
select is((select count(*)::int from mart.carga_fonte
            where cliente_id = 'c1000000-0000-4000-8000-00000000000b'), 0,
          'mart.carga_fonte: nada do cliente B');
select throws_ok('select count(*) from mart.carga', '42501', null,
                 'mart.carga: api_site não tem acesso');

-- cfg: nenhuma linha que não seja do cliente A
select is((select count(*)::int from cfg.cliente
            where id <> 'c1000000-0000-4000-8000-00000000000a'), 0, 'cfg.cliente');
select is((select count(*)::int from cfg.empresa
            where cliente_id <> 'c1000000-0000-4000-8000-00000000000a'), 0, 'cfg.empresa');
select is((select count(*)::int from cfg.painel
            where cliente_id <> 'c1000000-0000-4000-8000-00000000000a'), 0, 'cfg.painel');
select is((select count(*)::int from cfg.perfil_acesso
            where cliente_id <> 'c1000000-0000-4000-8000-00000000000a'), 0, 'cfg.perfil_acesso');
select is((select count(*)::int from cfg.perfil_tela
            where perfil_id <> 'f1000000-0000-4000-8000-00000000000a'), 0, 'cfg.perfil_tela');
select is((select count(*)::int from cfg.parametro
            where empresa_id = 'e1000000-0000-4000-8000-0000000000b1'), 0, 'cfg.parametro');
select is((select count(*)::int from cfg.regra_lancamento
            where empresa_id = 'e1000000-0000-4000-8000-0000000000b1'), 0, 'cfg.regra_lancamento');
select is((select count(*)::int from cfg.fonte_carga
            where cliente_id <> 'c1000000-0000-4000-8000-00000000000a'), 0, 'cfg.fonte_carga');
select is((select count(*)::int from cfg.mapa_empresa
            where cliente_id <> 'c1000000-0000-4000-8000-00000000000a'), 0, 'cfg.mapa_empresa');

-- seg: só os próprios registros
select is((select count(*)::int from seg.usuario), 1, 'seg.usuario: só o próprio');
select is((select count(*)::int from seg.usuario_empresa
            where usuario_id <> 'd1000000-0000-4000-8000-00000000000a'), 0, 'seg.usuario_empresa: só os próprios');

-- funções de apoio
select results_eq('select * from seg.clientes_do_usuario()',
                  $$values ('c1000000-0000-4000-8000-00000000000a'::uuid)$$,
                  'clientes_do_usuario() devolve só o cliente A');
select is((select count(*)::int from seg.empresas_do_usuario()), 2, 'empresas_do_usuario() devolve as 2 de A');

-- api_site não escreve no mart nem lê logs
select throws_ok($$insert into mart.lancamento values ('e1000000-0000-4000-8000-0000000000a1', '2020-01-01', 1, 1)$$,
                 '42501', null, 'api_site não escreve em mart.lancamento');
select throws_ok('select count(*) from seg.log_acesso', '42501', null, 'api_site não lê seg.log_acesso');

-- log de acesso: aceita o próprio usuário, recusa outro
select lives_ok($$insert into seg.log_acesso (usuario_id, codigo_tela, resultado)
                  values ('d1000000-0000-4000-8000-00000000000a', 'FIN-01', 'permitido')$$,
                'api_site grava log de acesso do próprio usuário');
select throws_ok($$insert into seg.log_acesso (usuario_id, codigo_tela, resultado)
                   values ('d1000000-0000-4000-8000-00000000000b', 'FIN-01', 'permitido')$$,
                 '42501', null, 'api_site não grava log em nome de outro usuário');

-- escrita em cadastros: grant existe, mas sem política o RLS nega
select throws_ok($$insert into cfg.empresa (cliente_id, nome)
                   values ('c1000000-0000-4000-8000-00000000000a', 'Nova')$$,
                 '42501', null, 'sem política de escrita, cadastro é negado pelo RLS');

reset role;

select * from finish();
rollback;

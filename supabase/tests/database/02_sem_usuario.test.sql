-- Sem app.usuario_id, api_site não vê nenhuma linha.
-- Arquivo próprio: cada arquivo roda numa sessão nova, em que app.usuario_id nunca foi definido.
begin;

create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to api_site;

select plan(20);

set local role api_site;

select is(current_setting('app.usuario_id', true), null, 'app.usuario_id não definido');

select is((select count(*)::int from mart.lancamento), 0, 'mart.lancamento');
select is((select count(*)::int from mart.carga_fonte), 0, 'mart.carga_fonte');
select is((select count(*)::int from cfg.cliente), 0, 'cfg.cliente');
select is((select count(*)::int from cfg.empresa), 0, 'cfg.empresa');
select is((select count(*)::int from cfg.painel), 0, 'cfg.painel');
select is((select count(*)::int from cfg.perfil_acesso), 0, 'cfg.perfil_acesso');
select is((select count(*)::int from cfg.perfil_tela), 0, 'cfg.perfil_tela');
select is((select count(*)::int from cfg.parametro), 0, 'cfg.parametro');
select is((select count(*)::int from cfg.regra_lancamento), 0, 'cfg.regra_lancamento');
select is((select count(*)::int from cfg.fonte_carga), 0, 'cfg.fonte_carga');
select is((select count(*)::int from cfg.mapa_empresa), 0, 'cfg.mapa_empresa');
select is((select count(*)::int from seg.usuario), 0, 'seg.usuario');
select is((select count(*)::int from seg.usuario_cliente), 0, 'seg.usuario_cliente');
select is((select count(*)::int from seg.usuario_perfil), 0, 'seg.usuario_perfil');
select is((select count(*)::int from seg.usuario_empresa), 0, 'seg.usuario_empresa');
select is((select count(*)::int from seg.clientes_do_usuario()), 0, 'clientes_do_usuario()');
select is((select count(*)::int from seg.empresas_do_usuario()), 0, 'empresas_do_usuario()');

-- string vazia (como fica depois de um set_config local terminar) também não vê nada
set local app.usuario_id = '';
select is((select count(*)::int from mart.lancamento), 0, 'app.usuario_id vazio: mart.lancamento');
select is((select count(*)::int from cfg.empresa), 0, 'app.usuario_id vazio: cfg.empresa');

reset role;

select * from finish();
rollback;

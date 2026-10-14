-- O role carga escreve no mart, lê só o necessário de cfg, não lê seg e não escreve em cfg.
begin;

create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to carga;

select plan(14);

set local role carga;

-- seg: nenhum acesso
select throws_ok('select count(*) from seg.usuario', '42501', null, 'carga não lê seg.usuario');
select throws_ok('select count(*) from seg.usuario_empresa', '42501', null, 'carga não lê seg.usuario_empresa');
select throws_ok('select count(*) from seg.log_acesso', '42501', null, 'carga não lê seg.log_acesso');

-- cfg: lê só fonte_carga, mapa_empresa e empresa
select is((select count(*)::int from cfg.fonte_carga), 4, 'carga lê cfg.fonte_carga');
select is((select count(*)::int from cfg.mapa_empresa), 3, 'carga lê cfg.mapa_empresa');
select is((select count(*)::int from cfg.empresa), 3, 'carga lê cfg.empresa');
select throws_ok('select count(*) from cfg.cliente', '42501', null, 'carga não lê cfg.cliente');

-- cfg: não escreve
select throws_ok($$insert into cfg.cliente (nome, slug) values ('X', 'x')$$,
                 '42501', null, 'carga não insere em cfg.cliente');
select throws_ok($$update cfg.empresa set nome = nome$$,
                 '42501', null, 'carga não altera cfg.empresa');
select throws_ok($$delete from cfg.mapa_empresa$$,
                 '42501', null, 'carga não apaga cfg.mapa_empresa');

-- mart: escreve
select lives_ok($$insert into mart.carga (status) values ('rodando')$$, 'carga insere em mart.carga');
select lives_ok($$update mart.lancamento set valor = valor
                   where empresa_id = 'e1000000-0000-4000-8000-0000000000a1'$$,
                'carga altera mart.lancamento');

-- tabela temporária (a carga usa staging temporário em vez de stg)
select lives_ok('create temp table tmp_lancamento (like mart.lancamento)', 'carga cria tabela temporária');
select throws_ok('create table stg.qualquer (id int)', '42501', null, 'carga não cria tabela em stg');

reset role;

select * from finish();
rollback;

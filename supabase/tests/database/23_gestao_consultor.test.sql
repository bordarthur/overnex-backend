-- Migration 33: o consultor (Caio, com vínculo em A e B) lê os painéis ligados, as fontes e
-- as cargas dos clientes da carteira sem escolher um, e nada de cliente fora dela; não
-- grava nada; quem não é consultor continua só com o cliente escolhido (ou o único).
begin;

create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to api_site;

select plan(9);

-- um cliente fora da carteira do Caio, com painel ligado, fonte e carga
insert into cfg.cliente (id, nome, slug)
values ('c1000000-0000-4000-8000-0000000000cc', 'Gama Fora', 'gama-fora');
insert into cfg.cliente_tela (cliente_id, codigo_tela, ligada)
values ('c1000000-0000-4000-8000-0000000000cc', 'VG-01', true);
insert into cfg.fonte_carga (id, cliente_id, tipo, caminho_bucket, tabela_destino)
values (990033, 'c1000000-0000-4000-8000-0000000000cc', 'tabela', 'gama/f_vendas_gama.parquet',
        'f_vendas_gama');
insert into mart.carga (id, inicio, fim, status) overriding system value
values (990033, now(), now(), 'ok');
insert into mart.carga_fonte (carga_id, fonte_id, cliente_id, data_snapshot, status)
values (990033, 990033, 'c1000000-0000-4000-8000-0000000000cc', current_date, 'ok');

-- ---------------------------------------------------------------------------
-- Caio (consultor), sem cliente escolhido
-- ---------------------------------------------------------------------------
set local role api_site;
set local app.usuario_id = 'd1000000-0000-4000-8000-0000000000c0';
set local app.cliente_id = '';

select ok(seg.eh_consultor(), 'Caio é consultor');
select results_eq(
    $$select distinct c.slug from cfg.cliente_tela t join cfg.cliente c on c.id = t.cliente_id
       order by 1$$,
    $$values ('padrao-lann-alfa'), ('padrao-lann-beta')$$,
    'consultor lê os painéis ligados dos dois clientes da carteira, e só deles');
select results_eq(
    $$select count(distinct cliente_id)::int from cfg.fonte_carga$$,
    $$values (2)$$,
    'e as fontes dos dois');
select results_eq(
    $$select count(distinct cliente_id)::int from mart.carga_fonte$$,
    $$values (2)$$,
    'e as cargas dos dois');
select is_empty(
    $$select 1 from cfg.fonte_carga where cliente_id = 'c1000000-0000-4000-8000-0000000000cc'$$,
    'nada do cliente fora da carteira');
select throws_ok(
    $$insert into cfg.cliente_tela (cliente_id, codigo_tela, ligada)
      values ('c1000000-0000-4000-8000-00000000000a', 'FIN-04', true)$$,
    '42501', null, 'consultor não liga painel');

-- ---------------------------------------------------------------------------
-- quem não é consultor não ganha nada
-- ---------------------------------------------------------------------------
-- Ana (cliente principal, só em A): continua vendo só o único cliente dela
set local app.usuario_id = 'd1000000-0000-4000-8000-00000000000a';
select ok(not seg.eh_consultor(), 'Ana não é consultora');
select results_eq(
    $$select distinct c.slug from cfg.fonte_carga f join cfg.cliente c on c.id = f.cliente_id$$,
    $$values ('padrao-lann-alfa')$$,
    'cliente principal continua só com o próprio cliente');

-- consultor desativado perde a leitura
reset role;
update seg.usuario set ativo = false where id = 'd1000000-0000-4000-8000-0000000000c0';
set local role api_site;
set local app.usuario_id = 'd1000000-0000-4000-8000-0000000000c0';
select is_empty($$select 1 from cfg.cliente_tela$$, 'consultor inativo não lê nada');

select * from finish();
rollback;

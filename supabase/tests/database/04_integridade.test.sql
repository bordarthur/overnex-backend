-- Integridade entre clientes garantida por FKs compostas, e regras de mart.carga_fonte.
begin;

create extension if not exists pgtap with schema extensions;

select plan(7);

select throws_ok(
    $$insert into cfg.mapa_empresa (cliente_id, empresa_origem, empresa_id)
      values ('c1000000-0000-4000-8000-00000000000a', 'INTRUSA', 'e1000000-0000-4000-8000-0000000000b1')$$,
    '23503', null,
    'cfg.mapa_empresa recusa empresa de outro cliente');

select lives_ok(
    $$insert into cfg.mapa_empresa (cliente_id, empresa_origem, empresa_id)
      values ('c1000000-0000-4000-8000-00000000000a', 'ALFA IND 2', 'e1000000-0000-4000-8000-0000000000a1')$$,
    'cfg.mapa_empresa aceita empresa do mesmo cliente');

select throws_ok(
    $$insert into cfg.mapa_empresa (cliente_id, empresa_origem, empresa_id)
      values ('c1000000-0000-4000-8000-00000000000a', 'ALFA IND', 'e1000000-0000-4000-8000-0000000000a2')$$,
    '23505', null,
    'cfg.mapa_empresa é único por (cliente_id, empresa_origem)');

select throws_ok(
    $$insert into seg.usuario_empresa (usuario_id, cliente_id, empresa_id)
      values ('d1000000-0000-4000-8000-00000000000a', 'c1000000-0000-4000-8000-00000000000a',
              'e1000000-0000-4000-8000-0000000000b1')$$,
    '23503', null,
    'seg.usuario_empresa recusa empresa de outro cliente');

select throws_ok(
    $$insert into mart.carga_fonte (carga_id, fonte_id, cliente_id, data_snapshot)
      select (select min(id) from mart.carga), f.id, 'c1000000-0000-4000-8000-00000000000b', current_date
        from cfg.fonte_carga f where f.cliente_id = 'c1000000-0000-4000-8000-00000000000a'$$,
    '23503', null,
    'mart.carga_fonte recusa cliente diferente do da fonte');

-- data_snapshot: opcional enquanto roda ou em erro, obrigatório em 'ok'
select lives_ok(
    $$insert into mart.carga_fonte (carga_id, fonte_id, cliente_id, status)
      select (select min(id) from mart.carga), f.id, f.cliente_id, 'rodando'
        from cfg.fonte_carga f where f.cliente_id = 'c1000000-0000-4000-8000-00000000000a'$$,
    'mart.carga_fonte aceita linha rodando sem data_snapshot');

select throws_ok(
    $$insert into mart.carga_fonte (carga_id, fonte_id, cliente_id, status)
      select (select min(id) from mart.carga), f.id, f.cliente_id, 'ok'
        from cfg.fonte_carga f where f.cliente_id = 'c1000000-0000-4000-8000-00000000000a'$$,
    '23514', null,
    'mart.carga_fonte recusa linha ok sem data_snapshot');

select * from finish();
rollback;

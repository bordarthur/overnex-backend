-- Base fictícia "Padrão LANN" — só para desenvolvimento local e testes.
--
-- Cliente A: Padrão LANN Alfa (2 empresas) — usuária Ana
-- Cliente B: Padrão LANN Beta (1 empresa)  — usuário Bruno
--
-- IDs fixos para os testes (api/tests e supabase/tests) se referirem a eles:
--   cliente A  c1000000-0000-4000-8000-00000000000a
--   cliente B  c1000000-0000-4000-8000-00000000000b
--   empresa A1 e1000000-0000-4000-8000-0000000000a1
--   empresa A2 e1000000-0000-4000-8000-0000000000a2
--   empresa B1 e1000000-0000-4000-8000-0000000000b1
--   usuário A  d1000000-0000-4000-8000-00000000000a  (oid Entra 0d000000-0000-4000-8000-00000000000a)
--   usuário B  d1000000-0000-4000-8000-00000000000b  (oid Entra 0d000000-0000-4000-8000-00000000000b)

-- Senhas locais dos roles. Em produção a senha é definida manualmente.
alter role api_site with login password 'api_site_local';
alter role carga    with login password 'carga_local';

insert into cfg.cliente (id, nome, slug, logo_url, cor_destaque) values
    ('c1000000-0000-4000-8000-00000000000a', 'Padrão LANN Alfa', 'padrao-lann-alfa', null, '#1F6FEB'),
    ('c1000000-0000-4000-8000-00000000000b', 'Padrão LANN Beta', 'padrao-lann-beta', null, '#D97706');

insert into cfg.empresa (id, cliente_id, nome) values
    ('e1000000-0000-4000-8000-0000000000a1', 'c1000000-0000-4000-8000-00000000000a', 'Alfa Indústria'),
    ('e1000000-0000-4000-8000-0000000000a2', 'c1000000-0000-4000-8000-00000000000a', 'Alfa Comércio'),
    ('e1000000-0000-4000-8000-0000000000b1', 'c1000000-0000-4000-8000-00000000000b', 'Beta Serviços');

insert into cfg.painel (cliente_id, versao, status, config_json) values
    ('c1000000-0000-4000-8000-00000000000a', 1, 'publicado', '{"telas": ["VG-01", "FIN-01"]}'),
    ('c1000000-0000-4000-8000-00000000000b', 1, 'publicado', '{"telas": ["VG-01", "FIN-01"]}');

insert into cfg.perfil_acesso (id, cliente_id, nome) values
    ('f1000000-0000-4000-8000-00000000000a', 'c1000000-0000-4000-8000-00000000000a', 'Financeiro'),
    ('f1000000-0000-4000-8000-00000000000b', 'c1000000-0000-4000-8000-00000000000b', 'Financeiro');

insert into cfg.perfil_tela (perfil_id, codigo_tela, ve_dado_nominal) values
    ('f1000000-0000-4000-8000-00000000000a', 'FIN-01', false),
    ('f1000000-0000-4000-8000-00000000000b', 'FIN-01', false),
    ('f1000000-0000-4000-8000-00000000000a', 'VG-01', false),
    ('f1000000-0000-4000-8000-00000000000b', 'VG-01', false);

insert into cfg.parametro (empresa_id, chave, valor, vigente_de) values
    ('e1000000-0000-4000-8000-0000000000a1', 'meta_margem', '0.18', '2025-01-01'),
    ('e1000000-0000-4000-8000-0000000000a2', 'meta_margem', '0.15', '2025-01-01'),
    ('e1000000-0000-4000-8000-0000000000b1', 'meta_margem', '0.22', '2025-01-01');

insert into seg.usuario (id, object_id_entra, email, tipo) values
    ('d1000000-0000-4000-8000-00000000000a', '0d000000-0000-4000-8000-00000000000a', 'ana@alfa.example', 'cliente'),
    ('d1000000-0000-4000-8000-00000000000b', '0d000000-0000-4000-8000-00000000000b', 'bruno@beta.example', 'cliente');

insert into seg.usuario_cliente (usuario_id, cliente_id, papel) values
    ('d1000000-0000-4000-8000-00000000000a', 'c1000000-0000-4000-8000-00000000000a', 'gestor'),
    ('d1000000-0000-4000-8000-00000000000b', 'c1000000-0000-4000-8000-00000000000b', 'leitor');

insert into seg.usuario_perfil (usuario_id, cliente_id, perfil_id) values
    ('d1000000-0000-4000-8000-00000000000a', 'c1000000-0000-4000-8000-00000000000a', 'f1000000-0000-4000-8000-00000000000a'),
    ('d1000000-0000-4000-8000-00000000000b', 'c1000000-0000-4000-8000-00000000000b', 'f1000000-0000-4000-8000-00000000000b');

insert into seg.usuario_empresa (usuario_id, cliente_id, empresa_id) values
    ('d1000000-0000-4000-8000-00000000000a', 'c1000000-0000-4000-8000-00000000000a', 'e1000000-0000-4000-8000-0000000000a1'),
    ('d1000000-0000-4000-8000-00000000000a', 'c1000000-0000-4000-8000-00000000000a', 'e1000000-0000-4000-8000-0000000000a2'),
    ('d1000000-0000-4000-8000-00000000000b', 'c1000000-0000-4000-8000-00000000000b', 'e1000000-0000-4000-8000-0000000000b1');

insert into cfg.regra_lancamento
    (empresa_id, fonte, campo, operador, valor, acao, id_descritivo_novo, prioridade, vigente_de, motivo, alterado_por)
values
    ('e1000000-0000-4000-8000-0000000000a1', 'lancamento', 'conta', '=', '3.1.01', 'reclassificar', 4, 10,
     '2025-10-01', 'Exemplo do seed', 'd1000000-0000-4000-8000-00000000000a'),
    ('e1000000-0000-4000-8000-0000000000b1', 'lancamento', 'conta', '=', '9.9.99', 'excluir', null, 10,
     '2025-10-01', 'Exemplo do seed', 'd1000000-0000-4000-8000-00000000000b');

-- Fontes e mapa de empresas da carga
insert into cfg.fonte_carga (cliente_id, tipo, caminho_bucket) values
    ('c1000000-0000-4000-8000-00000000000a', 'lancamento',
     'dataflow/dbo_f_lancamentos_agrupados_padrao_alfa/dbo_f_lancamentos_agrupados_padrao_alfa.parquet'),
    ('c1000000-0000-4000-8000-00000000000b', 'lancamento',
     'dataflow/dbo_f_lancamentos_agrupados_padrao_beta/dbo_f_lancamentos_agrupados_padrao_beta.parquet');

insert into cfg.mapa_empresa (cliente_id, empresa_origem, empresa_id) values
    ('c1000000-0000-4000-8000-00000000000a', 'ALFA IND', 'e1000000-0000-4000-8000-0000000000a1'),
    ('c1000000-0000-4000-8000-00000000000a', 'ALFA COM', 'e1000000-0000-4000-8000-0000000000a2'),
    ('c1000000-0000-4000-8000-00000000000b', 'BETA SERV', 'e1000000-0000-4000-8000-0000000000b1');

-- 12 meses (out/2025 a set/2026) × 4 descritivos × 3 empresas
insert into mart.lancamento (empresa_id, mes, id_descritivo, valor)
select e.id,
       m.mes::date,
       d.id_descritivo,
       round(d.base * e.fator * (1 + 0.02 * extract(month from m.mes)), 2)
  from (values
          ('e1000000-0000-4000-8000-0000000000a1'::uuid, 1.00),
          ('e1000000-0000-4000-8000-0000000000a2'::uuid, 0.45),
          ('e1000000-0000-4000-8000-0000000000b1'::uuid, 0.70)
       ) e (id, fator)
 cross join generate_series('2025-10-01'::date, '2026-09-01'::date, interval '1 month') m (mes)
 cross join (values
          (1,  500000.00),  -- receita bruta
          (2,  -60000.00),  -- deduções
          (3, -210000.00),  -- custos
          (4, -120000.00)   -- despesas operacionais
       ) d (id_descritivo, base);

-- Uma execução da carga com uma fonte 'ok' por cliente (datas diferentes de propósito)
with c as (
    insert into mart.carga (inicio, fim, status, mensagem)
    values ('2026-10-12 09:05-03', '2026-10-12 09:07-03', 'ok', 'seed')
    returning id
)
insert into mart.carga_fonte
    (carga_id, fonte_id, cliente_id, data_snapshot, status, linhas_lidas, linhas_gravadas, inicio, fim)
select c.id, f.id, f.cliente_id,
       case f.cliente_id
           when 'c1000000-0000-4000-8000-00000000000a' then date '2026-10-12'
           else date '2026-10-11'
       end,
       'ok', l.linhas, l.linhas, '2026-10-12 09:05-03', '2026-10-12 09:06-03'
  from c
 cross join cfg.fonte_carga f
 cross join lateral (
    select count(*)::int as linhas
      from mart.lancamento ml
      join cfg.empresa e on e.id = ml.empresa_id
     where e.cliente_id = f.cliente_id
 ) l;

-- Tabela de dados (tipo 'tabela'): mart.f_estoque_nilo_maia com linhas fictícias dos dois
-- clientes. Fontes inativas: a carga diária local não as roda; os testes ativam quando precisam.
insert into cfg.fonte_carga
    (cliente_id, tipo, caminho_bucket, ativo, tabela_destino, coluna_empresa, contem_dado_nominal)
values
    ('c1000000-0000-4000-8000-00000000000a', 'tabela',
     'dataflow/dbo_f_estoque_padrao_alfa/dbo_f_estoque_padrao_alfa.parquet',
     false, 'f_estoque_nilo_maia', 'EMPRESA', false),
    ('c1000000-0000-4000-8000-00000000000b', 'tabela',
     'dataflow/dbo_f_estoque_padrao_beta/dbo_f_estoque_padrao_beta.parquet',
     false, 'f_estoque_nilo_maia', 'EMPRESA', false);

insert into cfg.perfil_tabela (perfil_id, tabela, ve_dado_nominal) values
    ('f1000000-0000-4000-8000-00000000000a', 'f_estoque_nilo_maia', false),
    ('f1000000-0000-4000-8000-00000000000b', 'f_estoque_nilo_maia', false);

insert into mart.f_estoque_nilo_maia
    (_linha, _cliente_id, _empresa_id, "en_filial", "en_codigoproduto", "en_saldoestoque",
     "EMPRESA", "QTD_RESERVADA", "QTD_BLOQUEADA", "QTD_PENDENTE", "ESTOQUE_DISPONIVEL",
     "CUSTOFIN", "_data_snapshot")
values
    (1, 'c1000000-0000-4000-8000-00000000000a', 'e1000000-0000-4000-8000-0000000000a1',
     '1', 'P001', 120, 'ALFA IND', 10, 0, 5, 105, 12.50, '2026-10-12'),
    (2, 'c1000000-0000-4000-8000-00000000000a', 'e1000000-0000-4000-8000-0000000000a1',
     '1', 'P002', 40, 'ALFA IND', 0, 2, 0, 38, 7.25, '2026-10-12'),
    (3, 'c1000000-0000-4000-8000-00000000000a', 'e1000000-0000-4000-8000-0000000000a2',
     '2', 'P001', 15, 'ALFA COM', 0, 0, 0, 15, 12.50, '2026-10-12'),
    (1, 'c1000000-0000-4000-8000-00000000000b', 'e1000000-0000-4000-8000-0000000000b1',
     '1', 'B900', 300, 'BETA SERV', 50, 0, 0, 250, 3.10, '2026-10-11');

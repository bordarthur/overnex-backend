-- PJ Cortes — cliente, empresa, mapa de empresas, fontes das 25 tabelas, janela de datas e
-- parâmetros (produção).
--
-- NÃO é migration: rode uma vez no SQL Editor do Supabase, DEPOIS de aplicar as migrations
-- 11 (colunas_modelo) e 12 (tabelas da PJ) com `supabase db push`.
--
-- Empresa: uma só ("PJ Cortes"). O DW escreve o nome de vários jeitos ('1 PJ Cortes',
-- 'PJ CORTES', ' PJ Cortes'…) e todos apontam para ela. '2 PJ Cortes' só aparece no estoque
-- (en_filial 2): se for outra empresa, crie a empresa e troque o mapa dessa linha.
--
-- Vendas: f_vendas_pj (desde 10/03/2025) e f_vendas_agile_pj_cortes (sistema antigo, até
-- 08/03/2025) se somam na Visão geral. As duas usam as colunas da PJ (colunas_modelo).
-- Custos: CMV aberto por grupo do produto (it_grupo).
--
-- Janela de 3 meses nas tabelas de movimento grandes; o resumo mensal da carga sai do
-- arquivo inteiro, antes da janela (por isso a Agile fica vazia no mart e o histórico dela
-- continua na Visão geral). Metas, orçamentos, compras e dimensões entram inteiras, e os
-- títulos a receber e a pagar (f_receitas_pj, f_despesas_pj) também: a seção Financeiro
-- precisa dos títulos antigos ainda em aberto e de 12 meses de histórico.

begin;

-- 1. cliente e empresa
insert into cfg.cliente (nome, slug)
values ('PJ Cortes', 'pj-cortes')
on conflict (slug) do nothing;

insert into cfg.empresa (cliente_id, nome)
select c.id, 'PJ Cortes'
  from cfg.cliente c
 where c.slug = 'pj-cortes'
on conflict do nothing;

-- 2. mapa: valores da coluna de empresa no DW (inventário de 21/10/2026)
insert into cfg.mapa_empresa (cliente_id, empresa_origem, empresa_id)
select e.cliente_id, v.origem, e.id
  from cfg.empresa e
  join cfg.cliente c on c.id = e.cliente_id and c.slug = 'pj-cortes'
 cross join (values ('1 PJ Cortes'), ('2 PJ Cortes'), (' PJ Cortes'), ('PJ Cortes'), ('PJ CORTES'))
       v (origem)
 where e.nome = 'PJ Cortes'
on conflict do nothing;

-- 3. fontes (ativas)
-- coluna_empresa null: a tabela não tem empresa (ou tem linhas sem ela, como os lançamentos)
-- e todo usuário do cliente vê todas as linhas.
-- nominal: só perfis com ve_dado_nominal em cfg.perfil_tabela leem.
insert into cfg.fonte_carga
    (cliente_id, tipo, caminho_bucket, tabela_destino, coluna_empresa, contem_dado_nominal,
     colunas_modelo, ativo)
select c.id,
       'tabela',
       'dataflow/dbo_' || v.tabela || '/dbo_' || v.tabela || '.parquet',
       v.tabela,
       v.coluna_empresa,
       v.nominal,
       m.colunas_modelo::jsonb,
       true
  from cfg.cliente c
 cross join (values
        -- tabela                            coluna_empresa  nominal  colunas_modelo ('vendas' = as da PJ, abaixo)
        ('d_banco_pj',                       null::text,     true,    '{}'),  -- agência e conta
        ('d_bitem_pj',                       'EMPRESA',      false,   '{}'),
        ('d_cliente_pj',                     'EMPRESA',      true,    '{}'),  -- razão social, endereço
        ('d_custo_pj',                       null,           false,   '{}'),
        ('d_fornecedor_pj',                  'EMPRESA',      true,    '{}'),
        ('d_vendedor_pj',                    'EMPRESA',      true,    '{}'),  -- nome, CPF
        ('f_645_pj',                         null,           true,    '{}'),  -- Historico
        ('f_bordero_pj',                     'Empresa',      true,    '{}'),  -- Descricao
        ('f_compras_pj',                     'EMPRESA',      true,    '{}'),  -- en_observacao
        ('f_despesas_pj',                    'EMPRESA',      true,    '{}'),  -- en_observacao
        ('f_devolucao_pj',                   'EMPRESA',      true,    '{}'),  -- en_codigocliente
        ('f_devolucao_pj_agile',             null,           true,    '{}'),  -- en_codcli
        ('f_estoque_pj',                     'EMPRESA',      false,   '{}'),
        ('f_folha_pj_cortes',                'EMPRESA',      true,    '{}'),  -- NOME
        ('f_lancamentos_pj_cortes',          null,           true,    '{}'),  -- 2 linhas sem Empresa
        ('f_meta_compras_pj_cortes',         'EMPRESA',      false,   '{}'),
        ('f_metas_comercial_pj',             'EMPRESA',      false,   '{"data": "Data"}'),
        ('f_orcamento_comercial_pj_cortes',  'EMPRESA',      false,   '{}'),
        ('f_orcamento_pj',                   'EMPRESA',      false,   '{}'),
        ('f_orcamento_ticket_pj_cortes',     'Empresa',      false,   '{}'),
        ('f_pagas_pj',                       'EMPRESA',      true,    '{}'),  -- en_observacao
        ('f_recebidas_pj',                   'EMPRESA',      true,    '{}'),  -- en_observacao
        ('f_receitas_pj',                    'EMPRESA',      true,    '{}'),  -- en_codigocliente
        ('f_vendas_agile_pj_cortes',         'EMPRESA',      true,    'vendas'),
        ('f_vendas_pj',                      'EMPRESA',      true,    'vendas')   -- en_codigocliente
       ) v (tabela, coluna_empresa, nominal, colunas_modelo_texto)
 cross join lateral (
        select case v.colunas_modelo_texto
                   when 'vendas' then
                       '{"data": "en_dataemissao", "valor": "en_valortotal",'
                       ' "custo": "en_custototal", "pedido": "en_numpedido",'
                       ' "cliente": "en_codigocliente", "produto": "en_codigoproduto",'
                       ' "abertura": "it_grupo"}'
                   else v.colunas_modelo_texto
               end as colunas_modelo
       ) m (colunas_modelo)
 where c.slug = 'pj-cortes'
on conflict (cliente_id, tipo, caminho_bucket) do nothing;

-- 4. janela de 3 meses nas tabelas de movimento grandes
update cfg.fonte_carga f
   set coluna_data = v.coluna_data,
       meses_retidos = 3
  from cfg.cliente c,
       (values
        -- tabela                       coluna de data
        ('f_vendas_pj',                 'en_dataemissao'),
        ('f_vendas_agile_pj_cortes',    'en_dataemissao'),   -- só tem até 03/2025: fica vazia
        ('f_recebidas_pj',              'en_datarecebimento'),
        ('f_pagas_pj',                  'en_datpag'),
        ('f_bordero_pj',                'Data'),
        ('f_645_pj',                    'Data'),
        ('f_folha_pj_cortes',           'Data')
       ) v (tabela, coluna_data)
 where c.id = f.cliente_id
   and c.slug = 'pj-cortes'
   and f.tabela_destino = v.tabela;

-- 5. nome da abertura do CMV nos textos da seção Custos
insert into cfg.parametro_cliente (cliente_id, chave, valor, vigente_de)
select c.id, 'custos.abertura',
       '{"nome": "grupo", "descricao": "grupo de produto", "plural": "grupos"}'::jsonb,
       date '2025-01-01'
  from cfg.cliente c
 where c.slug = 'pj-cortes'
on conflict do nothing;

-- conferência
do $$
declare
    fontes int;
    janelas int;
    modelos int;
begin
    select count(*),
           count(*) filter (where f.meses_retidos is not null),
           count(*) filter (where f.colunas_modelo <> '{}'::jsonb)
      into fontes, janelas, modelos
      from cfg.fonte_carga f
      join cfg.cliente c on c.id = f.cliente_id
     where c.slug = 'pj-cortes' and f.tipo = 'tabela';
    if fontes <> 25 or janelas <> 7 or modelos <> 3 then
        raise exception 'esperava 25 fontes, 7 com janela e 3 com colunas_modelo; veio %, % e %',
            fontes, janelas, modelos;
    end if;
    if (select count(*) from cfg.mapa_empresa m
          join cfg.cliente c on c.id = m.cliente_id
         where c.slug = 'pj-cortes') <> 5 then
        raise exception 'esperava 5 valores no mapa de empresas da PJ Cortes';
    end if;
end
$$;

commit;

-- Para ver:
--   select f.tabela_destino, f.coluna_empresa, f.coluna_data, f.meses_retidos, f.colunas_modelo
--     from cfg.fonte_carga f join cfg.cliente c on c.id = f.cliente_id
--    where c.slug = 'pj-cortes' order by 1;

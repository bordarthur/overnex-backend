-- Distribuidora Overnex — a empresa de demonstração, com dados fictícios (produção).
--
-- NÃO é migration: rode no SQL Editor do Supabase. Pode rodar mais de uma vez (troca o que
-- já existe pelo que está aqui). Não depende de migration nova.
--
-- ORDEM:
-- 1. o cliente criado pela Gestão, com o slug 'distribuidora-overnex' (este script não o
--    cria: se ele não existir, para com erro);
-- 2. este script;
-- 3. o workflow diario.yml (Run workflow): o job `demo` gera os arquivos e a carga os leva
--    ao mart. Depois, conferir mart.carga_fonte (consultas no fim deste script);
-- 4. na Gestão: ligar os painéis, ajustar a matriz e cadastrar o usuário da demonstração
--    (perfil cliente principal, só neste cliente: o admin vê no seletor os nomes dos
--    clientes reais).
--
-- Os dados vêm do gerador jobs/demo (todo dia, junto com o snapshot), não de um banco do
-- cliente. Usam as mesmas tabelas do mart da PJ Cortes: a carga troca só as linhas deste
-- cliente, e o RLS separa os dois. O que está aqui precisa bater com jobs/demo/demo/modelo.py
-- (valores de EMPRESA, contas do ERP e saldo inicial) e com jobs/demo/demo/tabelas.py (as 12
-- tabelas): o teste jobs/demo/tests/test_cadastro_sql.py confere.
--
-- Empresas: Overnex Fortaleza (matriz) e Overnex Recife (filial). O fluxo de caixa não tem
-- coluna de empresa (como na PJ): o saldo inicial é do cliente inteiro.

begin;

do $$
begin
    if not exists (select 1 from cfg.cliente where slug = 'distribuidora-overnex') then
        raise exception 'cliente distribuidora-overnex não existe: crie pela Gestão antes';
    end if;
end
$$;

-- 1. empresas (o trigger da migration 30 as põe nos vínculos que o cliente já tem)
insert into cfg.empresa (cliente_id, nome)
select c.id, v.nome
  from cfg.cliente c
 cross join (values ('Overnex Fortaleza'), ('Overnex Recife')) v (nome)
 where c.slug = 'distribuidora-overnex'
on conflict do nothing;

-- 2. mapa: valor da coluna EMPRESA nos arquivos → empresa
insert into cfg.mapa_empresa (cliente_id, empresa_origem, empresa_id)
select e.cliente_id, v.origem, e.id
  from cfg.empresa e
  join cfg.cliente c on c.id = e.cliente_id and c.slug = 'distribuidora-overnex'
  join (values ('OVERNEX FORTALEZA', 'Overnex Fortaleza'),
               ('OVERNEX RECIFE',    'Overnex Recife')) v (origem, nome)
    on v.nome = e.nome
on conflict (cliente_id, empresa_origem) do update set empresa_id = excluded.empresa_id;

-- 3. fontes: os arquivos do gerador, em demo/distribuidora-overnex/<tabela>.parquet
-- coluna_empresa null: a tabela vale para o cliente inteiro (produtos, fornecedores, fluxo).
-- vendas: as colunas da PJ, mais vendedor, linha e quantidade (o faturado por vendedor do
-- resumo mensal, de que a rampa precisa). Janela de 13 meses só nas vendas, como na PJ: o
-- resumo mensal sai do arquivo inteiro.
insert into cfg.fonte_carga
    (cliente_id, tipo, caminho_bucket, tabela_destino, coluna_empresa, contem_dado_nominal,
     colunas_modelo, coluna_data, meses_retidos, ativo)
select c.id,
       'tabela',
       'demo/distribuidora-overnex/' || v.tabela || '.parquet',
       v.tabela,
       v.coluna_empresa,
       v.nominal,
       v.colunas_modelo::jsonb,
       v.coluna_data,
       v.meses,
       true
  from cfg.cliente c
 cross join (values
        -- tabela                       coluna_empresa  nominal  colunas_modelo  janela
        ('d_bitem_pj',                  null::text,     false,   '{}',      null::text,       null::smallint),
        ('d_cliente_pj',                'EMPRESA',      true,    '{}',      null,             null),
        ('d_fornecedor_pj',             null,           true,    '{}',      null,             null),
        ('d_vendedor_pj',               'EMPRESA',      true,    '{}',      null,             null),
        ('f_despesas_pj',               'EMPRESA',      true,    '{}',      null,             null),
        ('f_folha_pj_cortes',           'EMPRESA',      true,    '{}',      null,             null),
        ('f_fluxo_pj',                  null,           true,    '{}',      null,             null),
        ('f_lancamentos_agrupados_pj',  'Empresa',      false,   '{}',      null,             null),
        ('f_metas_comercial_pj',        'EMPRESA',      false,   '{"data": "Data"}', null,    null),
        ('f_orcamento_pj',              'EMPRESA',      false,   '{}',      null,             null),
        ('f_receitas_pj',               'EMPRESA',      true,    '{}',      null,             null),
        ('f_vendas_pj',                 'EMPRESA',      true,
            '{"data": "en_dataemissao", "valor": "en_valortotal", "custo": "en_custototal",'
            ' "pedido": "en_numpedido", "cliente": "en_codigocliente",'
            ' "produto": "en_codigoproduto", "abertura": "it_grupo",'
            ' "vendedor": "en_codigovendedor", "linha": "it_grupo",'
            ' "quantidade": "en_quantidade"}',
                                                                        'en_dataemissao', 13)
       ) v (tabela, coluna_empresa, nominal, colunas_modelo, coluna_data, meses)
 where c.slug = 'distribuidora-overnex'
on conflict (cliente_id, tipo, caminho_bucket) do update
   set tabela_destino      = excluded.tabela_destino,
       coluna_empresa      = excluded.coluna_empresa,
       contem_dado_nominal = excluded.contem_dado_nominal,
       colunas_modelo      = excluded.colunas_modelo,
       coluna_data         = excluded.coluna_data,
       meses_retidos       = excluded.meses_retidos,
       ativo               = true;

-- 4. parâmetros: nome da abertura do CMV na seção Custos e caixa mínimo da projeção
insert into cfg.parametro_cliente (cliente_id, chave, valor, vigente_de)
select c.id, v.chave, v.valor::jsonb, date '2024-01-01'
  from cfg.cliente c
 cross join (values
        ('custos.abertura',
         '{"nome": "linha", "descricao": "linha de produto", "plural": "linhas"}'),
        ('financeiro.caixa_minimo', '{"valor": 1500000}')
       ) v (chave, valor)
 where c.slug = 'distribuidora-overnex'
on conflict (cliente_id, chave, vigente_de) do update set valor = excluded.valor;

-- 5. de-para das contas do ERP fictício: grupo da DRE (pelo plano da LANN) e grupo de
-- pagamento do cartão "Contas a pagar lançadas". A DRE da demo vem dos lançamentos
-- agrupados; o de-para serve aos grupos de pagamento e às provisões do fluxo de caixa.
delete from cfg.conta_dre d
 using cfg.cliente c
 where c.id = d.cliente_id and c.slug = 'distribuidora-overnex';

insert into cfg.conta_dre (cliente_id, conta, id_descritivo, descricao, fora_da_dre, grupo_pagamento)
select c.id, v.conta, v.id_descritivo, v.descricao, v.fora_da_dre, v.grupo
  from cfg.cliente c
 cross join (values
        -- conta     Id_Descritivo  descrição                            fora_da_dre                 grupo
        ('201001', '120.01.01', 'Compra de mercadorias para revenda', 'o CMV entra pelas vendas', 'fornecedores'),
        ('201002', '120.04.04', 'Frete sobre compras',                null::text,                 'fornecedores'),
        ('301001', '140.01.01', 'Salários',                           null,                       'custos'),
        ('301002', '140.01.05', 'FGTS',                               null,                       'custos'),
        ('301003', '140.01.04', 'INSS',                               null,                       'custos'),
        ('301004', '140.01.07', 'Vale-alimentação e transporte',      null,                       'custos'),
        ('301005', '140.01.16', 'Plano de saúde',                     null,                       'custos'),
        ('301006', '140.01.02', '13º salário',                        null,                       'custos'),
        ('301007', '140.01.03', 'Férias',                             null,                       'custos'),
        ('301008', '140.01.11', 'Rescisões',                          null,                       'custos'),
        ('401001', '110.03.04', 'ICMS',                               null,                       'custos'),
        ('401002', '110.03.01', 'PIS',                                null,                       'custos'),
        ('401003', '110.03.02', 'COFINS',                             null,                       'custos'),
        ('401004', '200.01.01', 'IRPJ e CSLL',                        null,                       'custos'),
        ('501001', '140.02.01', 'Aluguel',                            null,                       'custos'),
        ('501002', '140.02.02', 'Energia elétrica',                   null,                       'custos'),
        ('501003', '140.02.03', 'Água e esgoto',                      null,                       'custos'),
        ('501004', '140.02.04', 'Telefone e internet',                null,                       'custos'),
        ('501005', '140.02.09', 'Sistema ERP',                        null,                       'custos'),
        ('501006', '140.06.02', 'Contabilidade',                      null,                       'custos'),
        ('501007', '140.02.11', 'Segurança e monitoramento',          null,                       'custos'),
        ('501008', '140.02.07', 'Material de escritório',             null,                       'custos'),
        ('501009', '140.02.18', 'Seguros',                            null,                       'custos'),
        ('501010', '140.04.04', 'Publicidade',                        null,                       'custos'),
        ('502001', '130.01.01', 'Comissões de vendedores',            null,                       'custos'),
        ('502002', '130.03.01', 'Combustível da frota',               null,                       'custos'),
        ('502003', '130.03.04', 'Fretes de entrega terceirizados',    null,                       'custos'),
        ('502004', '130.02.01', 'Tarifas de boleto',                  null,                       'custos'),
        ('502005', '130.03.03', 'Manutenção da frota',                null,                       'custos'),
        ('601001', '170.01.05', 'Juros de empréstimos',               null,                       'divida'),
        ('601002', '400.02.01', 'Amortização de empréstimos',         null,                       'divida'),
        ('601003', '170.01.02', 'Tarifas bancárias',                  null,                       'custos'),
        ('602001', '410.01.02', 'Empréstimos tomados',                null,                       null::text),
        ('701001', '420.02.02', 'Veículos',                           null,                       'capex'),
        ('701002', '420.02.01', 'Máquinas e equipamentos',            null,                       'capex'),
        ('801001', '210.01.01', 'Retirada de sócios',                 null,                       null)
       ) v (conta, id_descritivo, descricao, fora_da_dre, grupo)
 where c.slug = 'distribuidora-overnex';

-- 6. saldo de caixa no início do fluxo (o gerador parte do mesmo número)
insert into cfg.saldo_inicial (cliente_id, data, saldo, origem)
select c.id, date '2024-11-01', 1900000.00, 'Demonstração: saldo inicial do gerador jobs/demo'
  from cfg.cliente c
 where c.slug = 'distribuidora-overnex'
on conflict (cliente_id, data) do update set saldo = excluded.saldo, origem = excluded.origem;

-- conferência
do $$
declare
    cliente uuid := (select id from cfg.cliente where slug = 'distribuidora-overnex');
begin
    if (select count(*) from cfg.empresa where cliente_id = cliente) <> 2 then
        raise exception 'esperava 2 empresas na Distribuidora Overnex';
    end if;
    if (select count(*) from cfg.mapa_empresa where cliente_id = cliente) <> 2 then
        raise exception 'esperava 2 valores no mapa de empresas';
    end if;
    if (select count(*) from cfg.fonte_carga
         where cliente_id = cliente and tipo = 'tabela' and ativo) <> 12 then
        raise exception 'esperava 12 fontes ativas';
    end if;
    if (select count(*) from cfg.conta_dre where cliente_id = cliente) <> 36 then
        raise exception 'esperava 36 contas no de-para';
    end if;
end
$$;

commit;

-- Para ver, depois da carga:
--   select f.tabela_destino, cf.data_snapshot, cf.status, cf.linhas_gravadas, cf.mensagem
--     from cfg.fonte_carga f
--     join cfg.cliente c on c.id = f.cliente_id and c.slug = 'distribuidora-overnex'
--     left join lateral (select * from mart.carga_fonte x where x.fonte_id = f.id
--                         order by x.id desc limit 1) cf on true
--    order by 1;
--
-- Para parar de atualizar a demonstração (a carga deixa de ler os arquivos; as linhas que já
-- estão no mart ficam, e para tirá-las é preciso apagar as linhas do cliente, `_cliente_id`,
-- em cada uma das 12 tabelas):
--   update cfg.fonte_carga f set ativo = false
--     from cfg.cliente c where c.id = f.cliente_id and c.slug = 'distribuidora-overnex';

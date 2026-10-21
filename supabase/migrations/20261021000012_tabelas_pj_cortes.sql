-- Migration 12 — tabelas de dados da PJ Cortes.
--
-- Mesmo padrão da migration 5: as colunas do Parquet (nomes preservados; texto → text,
-- double → numeric, date → date) mais _linha, _cliente_id e _empresa_id, com o preparo de
-- mart.preparar_tabela_dados(). Geradas a partir do inventário do bucket de 21/10/2026
-- (python -m carga.inspecionar).
--
-- Só adições.

-- dataflow/dbo_d_banco_pj/dbo_d_banco_pj.parquet
create table mart.d_banco_pj (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "CODBANCO"       bigint,
    "NOME"           text,
    "NUMBANCO"       bigint,
    "AGENCIA"        text,
    "CONTA"          text,
    "_data_snapshot" date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('d_banco_pj');

-- dataflow/dbo_d_bitem_pj/dbo_d_bitem_pj.parquet
create table mart.d_bitem_pj (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "en_codigoproduto" text,
    "en_nome"          text,
    "en_grupo"         text,
    "en_secao"         text,
    "en_valorunitario" numeric,
    "en_custounitario" numeric,
    "EMPRESA"          text,
    "id_secao"         text,
    "id_secao_cmv"     text,
    "_data_snapshot"   date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('d_bitem_pj');

-- dataflow/dbo_d_cliente_pj/dbo_d_cliente_pj.parquet
create table mart.d_cliente_pj (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "en_codigocliente" text,
    "en_razaosocial"   text,
    "en_fantasia"      text,
    "en_endereco"      text,
    "en_bairro"        text,
    "en_cidade"        text,
    "en_UF"            text,
    "en_cep"           text,
    "en_ramo"          text,
    "en_limitecredito" numeric,
    "ven_codigo"       text,
    "Grupo"            text,
    "en_canal"         text,
    "EMPRESA"          text,
    "_data_snapshot"   date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('d_cliente_pj');

-- dataflow/dbo_d_custo_pj/dbo_d_custo_pj.parquet
create table mart.d_custo_pj (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "CODCONTA"       text,
    "CONTA"          text,
    "GRUPOCONTA"     text,
    "_data_snapshot" date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('d_custo_pj');

-- dataflow/dbo_d_fornecedor_pj/dbo_d_fornecedor_pj.parquet
create table mart.d_fornecedor_pj (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "en_codigofornecedor" text,
    "en_razaosocial"      text,
    "en_fantasia"         text,
    "en_endereco"         text,
    "en_cidade"           text,
    "en_uf"               text,
    "en_canal"            text,
    "EMPRESA"             text,
    "_data_snapshot"      date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('d_fornecedor_pj');

-- dataflow/dbo_d_vendedor_pj/dbo_d_vendedor_pj.parquet
create table mart.d_vendedor_pj (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "en_codigovendedor" text,
    "en_nome"           text,
    "en_cpf"            text,
    "en_dtentrada"      text,
    "en_dtsaida"        text,
    "Canal"             text,
    "sp_supervisor"     text,
    "EMPRESA"           text,
    "_data_snapshot"    date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('d_vendedor_pj');

-- dataflow/dbo_f_645_pj/dbo_f_645_pj.parquet
create table mart.f_645_pj (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "Rotina"          text,
    "NumTrans"        text,
    "Data"            text,
    "DtConcil"        text,
    "CaixaBancoMoeda" text,
    "VlSaida"         numeric,
    "VlEntrada"       numeric,
    "Historico"       text,
    "Historico2"      text,
    "NumDoc"          text,
    "CodBanco"        text,
    "CodMoeda"        text,
    "CodContaCred"    text,
    "CodContaDeb"     text,
    "Tipo"            text,
    "Valor"           numeric,
    "NumLanc"         text,
    "DtCompensacao"   text,
    "CodFunc"         text,
    "Banco"           text,
    "Bordero"         text,
    "_data_snapshot"  date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_645_pj');

-- dataflow/dbo_f_bordero_pj/dbo_f_bordero_pj.parquet
create table mart.f_bordero_pj (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "Empresa"        text,
    "Data"           text,
    "Conta"          text,
    "Descricao"      text,
    "Tipo"           text,
    "Rotina"         text,
    "CodigoBanco"    text,
    "Transacao"      text,
    "Bordero"        text,
    "VPAGO"          numeric,
    "DTCONCIL"       text,
    "_data_snapshot" date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_bordero_pj');

-- dataflow/dbo_f_compras_pj/dbo_f_compras_pj.parquet
create table mart.f_compras_pj (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "en_numrecebimento"     text,
    "en_numpedido"          text,
    "en_codigoplanocliente" text,
    "en_codigofornecedor"   text,
    "en_dataemissao"        text,
    "en_datavencimento"     text,
    "en_datapagamento"      text,
    "en_observacao"         text,
    "nf_codigoproduto"      text,
    "nf_quantidade"         numeric,
    "nf_custounitario"      numeric,
    "EMPRESA"               text,
    "_data_snapshot"        date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_compras_pj');

-- dataflow/dbo_f_despesas_pj/dbo_f_despesas_pj.parquet
create table mart.f_despesas_pj (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "en_numdoc"             text,
    "en_numlancamento"      text,
    "en_datemi"             text,
    "en_datven"             text,
    "en_datpag"             text,
    "en_codigoplanocliente" text,
    "en_nomeplanocliente"   text,
    "en_observacao"         text,
    "en_codigofornecedor"   text,
    "en_valor"              numeric,
    "en_valorpago"          numeric,
    "en_valorjuro"          numeric,
    "EMPRESA"               text,
    "_data_snapshot"        date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_despesas_pj');

-- dataflow/dbo_f_devolucao_pj/dbo_f_devolucao_pj.parquet
create table mart.f_devolucao_pj (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "en_dataemissao"   text,
    "en_codigoproduto" text,
    "en_codigocliente" text,
    "en_quantidade"    numeric,
    "en_valortotal"    numeric,
    "en_motivo"        text,
    "en_numdocumento"  text,
    "en_numpedido"     text,
    "EMPRESA"          text,
    "_data_snapshot"   date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_devolucao_pj');

-- dataflow/dbo_f_devolucao_pj_agile/dbo_f_devolucao_pj_agile.parquet
create table mart.f_devolucao_pj_agile (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "en_datemi"      text,
    "en_codcli"      bigint,
    "en_quanti"      numeric,
    "en_precus"      numeric,
    "en_prcmv"       numeric,
    "en_valdes"      numeric,
    "en_preven"      numeric,
    "en_prmedio"     numeric,
    "en_VALTOT"      numeric,
    "Id_Descritivo"  text,
    "en_codigo"      text,
    "_data_snapshot" date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_devolucao_pj_agile');

-- dataflow/dbo_f_estoque_pj/dbo_f_estoque_pj.parquet
create table mart.f_estoque_pj (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "en_filial"        text,
    "en_codigoproduto" text,
    "en_saldoestoque"  numeric,
    "EMPRESA"          text,
    "_data_snapshot"   date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_estoque_pj');

-- dataflow/dbo_f_folha_pj_cortes/dbo_f_folha_pj_cortes.parquet
create table mart.f_folha_pj_cortes (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "NOME"           text,
    "CARGO"          text,
    "SETOR"          text,
    "EMPRESA"        text,
    "REGIME"         text,
    "CONTRATO"       text,
    "Id_Funcao"      text,
    "Data"           text,
    "Lancamento"     text,
    "Valor"          numeric,
    "Descritivo"     text,
    "Id_Descritivo"  text,
    "_data_snapshot" date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_folha_pj_cortes');

-- dataflow/dbo_f_lancamentos_pj_cortes/dbo_f_lancamentos_pj_cortes.parquet
create table mart.f_lancamentos_pj_cortes (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "Empresa"                     text,
    "Data_de_Competencia"         text,
    "Valor_Regime_de_Competencia" numeric,
    "Tipo"                        text,
    "Id_Descritivo"               text,
    "Descricao"                   text,
    "_data_snapshot"              date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_lancamentos_pj_cortes');

-- dataflow/dbo_f_meta_compras_pj_cortes/dbo_f_meta_compras_pj_cortes.parquet
create table mart.f_meta_compras_pj_cortes (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "Data"           text,
    "Valor"          numeric,
    "EMPRESA"        text,
    "_data_snapshot" date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_meta_compras_pj_cortes');

-- dataflow/dbo_f_metas_comercial_pj/dbo_f_metas_comercial_pj.parquet
create table mart.f_metas_comercial_pj (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "Data"           text,
    "Volume"         numeric,
    "Valor"          numeric,
    "Mix"            numeric,
    "NDeClientes"    numeric,
    "en_vendedor"    text,
    "EMPRESA"        text,
    "_data_snapshot" date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_metas_comercial_pj');

-- dataflow/dbo_f_orcamento_comercial_pj_cortes/dbo_f_orcamento_comercial_pj_cortes.parquet
create table mart.f_orcamento_comercial_pj_cortes (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "Data"           text,
    "Volume"         numeric,
    "Valor"          numeric,
    "Cenario"        text,
    "Id_Descritivo"  text,
    "EMPRESA"        text,
    "_data_snapshot" date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_orcamento_comercial_pj_cortes');

-- dataflow/dbo_f_orcamento_pj/dbo_f_orcamento_pj.parquet
create table mart.f_orcamento_pj (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "Data"           text,
    "Valor"          numeric,
    "Id_Descritivo"  text,
    "Cenario"        text,
    "EMPRESA"        text,
    "_data_snapshot" date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_orcamento_pj');

-- dataflow/dbo_f_orcamento_ticket_pj_cortes/dbo_f_orcamento_ticket_pj_cortes.parquet
create table mart.f_orcamento_ticket_pj_cortes (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "Nome_da_Origem" text,
    "Empresa"        text,
    "Grupo"          text,
    "Data"           text,
    "Valor"          numeric,
    "Cenario"        text,
    "Personalizar"   text,
    "_data_snapshot" date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_orcamento_ticket_pj_cortes');

-- dataflow/dbo_f_pagas_pj/dbo_f_pagas_pj.parquet
create table mart.f_pagas_pj (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "en_datpag"             text,
    "en_codfor"             text,
    "en_valor"              numeric,
    "en_valpag"             numeric,
    "en_codigoplanocliente" text,
    "en_observacao"         text,
    "en_valorjuro"          numeric,
    "EMPRESA"               text,
    "_data_snapshot"        date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_pagas_pj');

-- dataflow/dbo_f_recebidas_pj/dbo_f_recebidas_pj.parquet
create table mart.f_recebidas_pj (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "en_numdocumento"       text,
    "en_datarecebimento"    text,
    "en_codigocliente"      text,
    "en_valor"              numeric,
    "en_valorpago"          numeric,
    "en_codigoplanocliente" text,
    "en_observacao"         text,
    "en_valorjuro"          numeric,
    "en_codigovendedor"     text,
    "EMPRESA"               text,
    "_data_snapshot"        date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_recebidas_pj');

-- dataflow/dbo_f_receitas_pj/dbo_f_receitas_pj.parquet
create table mart.f_receitas_pj (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "en_numdoc"          text,
    "en_dataemissao"     text,
    "en_datavencimento"  text,
    "en_datapagamento"   text,
    "codigoplanocliente" text,
    "en_observacao"      text,
    "en_codigovendedor"  text,
    "en_codigocliente"   text,
    "en_valor"           numeric,
    "en_valorpago"       numeric,
    "en_valorjuro"       numeric,
    "EMPRESA"            text,
    "_data_snapshot"     date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_receitas_pj');

-- dataflow/dbo_f_vendas_agile_pj_cortes/dbo_f_vendas_agile_pj_cortes.parquet
create table mart.f_vendas_agile_pj_cortes (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "en_dataemissao"    text,
    "en_numpedido"      text,
    "en_codigoproduto"  text,
    "en_codigocliente"  text,
    "en_quantidade"     numeric,
    "en_valortotal"     numeric,
    "en_custototal"     numeric,
    "en_codigovendedor" text,
    "it_grupo"          text,
    "EMPRESA"           text,
    "_data_snapshot"    date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_vendas_agile_pj_cortes');

-- dataflow/dbo_f_vendas_pj/dbo_f_vendas_pj.parquet
create table mart.f_vendas_pj (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "en_dataemissao"    text,
    "en_numpedido"      text,
    "en_codigoproduto"  text,
    "en_codigocliente"  text,
    "en_quantidade"     numeric,
    "en_valortotal"     numeric,
    "en_custototal"     numeric,
    "en_codigovendedor" text,
    "it_grupo"          text,
    "EMPRESA"           text,
    "_data_snapshot"    date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_vendas_pj');

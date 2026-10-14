-- Migration 5 — tabelas de dados: cada Parquet do bucket ganha uma tabela no mart.
--
-- Por quê: o front precisa ler, além dos lançamentos, as demais tabelas que o snapshot
-- copia do DW. Cada tabela de dados tem AS MESMAS colunas do Parquet (mesmos nomes,
-- entre aspas; texto → text, double → numeric, date → date) e três colunas de sistema:
--
--   _linha       posição da linha no arquivo (ordem estável e paginação na API)
--   _cliente_id  dono dos dados (RLS por cliente)
--   _empresa_id  empresa, quando a fonte tem coluna de empresa (RLS por empresa);
--                null = dado do cliente inteiro, visível a todos os usuários dele
--
-- A carga (tipo 'tabela' em cfg.fonte_carga) substitui as linhas do cliente a cada
-- execução. A API só expõe tabelas registradas em cfg.fonte_carga para o cliente do
-- usuário e liberadas a um perfil dele em cfg.perfil_tabela.
--
-- Só adições: nada existente é apagado ou renomeado. Coluna nova no DW → nova
-- migration com `alter table mart.<tabela> add column "<Nome>" <tipo>`; até lá a carga
-- ignora a coluna e avisa.

-- ---------------------------------------------------------------------------
-- cfg.fonte_carga: tipo 'tabela'
-- ---------------------------------------------------------------------------

alter table cfg.fonte_carga
    drop constraint fonte_carga_tipo_check,
    add constraint fonte_carga_tipo_check check (tipo in ('lancamento', 'tabela'));

alter table cfg.fonte_carga
    add column tabela_destino      text check (tabela_destino ~ '^[a-z][a-z0-9_]{0,62}$'),
    add column coluna_empresa      text,
    add column contem_dado_nominal boolean not null default true;

comment on column cfg.fonte_carga.tabela_destino is
    'Tipo tabela: nome da tabela em mart que recebe o Parquet.';
comment on column cfg.fonte_carga.coluna_empresa is
    'Tipo tabela: coluna do arquivo com a empresa (traduzida por cfg.mapa_empresa). '
    'Null = dados do cliente inteiro, sem separação por empresa.';
comment on column cfg.fonte_carga.contem_dado_nominal is
    'Tipo tabela: a API só entrega a quem tem ve_dado_nominal em cfg.perfil_tabela.';

alter table cfg.fonte_carga
    add constraint fonte_carga_tipo_tabela_check
        check ((tipo = 'tabela') = (tabela_destino is not null)),
    add constraint fonte_carga_coluna_empresa_check
        check (coluna_empresa is null or tipo = 'tabela'),
    add constraint fonte_carga_tabela_unica unique (cliente_id, tabela_destino);

-- ---------------------------------------------------------------------------
-- cfg.perfil_tabela: quais tabelas de dados cada perfil lê
-- ---------------------------------------------------------------------------

create table cfg.perfil_tabela (
    perfil_id       uuid not null references cfg.perfil_acesso (id) on delete cascade,
    tabela          text not null check (tabela ~ '^[a-z][a-z0-9_]{0,62}$'),
    ve_dado_nominal boolean not null default false,
    primary key (perfil_id, tabela)
);

alter table cfg.perfil_tabela enable row level security;
alter table cfg.perfil_tabela force row level security;

revoke all on cfg.perfil_tabela from public;
grant select, insert, update, delete on cfg.perfil_tabela to api_site;

create policy api_site_select on cfg.perfil_tabela
    for select to api_site
    using (exists (
        select 1
          from cfg.perfil_acesso p
         where p.id = perfil_tabela.perfil_id
           and p.cliente_id in (select seg.clientes_do_usuario())
    ));

-- ---------------------------------------------------------------------------
-- Preparo padrão de toda tabela de dados (RLS, políticas, grants e índice)
-- ---------------------------------------------------------------------------

create function mart.preparar_tabela_dados(p_tabela text)
returns void
language plpgsql
set search_path = ''
as $$
begin
    execute format('alter table mart.%I enable row level security', p_tabela);
    execute format('alter table mart.%I force row level security', p_tabela);
    execute format('create index on mart.%I (_empresa_id, _linha)', p_tabela);

    execute format('revoke all on mart.%I from public', p_tabela);
    execute format('grant select on mart.%I to api_site', p_tabela);
    execute format('grant select, insert, update, delete on mart.%I to carga', p_tabela);

    execute format(
        'create policy api_site_select on mart.%I for select to api_site '
        'using (_cliente_id in (select seg.clientes_do_usuario()) '
        'and (_empresa_id is null or _empresa_id in (select seg.empresas_do_usuario())))',
        p_tabela);
    execute format(
        'create policy carga_all on mart.%I for all to carga using (true) with check (true)',
        p_tabela);
end
$$;

comment on function mart.preparar_tabela_dados(text) is
    'Aplica RLS, políticas, grants e índice padrão a uma tabela de dados do mart. '
    'Chamar logo depois do create table, na mesma migration.';

revoke all on function mart.preparar_tabela_dados(text) from public;

-- ---------------------------------------------------------------------------
-- Tabelas de dados — Nilo Maia (geradas a partir do inventário do bucket de 14/10/2026)
-- ---------------------------------------------------------------------------

-- dataflow/dbo_f_645_nilo_maia/dbo_f_645_nilo_maia.parquet
create table mart.f_645_nilo_maia (
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
select mart.preparar_tabela_dados('f_645_nilo_maia');

-- dataflow/dbo_f_balanco_nilo_maia/dbo_f_balanco_nilo_maia.parquet
create table mart.f_balanco_nilo_maia (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "CODPLANOCONTA"  text,
    "CODREDUZIDO_PC" text,
    "CODCONTA_PC"    text,
    "NOME_CONTA"     text,
    "NATUREZA"       text,
    "MES"            numeric,
    "MESANO"         text,
    "SALDOINICIAL"   numeric,
    "VALORDEBITO"    numeric,
    "VALORCREDITO"   numeric,
    "SALDOFINAL"     numeric,
    "_data_snapshot" date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_balanco_nilo_maia');

-- dataflow/dbo_f_compras_nilo_maia/dbo_f_compras_nilo_maia.parquet
create table mart.f_compras_nilo_maia (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "RECNUM"         text,
    "NUMNOTA"        text,
    "en_custo"       text,
    "en_codfor"      text,
    "en_datemi2"     text,
    "en_datemi"      text,
    "en_datven"      text,
    "en_datpag"      text,
    "en_historico"   text,
    "DUPLIC"         text,
    "en_valor"       numeric,
    "en_valpag"      numeric,
    "en_codban"      text,
    "NOMEFUNC"       text,
    "DESCONTOFIN"    numeric,
    "_data_snapshot" date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_compras_nilo_maia');

-- dataflow/dbo_f_contas_a_pagar_nilo_maia/dbo_f_contas_a_pagar_nilo_maia.parquet
create table mart.f_contas_a_pagar_nilo_maia (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "VALOR"          numeric,
    "CODCONTA"       text,
    "DTVENC"         text,
    "_data_snapshot" date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_contas_a_pagar_nilo_maia');

-- dataflow/dbo_f_contas_a_receber_nilo_maia/dbo_f_contas_a_receber_nilo_maia.parquet
create table mart.f_contas_a_receber_nilo_maia (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "VALOR"          numeric,
    "DTVENC"         text,
    "_data_snapshot" date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_contas_a_receber_nilo_maia');

-- dataflow/dbo_f_estoque_nilo_maia/dbo_f_estoque_nilo_maia.parquet
create table mart.f_estoque_nilo_maia (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "en_filial"          text,
    "en_codigoproduto"   text,
    "en_saldoestoque"    numeric,
    "EMPRESA"            text,
    "QTD_RESERVADA"      numeric,
    "QTD_BLOQUEADA"      numeric,
    "QTD_PENDENTE"       numeric,
    "ESTOQUE_DISPONIVEL" numeric,
    "CUSTOFIN"           numeric,
    "_data_snapshot"     date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_estoque_nilo_maia');

-- dataflow/dbo_f_inadimplencia_nilo_maia/dbo_f_inadimplencia_nilo_maia.parquet
create table mart.f_inadimplencia_nilo_maia (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "en_codigocliente"  text,
    "en_dataemissao"    text,
    "en_datavencimento" text,
    "en_datapagamento"  text,
    "en_codcob"         text,
    "en_numdocumento"   text,
    "en_parcela"        text,
    "en_valor"          numeric,
    "en_valorpago"      numeric,
    "CODFILIAL"         text,
    "STATUS"            text,
    "en_vended"         text,
    "DTCANCEL"          text,
    "EMPRESA"           text,
    "_data_snapshot"    date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_inadimplencia_nilo_maia');

-- dataflow/dbo_f_lancamentos_agrupados_nilo_maia/dbo_f_lancamentos_agrupados_nilo_maia.parquet
create table mart.f_lancamentos_agrupados_nilo_maia (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "EMPRESA"          text,
    "DATA_COMPETENCIA" text,
    "DATA_CAIXA"       text,
    "DATA_VENCIMENTO"  text,
    "CODIGO_CONTA"     text,
    "CONTA"            text,
    "GRUPO_CONTA"      text,
    "CODIGO_GRUPO"     text,
    "VALOR_REALIZADO"  numeric,
    "VALOR_PREVISTO"   numeric,
    "TIPO"             text,
    "ORIGEM"           text,
    "DESCRICAO"        text,
    "_data_snapshot"   date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_lancamentos_agrupados_nilo_maia');

-- dataflow/dbo_f_metas_nilo_maia/dbo_f_metas_nilo_maia.parquet
create table mart.f_metas_nilo_maia (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "Competencia"    text,
    "Valor"          numeric,
    "Empresa"        text,
    "_data_snapshot" date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_metas_nilo_maia');

-- dataflow/dbo_f_movimentacao_nilo_maia/dbo_f_movimentacao_nilo_maia.parquet
create table mart.f_movimentacao_nilo_maia (
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
select mart.preparar_tabela_dados('f_movimentacao_nilo_maia');

-- dataflow/dbo_f_orcamento_nilo_maia/dbo_f_orcamento_nilo_maia.parquet
create table mart.f_orcamento_nilo_maia (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "Data"           text,
    "Valor"          numeric,
    "Id_Descritivo"  text,
    "Cenario"        text,
    "_data_snapshot" date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_orcamento_nilo_maia');

-- dataflow/dbo_f_pagas_nilo_maia/dbo_f_pagas_nilo_maia.parquet
create table mart.f_pagas_nilo_maia (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "en_datapagamento"      text,
    "en_codigofornecedor"   text,
    "en_valor"              numeric,
    "en_valorpago"          numeric,
    "en_codigoplanocliente" text,
    "en_observacao"         text,
    "en_valorjuro"          numeric,
    "EMPRESA"               text,
    "_data_snapshot"        date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_pagas_nilo_maia');

-- dataflow/dbo_f_pedidos_nilo_maia/dbo_f_pedidos_nilo_maia.parquet
create table mart.f_pedidos_nilo_maia (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "VALOR"          numeric,
    "DTVENC"         text,
    "_data_snapshot" date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_pedidos_nilo_maia');

-- dataflow/dbo_f_recebidas_nilo_maia/dbo_f_recebidas_nilo_maia.parquet
create table mart.f_recebidas_nilo_maia (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "en_numdocumento"       text,
    "en_dataemissao"        text,
    "en_datarecebimento"    text,
    "en_datavencimento"     text,
    "en_codigocliente"      text,
    "en_valor"              numeric,
    "en_valorpago"          numeric,
    "en_codigoplanocliente" text,
    "en_observacao"         text,
    "en_valorjuro"          text,
    "en_codigovendedor"     text,
    "EMPRESA"               text,
    "_data_snapshot"        date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_recebidas_nilo_maia');

-- dataflow/dbo_f_receitas_nilo_maia/dbo_f_receitas_nilo_maia.parquet
create table mart.f_receitas_nilo_maia (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "en_codcli"            text,
    "en_datemi"            text,
    "en_datven"            text,
    "en_datpag"            text,
    "en_codcob"            text,
    "en_numdoc"            text,
    "en_parc"              text,
    "en_valor"             numeric,
    "en_valpag"            numeric,
    "CODFILIAL"            text,
    "STATUS"               text,
    "en_vended"            text,
    "DTCANCEL"             text,
    "VLRTOTDESPESASEJUROS" numeric,
    "_data_snapshot"       date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_receitas_nilo_maia');

-- dataflow/dbo_f_saldos_bancarios_nilo_maia/dbo_f_saldos_bancarios_nilo_maia.parquet
create table mart.f_saldos_bancarios_nilo_maia (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "CODBANCO"       text,
    "CODCOB"         text,
    "DATA"           text,
    "SALDO"          numeric,
    "_data_snapshot" date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_saldos_bancarios_nilo_maia');

-- dataflow/dbo_f_transf_65apli_nilo_maia/dbo_f_transf_65apli_nilo_maia.parquet
create table mart.f_transf_65apli_nilo_maia (
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
select mart.preparar_tabela_dados('f_transf_65apli_nilo_maia');

-- dataflow/dbo_f_transf_65dni_nilo_maia/dbo_f_transf_65dni_nilo_maia.parquet
create table mart.f_transf_65dni_nilo_maia (
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
select mart.preparar_tabela_dados('f_transf_65dni_nilo_maia');

-- dataflow/dbo_f_transf_65sent_nilo_maia/dbo_f_transf_65sent_nilo_maia.parquet
create table mart.f_transf_65sent_nilo_maia (
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
select mart.preparar_tabela_dados('f_transf_65sent_nilo_maia');

-- dataflow/dbo_f_transf_65vale_nilo_maia/dbo_f_transf_65vale_nilo_maia.parquet
create table mart.f_transf_65vale_nilo_maia (
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
select mart.preparar_tabela_dados('f_transf_65vale_nilo_maia');

-- dataflow/dbo_f_vendas_nilo_maia/dbo_f_vendas_nilo_maia.parquet
create table mart.f_vendas_nilo_maia (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "en_vendedor"    text,
    "en_datemicao"   text,
    "en_datemi"      text,
    "en_codcli"      text,
    "en_ramo"        text,
    "en_codigo"      text,
    "en_nome"        text,
    "en_unidade"     text,
    "en_VALTOT"      numeric,
    "TOTQTUNITCX"    numeric,
    "en_precus"      numeric,
    "en_quanti"      numeric,
    "en_numdoc"      text,
    "_data_snapshot" date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_vendas_nilo_maia');


-- Migration 13 — datas das tabelas de fatos da PJ Cortes como date.
--
-- Por quê: as 24 colunas de data vinham como texto ('01/08/2026 00:00:00'), e a API
-- convertia cada valor (regex + to_date) em toda leitura. Com os títulos inteiros, sem
-- janela, a seção Financeiro levava ~18 s só nisso. Agora a carga converte uma vez
-- (jobs/carga/carga/tabela.py: coluna date no mart com texto no arquivo) e a API lê a
-- coluna direto (TabelaDoCliente.datas). De quebra, date ocupa 4 bytes, e o texto, 20.
--
-- ORDEM EM PRODUÇÃO: carga e API com o código novo ANTES desta migration. A carga antiga
-- gravaria o texto numa coluna date pelo DateStyle do servidor (MDY: 01/08 viraria 8 de
-- janeiro, e 31/08 falharia) e a API antiga quebraria com left(date).
--
-- Conversão dos dados que já estão no mart: os 10 primeiros caracteres nos formatos da
-- carga (AAAA-MM-DD, DD/MM/AAAA, AAAA/MM/DD); outro formato vira null, como a API já
-- tratava. Conferido em 21/10/2026: as 24 colunas estão em DD/MM/AAAA, sem exceção.
--
-- d_vendedor_pj (en_dtentrada, en_dtsaida) continua em texto: nenhuma tela usa.

create function pg_temp.texto_para_data(valor text) returns date
language sql immutable as $$
    select case
        when left(trim(valor), 10) ~ '^\d{4}-\d{2}-\d{2}$'
            then to_date(left(trim(valor), 10), 'YYYY-MM-DD')
        when left(trim(valor), 10) ~ '^\d{2}/\d{2}/\d{4}$'
            then to_date(left(trim(valor), 10), 'DD/MM/YYYY')
        when left(trim(valor), 10) ~ '^\d{4}/\d{2}/\d{2}$'
            then to_date(left(trim(valor), 10), 'YYYY/MM/DD')
    end
$$;

alter table mart.f_645_pj
    alter column "Data" type date using pg_temp.texto_para_data("Data");
alter table mart.f_bordero_pj
    alter column "Data" type date using pg_temp.texto_para_data("Data");
alter table mart.f_compras_pj
    alter column "en_dataemissao"    type date using pg_temp.texto_para_data("en_dataemissao"),
    alter column "en_datavencimento" type date using pg_temp.texto_para_data("en_datavencimento"),
    alter column "en_datapagamento"  type date using pg_temp.texto_para_data("en_datapagamento");
alter table mart.f_despesas_pj
    alter column "en_datemi" type date using pg_temp.texto_para_data("en_datemi"),
    alter column "en_datven" type date using pg_temp.texto_para_data("en_datven"),
    alter column "en_datpag" type date using pg_temp.texto_para_data("en_datpag");
alter table mart.f_devolucao_pj
    alter column "en_dataemissao" type date using pg_temp.texto_para_data("en_dataemissao");
alter table mart.f_devolucao_pj_agile
    alter column "en_datemi" type date using pg_temp.texto_para_data("en_datemi");
alter table mart.f_folha_pj_cortes
    alter column "Data" type date using pg_temp.texto_para_data("Data");
alter table mart.f_lancamentos_pj_cortes
    alter column "Data_de_Competencia" type date
        using pg_temp.texto_para_data("Data_de_Competencia");
alter table mart.f_meta_compras_pj_cortes
    alter column "Data" type date using pg_temp.texto_para_data("Data");
alter table mart.f_metas_comercial_pj
    alter column "Data" type date using pg_temp.texto_para_data("Data");
alter table mart.f_orcamento_comercial_pj_cortes
    alter column "Data" type date using pg_temp.texto_para_data("Data");
alter table mart.f_orcamento_pj
    alter column "Data" type date using pg_temp.texto_para_data("Data");
alter table mart.f_orcamento_ticket_pj_cortes
    alter column "Data" type date using pg_temp.texto_para_data("Data");
alter table mart.f_pagas_pj
    alter column "en_datpag" type date using pg_temp.texto_para_data("en_datpag");
alter table mart.f_recebidas_pj
    alter column "en_datarecebimento" type date
        using pg_temp.texto_para_data("en_datarecebimento");
alter table mart.f_receitas_pj
    alter column "en_dataemissao"    type date using pg_temp.texto_para_data("en_dataemissao"),
    alter column "en_datavencimento" type date using pg_temp.texto_para_data("en_datavencimento"),
    alter column "en_datapagamento"  type date using pg_temp.texto_para_data("en_datapagamento");
alter table mart.f_vendas_agile_pj_cortes
    alter column "en_dataemissao" type date using pg_temp.texto_para_data("en_dataemissao");
alter table mart.f_vendas_pj
    alter column "en_dataemissao" type date using pg_temp.texto_para_data("en_dataemissao");

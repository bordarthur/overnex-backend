-- Migration 18 — lançamentos agrupados da DRE da PJ Cortes.
--
-- Por quê: a DRE da PJ no Power BI vem de uma view que junta lançamentos, despesas pelo
-- de-para, folha gerencial, vendas, CMV por seção e devoluções, com as exclusões da PJ
-- (jobs/snapshot/azure/f_lancamentos_agrupados_pj.sql). A view passa a rodar no Azure SQL
-- e chega pelo snapshot como as outras tabelas; a API monta a DRE dela, com o plano de
-- contas (cfg.plano_conta), em vez de refazer a regra da PJ em código.
--
-- Colunas da view: Empresa, Data (date), Id_Descritivo e Valor (com o sinal do resultado:
-- receita positiva, custo e despesa negativos). Mesmo padrão da migration 12.
--
-- Só adição.

-- dataflow/dbo_f_lancamentos_agrupados_pj/dbo_f_lancamentos_agrupados_pj.parquet
create table mart.f_lancamentos_agrupados_pj (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "Empresa"        text,
    "Data"           date,
    "Id_Descritivo"  text,
    "Valor"          numeric,
    "_data_snapshot" date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_lancamentos_agrupados_pj');

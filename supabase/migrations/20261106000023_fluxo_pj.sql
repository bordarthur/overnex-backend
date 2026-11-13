-- Migration 23 — fluxo de caixa realizado da PJ Cortes.
--
-- Por quê: o fluxo de caixa da PJ no Power BI vem da view f_Fluxo_PJ (645 do Winthor mais
-- o borderô aberto por título, pela data de compensação, com a conta do Winthor levada ao
-- plano padrão pelo de-para d_De_Para_PJ_Cortes). A view roda no Azure SQL e chega pelo
-- snapshot como as outras tabelas; a API monta a DFC dela com o plano de contas
-- (cfg.plano_conta: competência CA e CO/CA, classe FCO/FCI/FCF) e o saldo inicial
-- (cfg.saldo_inicial, migration 22).
--
-- Colunas: as da view, com os mesmos nomes. Data vira date (a carga converte o texto
-- 'dd/mm/aaaa' do Azure); valores em numeric, com o sinal do caixa (entrada positiva,
-- saída negativa). Origem diz o bloco da view ('Bloco 1' lançamentos diretos, 'Bloco 2'
-- borderô aberto, 'Bloco 3' 775 sem borderô, 'Bloco 6' 1209, 'Bloco 7' 618/619,
-- 'Bloco 9' 1803). Id_Descritivo vazio = conta sem de-para (fica fora das linhas da DFC,
-- mas entra no saldo). Descricao e Descricao2 são o histórico da 645 (pode ter nome de
-- pessoa). Mesmo padrão da migration 12.
--
-- Só adição.

-- dataflow/dbo_f_fluxo_pj/dbo_f_fluxo_pj.parquet
create table mart.f_fluxo_pj (
    _linha      bigint not null,
    _cliente_id uuid   not null references cfg.cliente (id),
    _empresa_id uuid,
    "Empresas"          text,
    "Transacao"         text,
    "Banco"             text,
    "Data"              date,
    "ValorEntrada"      numeric,
    "ValorSaida"        numeric,
    "Valor"             numeric,
    "CodContaCred"      text,
    "CodContaDeb"       text,
    "Descricao"         text,
    "Descricao2"        text,
    "Tipo"              text,
    "Rotina"            text,
    "Moeda"             text,
    "Estorno"           text,
    "Origem"            text,
    "CodConta"          text,
    "ColunaVerificacao" text,
    "Id_LANN"           text,
    "Id_Descritivo"     text,
    "_data_snapshot"    date,
    primary key (_cliente_id, _linha),
    foreign key (_empresa_id, _cliente_id) references cfg.empresa (id, cliente_id)
);
select mart.preparar_tabela_dados('f_fluxo_pj');

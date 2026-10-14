-- Migration 6 — janela de datas nas fontes do tipo 'tabela'.
--
-- Por quê: as tabelas de movimento do DW (vendas, receitas, lançamentos…) somam
-- milhões de linhas e não cabem inteiras no banco. Com a janela, a carga só grava as
-- linhas dos últimos N meses da coluna de data escolhida.
--
-- coluna_data   coluna do arquivo com a data da linha (date, timestamp ou texto em
--               formato AAAA-MM-DD ou DD/MM/AAAA, com ou sem hora)
-- meses_retidos N: entram as linhas a partir do dia 1 do mês do snapshot menos N-1
--               (snapshot de 14/10 e N = 3 → a partir de 01/08). Linhas com a data
--               vazia entram. A referência é o _data_snapshot do arquivo, nunca "hoje".
--
-- Os dois vazios = a tabela entra inteira (como antes). Só adições.

alter table cfg.fonte_carga
    add column coluna_data   text,
    add column meses_retidos smallint check (meses_retidos > 0);

comment on column cfg.fonte_carga.coluna_data is
    'Tipo tabela: coluna do arquivo usada na janela de datas (com meses_retidos).';
comment on column cfg.fonte_carga.meses_retidos is
    'Tipo tabela: só entram linhas dos últimos N meses (contando o mês do snapshot).';

alter table cfg.fonte_carga
    add constraint fonte_carga_janela_completa
        check ((coluna_data is null) = (meses_retidos is null)),
    add constraint fonte_carga_janela_so_tabela
        check (coluna_data is null or tipo = 'tabela');

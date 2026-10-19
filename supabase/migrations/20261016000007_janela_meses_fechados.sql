-- Migration 7 — janela de datas só com meses fechados.
--
-- Por quê: em tabelas que o DW atualiza com atraso (ex.: vendas da Nilo Maia), o mês
-- do snapshot ainda não tem linhas, e a janela de 1 mês deixava a tabela vazia.
--
-- meses_fechados = true: a janela termina no último dia do mês ANTERIOR ao do snapshot.
--   snapshot de 16/10, meses_retidos = 1 → só setembro (01/09 a 30/09)
--   snapshot de 16/10, meses_retidos = 3 → julho, agosto e setembro
-- meses_fechados = false (padrão): como antes, do início da janela até o mês do snapshot.
--
-- Só adição.

alter table cfg.fonte_carga
    add column meses_fechados boolean not null default false;

comment on column cfg.fonte_carga.meses_fechados is
    'Tipo tabela: true = a janela de datas usa só meses fechados (termina no mês anterior ao do snapshot).';

alter table cfg.fonte_carga
    add constraint fonte_carga_meses_fechados_com_janela
        check (not meses_fechados or meses_retidos is not null);

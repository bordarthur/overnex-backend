-- Migration 4 — mart.carga_fonte.data_snapshot passa a aceitar null.
--
-- Por quê: a carga registra cada fonte como 'rodando' ANTES de baixar o arquivo,
-- e precisa registrar como 'erro' falhas em que a data do snapshot não existe ou
-- não é única (arquivo inexistente no bucket, chave errada, _data_snapshot
-- misturado ou ausente). Com NOT NULL, essas falhas não teriam como ser gravadas.
--
-- O que continua garantido: linha 'ok' sempre tem data_snapshot. A API lê o
-- "atualizado em" só de linhas 'ok', então não muda nada para ela.

alter table mart.carga_fonte
    alter column data_snapshot drop not null;

alter table mart.carga_fonte
    add constraint carga_fonte_ok_tem_data_snapshot
    check (status <> 'ok' or data_snapshot is not null);

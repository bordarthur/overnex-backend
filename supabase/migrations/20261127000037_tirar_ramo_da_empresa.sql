-- Migration 37 — o "Ramo" sai da empresa: cfg.cliente.segmento deixa de existir.
--
-- Por quê: o usuário decidiu tirar o "Ramo" (27/11/2026). Ele é o cfg.cliente.segmento, texto
-- livre da migration 30. Desde as migrations 35 e 36, setor e segmento são de cada unidade
-- (cfg.empresa), e o ramo do cliente ficou repetido. Ele também podia contradizer as
-- unidades: a Leão de Ouro estava com ramo "Distribuidora" e as seis unidades em Varejo.
-- Nenhuma tela usava o ramo para decidir nada, e o front (branch feat/negocios) já não manda
-- nem mostra o campo.
--
--   - cfg.cliente perde a coluna segmento (o check da migration 30 sai junto);
--   - o api_site fica com update só em nome. Desde a migration 31 ele tinha (nome,
--     segmento); o drop da coluna já leva o privilégio dela, e o revoke + grant deixa o
--     resultado explícito. O insert continua com o grant da tabela inteira (migration 2).
--
-- Ordem em produção: AO CONTRÁRIO das migrations 35 e 36. Primeiro publique a API nova
-- (que não lê nem grava cfg.cliente.segmento), depois aplique esta migration: a API de antes
-- dela lê a coluna e quebraria a Gestão (barra lateral, empresa aberta, criar e editar).

alter table cfg.cliente drop column segmento;

revoke update on cfg.cliente from api_site;
grant update (nome) on cfg.cliente to api_site;

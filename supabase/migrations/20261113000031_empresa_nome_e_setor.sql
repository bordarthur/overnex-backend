-- Migration 31 — o admin troca o nome e o setor da empresa pela Gestão.
--
-- Por quê: a migration 30 deixou o admin criar a empresa (cfg.cliente), mas não editar.
-- As empresas que já existiam ficaram sem setor, e corrigir um nome exigia SQL. A rota
-- PUT /api/admin/clientes/{slug} troca nome e setor.
--
-- Sem esta migration a rota não funciona: o api_site tem o grant de update desde a
-- migration 2, mas nenhuma política, então o RLS não acha linha nenhuma para trocar (a API
-- responde 500 e registra no log que falta esta migration).
--
--   - política de update para o admin (seg.eh_admin()), como as da migration 27;
--   - o api_site passa a ter update só em nome e segmento, e perde o delete: o slug não
--     muda (está nas URLs) e a empresa não se desativa nem se apaga pela API.
--
-- A rota de edição da API depende desta migration: aplique antes. O resto da API funciona
-- sem ela.

-- o grant da migration 2 era na tabela inteira (sem política, o RLS negava tudo)
revoke update, delete on cfg.cliente from api_site;
grant update (nome, segmento) on cfg.cliente to api_site;

create policy api_site_admin_update on cfg.cliente
    for update to api_site using ((select seg.eh_admin())) with check ((select seg.eh_admin()));

-- Migration 36 — o admin edita a unidade (cfg.empresa) pela Gestão.
--
-- Por quê: a aba Unidades da Gestão (migration 35) era só de leitura; o usuário quer
-- corrigir pela tela o nome, o setor, o segmento, o grupo e o endereço de cada unidade
-- (PUT /api/admin/clientes/{slug}/unidades/{id}). Unidade nova e apagar unidade continuam
-- por SQL: a unidade precisa estar ligada aos dados da carga (`_empresa_id` e
-- cfg.mapa_empresa). O cliente da unidade também não muda.
--
-- O api_site tem insert, update e delete em cfg desde a migration 2, mas nenhuma política
-- em cfg.empresa: o RLS negava tudo. Como a migration 31 fez com cfg.cliente:
--   - o api_site perde insert e delete em cfg.empresa e fica com update só nas colunas que a
--     tela edita (nome, setor, segmento, grupo, uf, cidade, bairro, endereco); id e
--     cliente_id não mudam;
--   - política de update só para o admin (seg.eh_admin()), como as da migration 27.
--
-- A rota de edição da API depende desta migration: aplique antes. O resto da API funciona
-- sem ela (sem a migration, a rota responde 500 e registra no log que ela falta).

revoke insert, update, delete on cfg.empresa from api_site;
grant update (nome, setor, segmento, grupo, uf, cidade, bairro, endereco) on cfg.empresa
    to api_site;

create policy api_site_admin_update on cfg.empresa
    for update to api_site using ((select seg.eh_admin())) with check ((select seg.eh_admin()));

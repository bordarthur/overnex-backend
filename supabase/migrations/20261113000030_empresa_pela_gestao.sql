-- Migration 30 — empresa nova pela Gestão, com o setor.
--
-- Por quê: para dar login a alguém de uma empresa que ainda não está no Overnex, o Admin
-- BORD precisava de um script SQL só para criar o cfg.cliente. Agora a Gestão cria a
-- empresa (nome, slug e setor) e já convida as pessoas; o resto do cadastro (CNPJs, fontes
-- da carga, de-para de contas) continua por script, porque depende do ERP de cada cliente.
--
--   - cfg.cliente.segmento: o setor da empresa (texto livre, opcional).
--   - o admin insere em cfg.cliente pela API (política seg.eh_admin()). Não troca nem apaga:
--     corrigir nome, slug ou setor continua por SQL. Os 3 perfis fixos nascem pelo trigger
--     da migration 25.
--   - cfg.empresa nova entra sozinha nos vínculos que já existem. Antes,
--     seg.usuario_empresa só recebia as empresas que existiam na hora do vínculo: quem era
--     convidado antes de os CNPJs serem cadastrados ficava sem empresa nenhuma (e sem tela
--     nenhuma) até alguém salvar a pessoa de novo na Gestão. O vínculo já levava todas as
--     empresas do cliente (seg.definir_vinculos, migration 27); o trigger mantém isso.
--     Não mexe nos vínculos de empresas que já existem.
--
-- A API com o código de 13/11/2026 depende desta migration (lê cfg.cliente.segmento):
-- aplique antes. A API antiga continua funcionando depois dela.

alter table cfg.cliente
    add column segmento text check (segmento is null or btrim(segmento) <> '');

comment on column cfg.cliente.segmento is
    'Setor da empresa (ex.: Distribuição de alimentos), texto livre; null = não informado.';

-- o grant de insert já existe (migration 2); faltava a política
create policy api_site_admin_insert on cfg.cliente
    for insert to api_site with check ((select seg.eh_admin()));

-- Empresa nova: entra nos vínculos que o cliente já tem.
create function cfg.empresa_nos_vinculos()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    insert into seg.usuario_empresa (usuario_id, cliente_id, empresa_id)
    select uc.usuario_id, uc.cliente_id, new.id
      from seg.usuario_cliente uc
     where uc.cliente_id = new.cliente_id
    on conflict do nothing;
    return new;
end
$$;

comment on function cfg.empresa_nos_vinculos() is
    'Põe a empresa nova em todos os vínculos que o cliente dela já tem.';

revoke all on function cfg.empresa_nos_vinculos() from public;

create trigger empresa_nos_vinculos
    after insert on cfg.empresa
    for each row execute function cfg.empresa_nos_vinculos();

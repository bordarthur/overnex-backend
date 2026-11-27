-- Migration 33 — Gestão do consultor: leitura do controle das empresas da carteira.
--
-- Por quê: o Consultor por carteira passa a entrar na Gestão, só para ler, com as empresas
-- dos vínculos dele na barra lateral e, de cada uma, as abas Implantação e Prontidão dos
-- dados. A barra lateral mostra o estado das camadas de todas as empresas de uma vez; o
-- api_site só lia painéis ligados, fontes e cargas do cliente escolhido (ou de todos, para o
-- admin, migration 27). Aqui o consultor ganha a leitura dessas três tabelas nos clientes
-- dos vínculos dele (seg.clientes_vinculados), sem precisar escolher um.
--
-- Nada de dado de negócio (mart.<tabelas>), pessoas, matriz ou preferências: só o cadastro
-- de painéis e o controle da carga. Nenhuma escrita: as gravações da Gestão continuam só do
-- admin (migration 27).
--
-- Só adição. A API com o código de 25/11/2026 usa estas políticas na barra lateral do
-- consultor; sem elas, a barra mostra as empresas com as camadas desligadas (o resto
-- funciona).

create function seg.eh_consultor()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select exists (
        select 1
          from seg.usuario u
         where u.id = seg.usuario_atual()
           and u.ativo
           and u.perfil = 'consultor'
    )
$$;

comment on function seg.eh_consultor() is
    'O usuário de app.usuario_id está ativo e tem o perfil consultor (Gestão só de leitura).';

revoke all on function seg.eh_consultor() from public;
grant execute on function seg.eh_consultor() to api_site;

-- (select …) nas políticas: a função roda uma vez por consulta, não por linha
create policy api_site_carteira_select on cfg.cliente_tela
    for select to api_site
    using ((select seg.eh_consultor()) and cliente_id in (select seg.clientes_vinculados()));

create policy api_site_carteira_select on cfg.fonte_carga
    for select to api_site
    using ((select seg.eh_consultor()) and cliente_id in (select seg.clientes_vinculados()));

create policy api_site_carteira_select on mart.carga_fonte
    for select to api_site
    using ((select seg.eh_consultor()) and cliente_id in (select seg.clientes_vinculados()));

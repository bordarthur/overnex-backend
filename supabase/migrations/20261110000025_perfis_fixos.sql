-- Migration 25 — perfis fixos, painel ligado por cliente e dados do convite.
--
-- Por quê: a Gestão (mockup Gestão M2.1.0) trabalha com 4 perfis iguais em todos os
-- clientes (Admin BORD, Consultor por carteira, Cliente principal, Cliente usuário), com
-- painéis ligados ou desligados por cliente e com uma matriz "o que cada perfil vê" por
-- cliente. Até aqui o acesso era um perfil livre por cliente (cfg.perfil_acesso), montado
-- por SQL, e não havia como desligar um painel de um cliente.
--
--   - seg.usuario.perfil: o perfil da pessoa, o mesmo em todos os clientes dela. 'admin' é
--     o único que vê todos os clientes sem vínculo (migration 26). seg.usuario.tipo
--     ('cliente', 'lann') continua existindo e NÃO dá acesso a nada.
--   - cfg.perfil_acesso.perfil: cada cliente ganha 3 perfis fixos (consultor,
--     cliente_principal, cliente_usuario); a matriz da Gestão grava em cfg.perfil_tela
--     deles. perfil null = perfil livre antigo, que continua valendo na checagem mas não
--     aparece na Gestão.
--   - cfg.cliente_tela: painel ligado no cliente. Tela liberada = ligada no cliente E em
--     algum perfil do usuário (para o admin, basta estar ligada).
--   - seg.usuario.sessao_minutos: tempo de sessão parada da pessoa; null = o padrão do
--     perfil (seg.sessao_padrao). Usado a partir da migration 28.
--   - convidado_em, primeiro_acesso_em, desativado_em: para a situação da pessoa na Gestão
--     (convite enviado, convite vencido, desativado).
--
-- Conversão do que já existe (em produção: o perfil "Desenvolvimento" e o usuário de
-- desenvolvimento): os painéis que algum perfil do cliente liberava ficam ligados; os perfis
-- fixos nascem com a matriz padrão do mockup (Cliente usuário sem Pessoas, Resultado e
-- Caixa; Consultor sem Pessoas), mantendo o ve_dado_nominal que havia; quem era gestor ou
-- admin de cliente vira Cliente principal, quem era leitor vira Cliente usuário, e o vínculo
-- de perfil passa do perfil livre para o fixo. Os perfis livres ficam, sem usuários.
--
-- A API antiga continua funcionando depois desta migration (nada do que ela lê muda).

-- ---------------------------------------------------------------------------
-- seg.usuario
-- ---------------------------------------------------------------------------

alter table seg.usuario
    add column perfil text not null default 'cliente_usuario'
        check (perfil in ('admin', 'consultor', 'cliente_principal', 'cliente_usuario')),
    add column sessao_minutos integer
        check (sessao_minutos in (30, 60, 240, 480, 720, 1440)),
    add column convidado_em timestamptz,
    add column primeiro_acesso_em timestamptz,
    add column desativado_em timestamptz;

alter table seg.usuario
    add constraint usuario_sessao_longa_so_admin
        check (sessao_minutos is null or sessao_minutos <= 720 or perfil = 'admin');

comment on column seg.usuario.perfil is
    'Perfil da pessoa, o mesmo em todos os clientes: admin (vê todos, sem vínculo), '
    'consultor, cliente_principal ou cliente_usuario.';
comment on column seg.usuario.sessao_minutos is
    'Minutos de sessão parada até cair; null = padrão do perfil (seg.sessao_padrao). '
    'Mais de 12 h, só admin.';
comment on column seg.usuario.convidado_em is
    'Quando o convite foi enviado pela última vez (CLI de cadastro).';
comment on column seg.usuario.primeiro_acesso_em is
    'Primeiro login (seg.iniciar_sessao, migration 28); null = ainda não entrou.';
comment on column seg.usuario.desativado_em is
    'Quando o acesso foi desativado pela Gestão; null se ativo.';

-- Minutos de sessão parada padrão de cada perfil (mockup Gestão M2.1.0). Único lugar.
create function seg.sessao_padrao(p_perfil text)
returns integer
language sql
immutable
set search_path = ''
as $$
    select case p_perfil
               when 'admin' then 1440
               when 'consultor' then 480
               else 240
           end
$$;

comment on function seg.sessao_padrao(text) is
    'Minutos de sessão parada padrão do perfil: admin 24 h, consultor 8 h, clientes 4 h.';

-- Papel do vínculo (seg.usuario_cliente.papel) que corresponde ao perfil.
create function seg.papel_do_perfil(p_perfil text)
returns text
language sql
immutable
set search_path = ''
as $$
    select case p_perfil
               when 'admin' then 'admin'
               when 'cliente_usuario' then 'leitor'
               else 'gestor'
           end
$$;

comment on function seg.papel_do_perfil(text) is
    'Papel do vínculo para o perfil: consultor e cliente_principal = gestor; '
    'cliente_usuario = leitor.';

revoke all on function seg.sessao_padrao(text) from public;
revoke all on function seg.papel_do_perfil(text) from public;
grant execute on function seg.sessao_padrao(text) to api_site;
grant execute on function seg.papel_do_perfil(text) to api_site;

-- ---------------------------------------------------------------------------
-- cfg.perfil_acesso: perfis fixos
-- ---------------------------------------------------------------------------

alter table cfg.perfil_acesso
    add column perfil text
        check (perfil in ('consultor', 'cliente_principal', 'cliente_usuario'));

create unique index perfil_acesso_um_fixo_por_cliente
    on cfg.perfil_acesso (cliente_id, perfil) where perfil is not null;

comment on column cfg.perfil_acesso.perfil is
    'Perfil fixo que esta linha representa no cliente (um de cada por cliente); null = '
    'perfil livre antigo, fora da Gestão.';

-- Todo cliente novo nasce com os 3 perfis fixos (sem telas: a matriz padrão entra quando
-- o painel é ligado, pela API).
create function cfg.criar_perfis_fixos()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    insert into cfg.perfil_acesso (cliente_id, nome, perfil)
    values (new.id, 'Consultor por carteira', 'consultor'),
           (new.id, 'Cliente principal', 'cliente_principal'),
           (new.id, 'Cliente usuário', 'cliente_usuario')
    on conflict (cliente_id, nome) do update set perfil = excluded.perfil;
    return new;
end
$$;

revoke all on function cfg.criar_perfis_fixos() from public;

create trigger cliente_perfis_fixos
    after insert on cfg.cliente
    for each row execute function cfg.criar_perfis_fixos();

-- ---------------------------------------------------------------------------
-- cfg.cliente_tela: painel ligado no cliente
-- ---------------------------------------------------------------------------

create table cfg.cliente_tela (
    cliente_id  uuid not null references cfg.cliente (id),
    codigo_tela text not null check (codigo_tela ~ '^[A-Z]{2,5}-[0-9]{2}$'),
    ligada      boolean not null default true,
    primary key (cliente_id, codigo_tela)
);

comment on table cfg.cliente_tela is
    'Painéis ligados no cliente (Gestão). Sem linha, ou com ligada = false, a tela não '
    'abre para ninguém do cliente, nem para o admin.';

alter table cfg.cliente_tela enable row level security;
alter table cfg.cliente_tela force row level security;

revoke all on cfg.cliente_tela from public;
grant select on cfg.cliente_tela to api_site;

create policy api_site_select on cfg.cliente_tela
    for select to api_site
    using (cliente_id in (select seg.clientes_do_usuario()));

-- ---------------------------------------------------------------------------
-- Conversão do que já existe
-- ---------------------------------------------------------------------------

-- 1. perfis fixos dos clientes que já existem
insert into cfg.perfil_acesso (cliente_id, nome, perfil)
select c.id, p.nome, p.perfil
  from cfg.cliente c
 cross join (values ('Consultor por carteira', 'consultor'),
                    ('Cliente principal', 'cliente_principal'),
                    ('Cliente usuário', 'cliente_usuario')) p (nome, perfil)
on conflict (cliente_id, nome) do update set perfil = excluded.perfil;

-- 2. painéis ligados: os que algum perfil do cliente já liberava
insert into cfg.cliente_tela (cliente_id, codigo_tela)
select distinct p.cliente_id, pt.codigo_tela
  from cfg.perfil_tela pt
  join cfg.perfil_acesso p on p.id = pt.perfil_id
on conflict do nothing;

-- 3. matriz padrão nos perfis fixos, com o dado nominal que os perfis livres já tinham
insert into cfg.perfil_tela (perfil_id, codigo_tela, ve_dado_nominal)
select f.id, t.codigo_tela, t.ve_dado_nominal
  from cfg.perfil_acesso f
  join (
        select p.cliente_id, pt.codigo_tela, bool_or(pt.ve_dado_nominal) as ve_dado_nominal
          from cfg.perfil_tela pt
          join cfg.perfil_acesso p on p.id = pt.perfil_id
         where p.perfil is null
         group by p.cliente_id, pt.codigo_tela
       ) t on t.cliente_id = f.cliente_id
 where f.perfil is not null
   and not (f.perfil in ('consultor', 'cliente_usuario') and t.codigo_tela = 'PES-01')
   and not (f.perfil = 'cliente_usuario' and t.codigo_tela in ('FIN-02', 'FIN-04'))
on conflict do nothing;

-- 4. tabelas de dados (/api/dados): o Cliente principal lê o que os perfis livres liam
insert into cfg.perfil_tabela (perfil_id, tabela, ve_dado_nominal)
select f.id, t.tabela, t.ve_dado_nominal
  from cfg.perfil_acesso f
  join (
        select p.cliente_id, pt.tabela, bool_or(pt.ve_dado_nominal) as ve_dado_nominal
          from cfg.perfil_tabela pt
          join cfg.perfil_acesso p on p.id = pt.perfil_id
         where p.perfil is null
         group by p.cliente_id, pt.tabela
       ) t on t.cliente_id = f.cliente_id
 where f.perfil = 'cliente_principal'
on conflict do nothing;

-- 5. perfil de quem já existe: gestor ou admin de algum cliente = Cliente principal
update seg.usuario u
   set perfil = 'cliente_principal'
 where exists (
        select 1
          from seg.usuario_cliente uc
         where uc.usuario_id = u.id
           and uc.papel in ('gestor', 'admin')
       );

-- 6. o vínculo de perfil passa do perfil livre para o fixo do perfil da pessoa
delete from seg.usuario_perfil up
 using cfg.perfil_acesso p
 where p.id = up.perfil_id
   and p.perfil is null;

insert into seg.usuario_perfil (usuario_id, cliente_id, perfil_id)
select uc.usuario_id, uc.cliente_id, f.id
  from seg.usuario_cliente uc
  join seg.usuario u on u.id = uc.usuario_id
  join cfg.perfil_acesso f on f.cliente_id = uc.cliente_id and f.perfil = u.perfil
on conflict do nothing;

-- 7. quem já entrou alguma vez não aparece como "convite enviado"
update seg.usuario u
   set primeiro_acesso_em = l.primeiro
  from (
        select usuario_id, min(em) as primeiro
          from seg.log_acesso
         where resultado = 'permitido'
           and usuario_id is not null
         group by usuario_id
       ) l
 where l.usuario_id = u.id;

-- conferência
do $$
begin
    if exists (
        select 1
          from cfg.cliente c
         where (select count(*) from cfg.perfil_acesso p
                 where p.cliente_id = c.id and p.perfil is not null) <> 3
    ) then
        raise exception 'esperava 3 perfis fixos em cada cliente';
    end if;
    if exists (
        select 1
          from seg.usuario_perfil up
          join cfg.perfil_acesso p on p.id = up.perfil_id
         where p.perfil is null
    ) then
        raise exception 'ficou usuário ligado a perfil livre';
    end if;
    if exists (
        select 1
          from seg.usuario_cliente uc
         where not exists (
                select 1 from seg.usuario_perfil up
                 where up.usuario_id = uc.usuario_id and up.cliente_id = uc.cliente_id)
    ) then
        raise exception 'ficou vínculo de cliente sem perfil fixo';
    end if;
end
$$;

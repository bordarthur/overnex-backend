-- Migration 35 — unidades: setor, segmento, grupo e endereço de cada empresa (cfg.empresa).
--
-- Por quê: a bandeja "Negócios" do front (mockup M3.1.0) organiza o cliente em
-- cliente › setor › grupo › unidade (ex.: Leão de Ouro › Varejo › Lupo › Lupo 1). O cliente é
-- o cfg.cliente (a "empresa" que se escolhe no front); a unidade é o cfg.empresa (o que a
-- query das telas filtra). Um cliente pode ter negócios de setores diferentes (manual 6.6),
-- então o setor deixa de ser do cliente (migration 34) e passa a ser de cada unidade, junto
-- com o segmento, o grupo e o endereço:
--
--   - cfg.empresa ganha setor (FK para cfg.setor; null = não definido), segmento (texto
--     livre: "Calçados"), grupo (texto: "Lupo"; null = sem grupo), uf, cidade, bairro e
--     endereco (opcionais). Cadastro por SQL (supabase/producao/modelo_unidades.sql); a API
--     só lê. O `nome` continua sendo a unidade e o `cliente_id`, o cliente;
--   - o setor do cliente vai para as unidades dele que estão sem setor, e cfg.cliente.setor
--     sai (só a API de 25/11/2026, que não foi publicada, o lia). Os setores do cliente
--     passam a ser os das unidades dele, sem repetir;
--   - seg.contexto_acesso: `setores` passa a ser o das unidades liberadas à pessoa no cliente
--     escolhido, e cada item de `empresas` ganha o `setor` da unidade (a API soma só as
--     unidades do setor nos painéis de um setor). Mesmo retorno: create or replace;
--   - o Consultor por carteira lê as unidades dos vínculos dele sem escolher um cliente (a
--     barra lateral da Gestão mostra os setores de cada empresa), como a migration 33 fez com
--     painéis, fontes e cargas.
--
-- O cfg.cliente.segmento continua: no front ele passou a se chamar "Ramo" ("Frigorífico"),
-- porque segmento agora é da unidade.
--
-- Ordem em produção: esta migration antes da API nova (a API nova lê cfg.empresa.setor,
-- grupo e segmento). A API publicada antes dela continua funcionando depois: não lê
-- cfg.cliente.setor, e a coluna nova `setor` dos itens de `empresas` é ignorada.

-- ---------------------------------------------------------------------------
-- 1. colunas da unidade
-- ---------------------------------------------------------------------------
alter table cfg.empresa
    add column setor    text references cfg.setor (id),
    add column segmento text check (btrim(segmento) <> ''),
    add column grupo    text check (btrim(grupo) <> ''),
    add column uf       text check (uf ~ '^[A-Z]{2}$'),
    add column cidade   text check (btrim(cidade) <> ''),
    add column bairro   text check (btrim(bairro) <> ''),
    add column endereco text check (btrim(endereco) <> '');

comment on column cfg.empresa.nome is
    'Nome da unidade (ex.: Lupo 1), único no cliente.';
comment on column cfg.empresa.setor is
    'Setor da unidade (cfg.setor); null = não definido. Decide os painéis por setor (API, '
    'app/seguranca/setores.py): o painel vale no cliente se alguma unidade liberada à pessoa '
    'for de um dos setores dele.';
comment on column cfg.empresa.segmento is
    'Segmento da unidade, texto livre (ex.: Calçados); null = não informado.';
comment on column cfg.empresa.grupo is
    'Grupo da unidade dentro do setor (ex.: Lupo), texto; null = sem grupo. A bandeja '
    'Negócios agrupa as unidades por setor e grupo.';
comment on column cfg.empresa.uf is 'UF da unidade (2 letras maiúsculas); opcional.';

-- ---------------------------------------------------------------------------
-- 2. o setor sai do cliente e vai para as unidades dele
-- ---------------------------------------------------------------------------
update cfg.empresa e
   set setor = c.setor
  from cfg.cliente c
 where c.id = e.cliente_id
   and e.setor is null
   and c.setor is not null;

comment on table cfg.setor is
    'Setores das unidades (cfg.empresa.setor). Alguns painéis só valem em certos setores; a '
    'lista de cada painel fica na API (app/seguranca/setores.py).';

-- ---------------------------------------------------------------------------
-- 3. seg.contexto_acesso: os setores das unidades liberadas e o setor de cada unidade
-- ---------------------------------------------------------------------------
-- O corpo é o da migration 34, com o `setor` em cada item de `empresas` e `setores` vindo
-- das unidades.
create or replace function seg.contexto_acesso(
    p_id_autenticacao uuid,
    p_codigo_tela     text default null,
    p_cliente         text default null
)
returns table (
    usuario_id  uuid,
    ativo       boolean,
    -- [{"cliente_id", "papel"}] dos vínculos com cliente ativo (admin: vazio)
    vinculos    jsonb,
    -- {"<cliente_id>": ve_dado_nominal} do cliente escolhido, se p_codigo_tela está ligada
    -- nele e algum perfil do usuário a libera (admin: basta estar ligada, e vem true)
    perfis_tela jsonb,
    -- [{"cliente_id", "empresa_id", "setor"}] das unidades liberadas no cliente escolhido,
    -- na ordem nome, id (setor null = não definido)
    empresas    jsonb,
    -- seg.usuario.perfil
    perfil      text,
    -- cliente escolhido: o de p_cliente (slug), se o usuário pode abri-lo; sem p_cliente, o
    -- único vínculo de quem tem um só; senão null
    cliente_id  uuid,
    -- setores das unidades liberadas no cliente escolhido, sem repetir; vazio = nenhuma
    -- unidade com setor, ou sem cliente
    setores     text[]
)
language plpgsql
volatile
security invoker
set search_path = ''
as $$
#variable_conflict use_column
declare
    v_id       uuid;
    v_ativo    boolean;
    v_perfil   text;
    v_cliente  uuid;
    v_empresas jsonb;
begin
    select r.id, r.ativo into v_id, v_ativo
      from seg.resolver_usuario(p_id_autenticacao) r;

    perform set_config('app.usuario_id', coalesce(v_id::text, ''), true);
    perform set_config('app.cliente_id', '', true);

    if v_id is null or not v_ativo then
        return query
        select v_id, coalesce(v_ativo, false), '[]'::jsonb, '{}'::jsonb, '[]'::jsonb,
               null::text, null::uuid, '{}'::text[];
        return;
    end if;

    select u.perfil into v_perfil from seg.usuario u where u.id = v_id;

    -- cfg.cliente só mostra os clientes que o usuário pode abrir (seg.clientes_vinculados)
    if p_cliente is not null then
        select c.id into v_cliente
          from cfg.cliente c
         where c.slug = p_cliente
           and c.ativo;
    elsif v_perfil <> 'admin' then
        select v.id into v_cliente
          from seg.clientes_vinculados() as v (id)
         where (select count(*) from seg.clientes_vinculados()) = 1;
    end if;

    -- daqui em diante o RLS só mostra o cliente escolhido
    perform set_config('app.cliente_id', coalesce(v_cliente::text, ''), true);

    if v_perfil = 'admin' then
        select jsonb_agg(
                   jsonb_build_object('cliente_id', e.cliente_id, 'empresa_id', e.id,
                                      'setor', e.setor)
                   order by e.nome, e.id)
          into v_empresas
          from cfg.empresa e
         where e.cliente_id = v_cliente;
    else
        select jsonb_agg(
                   jsonb_build_object('cliente_id', ue.cliente_id, 'empresa_id', ue.empresa_id,
                                      'setor', e.setor)
                   order by e.nome, e.id)
          into v_empresas
          from seg.usuario_empresa ue
          join cfg.empresa e on e.id = ue.empresa_id and e.cliente_id = ue.cliente_id
         where ue.usuario_id = v_id
           and ue.cliente_id = v_cliente;
    end if;
    v_empresas := coalesce(v_empresas, '[]'::jsonb);

    return query
    select
        v_id,
        true,
        coalesce((
            select jsonb_agg(
                       jsonb_build_object('cliente_id', uc.cliente_id, 'papel', uc.papel)
                       order by uc.cliente_id)
              from seg.usuario_cliente uc
              join cfg.cliente c on c.id = uc.cliente_id and c.ativo
             where uc.usuario_id = v_id
               and v_perfil <> 'admin'
        ), '[]'::jsonb),
        case
            when v_perfil = 'admin' then coalesce((
                select jsonb_build_object(ct.cliente_id::text, true)
                  from cfg.cliente_tela ct
                 where ct.cliente_id = v_cliente
                   and ct.codigo_tela = p_codigo_tela
                   and ct.ligada
            ), '{}'::jsonb)
            else coalesce((
                select jsonb_object_agg(x.cliente_id::text, x.ve_dado_nominal)
                  from (
                        select up.cliente_id, bool_or(pt.ve_dado_nominal) as ve_dado_nominal
                          from seg.usuario_perfil up
                          join cfg.perfil_tela pt on pt.perfil_id = up.perfil_id
                          join cfg.cliente_tela ct
                            on ct.cliente_id = up.cliente_id
                           and ct.codigo_tela = pt.codigo_tela
                           and ct.ligada
                         where up.usuario_id = v_id
                           and up.cliente_id = v_cliente
                           and pt.codigo_tela = p_codigo_tela
                         group by up.cliente_id
                       ) x
            ), '{}'::jsonb)
        end,
        v_empresas,
        v_perfil,
        v_cliente,
        coalesce((
            select array_agg(distinct x.setor order by x.setor)
              from jsonb_to_recordset(v_empresas) as x (setor text)
             where x.setor is not null
        ), '{}'::text[]);
end
$$;
comment on function seg.contexto_acesso(uuid, text, text) is
    'Define app.usuario_id e app.cliente_id (o cliente escolhido pelo slug, entre os que o '
    'usuário pode abrir) e devolve o perfil, os vínculos, se a tela está liberada no cliente '
    'escolhido, as unidades liberadas nele (com o setor de cada uma) e os setores delas, '
    'numa chamada (checagem de acesso da API).';

-- create or replace mantém os grants da migration 34; repetidos por clareza
revoke all on function seg.contexto_acesso(uuid, text, text) from public;
grant execute on function seg.contexto_acesso(uuid, text, text) to api_site;

-- ---------------------------------------------------------------------------
-- 4. sem o setor no cliente
-- ---------------------------------------------------------------------------
-- leva junto o grant de update (setor) da migration 34
alter table cfg.cliente drop column setor;

-- ---------------------------------------------------------------------------
-- 5. o consultor lê as unidades da carteira (barra lateral da Gestão)
-- ---------------------------------------------------------------------------
create function seg.empresas_da_carteira()
returns setof uuid
language sql
stable
security definer
set search_path = ''
as $$
    select ue.empresa_id
      from seg.usuario_empresa ue
      join cfg.cliente c on c.id = ue.cliente_id and c.ativo
     where ue.usuario_id = seg.usuario_atual()
$$;

comment on function seg.empresas_da_carteira() is
    'Unidades dos vínculos do usuário de app.usuario_id em todos os clientes ativos dele, sem '
    'escolher um (só a política de leitura do consultor em cfg.empresa usa).';

revoke all on function seg.empresas_da_carteira() from public;
grant execute on function seg.empresas_da_carteira() to api_site;

-- só sem cliente escolhido (a barra lateral) e só as unidades dos vínculos: com um cliente
-- escolhido, vale a política de sempre (as unidades liberadas nele, seg.empresas_do_usuario),
-- e o RLS continua mostrando só o cliente escolhido
create policy api_site_carteira_select on cfg.empresa
    for select to api_site
    using ((select seg.eh_consultor())
           and (select seg.cliente_atual()) is null
           and id in (select seg.empresas_da_carteira()));

-- Migration 34 — setor controlado da empresa, para os painéis que só existem num setor.
--
-- Por quê: o Estoque (e depois Compras, Custo × preço, Logística e Carteira de clientes) só
-- vale para empresas do setor Distribuidora. O setor de hoje é o `cfg.cliente.segmento`,
-- texto livre ("Frigorífico", "Atacado"), que não serve para regra. Esta migration cria a
-- lista fechada de setores e o setor de cada empresa; o `segmento` continua como descrição
-- livre.
--
--   - cfg.setor: os setores (id e rótulo), lidos por qualquer usuário, como o plano de contas;
--     a API não grava. Setor novo: migration ou SQL;
--   - cfg.cliente.setor: o setor da empresa (null enquanto não for definido). O admin define
--     ao criar e ao editar a empresa na Gestão (update só nesta coluna, além de nome e
--     segmento; o insert já tinha grant na tabela inteira, migration 2);
--   - seg.contexto_acesso devolve também `setores`, os do cliente escolhido: a API decide se
--     a tela vale no setor (app/seguranca/setores.py) na mesma ida ao banco da checagem.
--     É uma lista para não fechar a porta a um grupo com mais de um negócio (Distribuidora e
--     Varejo, manual 6.6): hoje, zero ou um setor por empresa.
--
-- Quais painéis são de qual setor fica na API, junto do catálogo dos painéis. Trocar o setor
-- de uma empresa não apaga a configuração dos painéis do setor antigo (cfg.cliente_tela e a
-- matriz): eles só deixam de valer, e voltam se o setor voltar.
--
-- A API nova depende desta migration (lê cfg.cliente.setor e cfg.setor): aplique antes. A
-- API antiga continua funcionando depois dela (a coluna nova de seg.contexto_acesso é
-- ignorada por ela).

create table cfg.setor (
    id     text primary key check (id ~ '^[a-z]+(-[a-z]+)*$'),
    rotulo text not null check (btrim(rotulo) <> ''),
    ordem  smallint not null default 0
);

comment on table cfg.setor is
    'Setores da empresa (cfg.cliente.setor). Alguns painéis só valem em certos setores; a '
    'lista de cada painel fica na API (app/seguranca/setores.py).';

alter table cfg.setor enable row level security;
alter table cfg.setor force row level security;

revoke all on cfg.setor from public;
grant select on cfg.setor to api_site;

create policy api_site_select on cfg.setor
    for select to api_site
    using (true);

insert into cfg.setor (id, rotulo, ordem) values
    ('distribuidora', 'Distribuidora', 1),
    ('industria',     'Indústria',     2),
    ('varejo',        'Varejo',        3),
    ('servicos',      'Serviços',      4);

alter table cfg.cliente
    add column setor text references cfg.setor (id);

comment on column cfg.cliente.setor is
    'Setor da empresa (cfg.setor); null = não definido. Decide os painéis por setor. O '
    'segmento continua como descrição livre.';

grant update (setor) on cfg.cliente to api_site;

-- seg.contexto_acesso: muda o retorno (coluna setores), então drop + create. O corpo é o da
-- migration 26, com a coluna nova no fim.
drop function seg.contexto_acesso(uuid, text, text);

create function seg.contexto_acesso(
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
    -- [{"cliente_id", "empresa_id"}] liberadas no cliente escolhido, na ordem nome, id
    empresas    jsonb,
    -- seg.usuario.perfil
    perfil      text,
    -- cliente escolhido: o de p_cliente (slug), se o usuário pode abri-lo; sem p_cliente, o
    -- único vínculo de quem tem um só; senão null
    cliente_id  uuid,
    -- setores do cliente escolhido (cfg.cliente.setor); vazio = sem setor ou sem cliente
    setores     text[]
)
language plpgsql
volatile
security invoker
set search_path = ''
as $$
#variable_conflict use_column
declare
    v_id      uuid;
    v_ativo   boolean;
    v_perfil  text;
    v_cliente uuid;
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
        case
            when v_perfil = 'admin' then coalesce((
                select jsonb_agg(
                           jsonb_build_object('cliente_id', e.cliente_id, 'empresa_id', e.id)
                           order by e.nome, e.id)
                  from cfg.empresa e
                 where e.cliente_id = v_cliente
            ), '[]'::jsonb)
            else coalesce((
                select jsonb_agg(
                           jsonb_build_object('cliente_id', ue.cliente_id, 'empresa_id', ue.empresa_id)
                           order by e.nome, e.id)
                  from seg.usuario_empresa ue
                  join cfg.empresa e on e.id = ue.empresa_id and e.cliente_id = ue.cliente_id
                 where ue.usuario_id = v_id
                   and ue.cliente_id = v_cliente
            ), '[]'::jsonb)
        end,
        v_perfil,
        v_cliente,
        coalesce((
            select array_agg(c.setor order by c.setor)
              from cfg.cliente c
             where c.id = v_cliente
               and c.setor is not null
        ), '{}'::text[]);
end
$$;
comment on function seg.contexto_acesso(uuid, text, text) is
    'Define app.usuario_id e app.cliente_id (o cliente escolhido pelo slug, entre os que o '
    'usuário pode abrir) e devolve o perfil, os vínculos, se a tela está liberada no cliente '
    'escolhido, as empresas e os setores dele, numa chamada (checagem de acesso da API).';

revoke all on function seg.contexto_acesso(uuid, text, text) from public;
grant execute on function seg.contexto_acesso(uuid, text, text) to api_site;

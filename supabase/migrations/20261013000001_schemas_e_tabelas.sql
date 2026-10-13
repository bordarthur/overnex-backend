-- Migration 1 — schemas e tabelas do Overnex.
--
-- stg  : reservado. A carga usa tabela temporária, então não há tabelas aqui.
-- cfg  : cadastros e configuração por cliente.
-- seg  : usuários, vínculos e logs.
-- mart : dados prontos para as telas e controle da carga.
--
-- Nada é criado em public.

create schema stg;
create schema cfg;
create schema seg;
create schema mart;

comment on schema stg is 'Reservado. A carga usa tabela temporária; não criar tabelas aqui.';
comment on schema cfg is 'Cadastros e configuração por cliente.';
comment on schema seg is 'Usuários, vínculos de acesso e logs.';
comment on schema mart is 'Dados prontos para as telas e controle da carga diária.';

-- ---------------------------------------------------------------------------
-- cfg: cliente, empresa, painel, perfis
-- ---------------------------------------------------------------------------

create table cfg.cliente (
    id           uuid primary key default gen_random_uuid(),
    nome         text not null,
    slug         text not null unique check (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
    logo_url     text,
    cor_destaque text check (cor_destaque ~ '^#[0-9a-fA-F]{6}$'),
    ativo        boolean not null default true
);

create table cfg.empresa (
    id         uuid primary key default gen_random_uuid(),
    cliente_id uuid not null references cfg.cliente (id),
    nome       text not null,
    unique (cliente_id, nome),
    -- alvo das FKs compostas que garantem "empresa do mesmo cliente"
    unique (id, cliente_id)
);

create table cfg.painel (
    cliente_id  uuid not null references cfg.cliente (id),
    versao      integer not null check (versao > 0),
    status      text not null default 'rascunho'
                check (status in ('rascunho', 'publicado', 'arquivado')),
    config_json jsonb not null default '{}'::jsonb,
    primary key (cliente_id, versao)
);

-- no máximo um painel publicado por cliente
create unique index painel_um_publicado_por_cliente
    on cfg.painel (cliente_id) where status = 'publicado';

create table cfg.perfil_acesso (
    id         uuid primary key default gen_random_uuid(),
    cliente_id uuid not null references cfg.cliente (id),
    nome       text not null,
    unique (cliente_id, nome),
    unique (id, cliente_id)
);

create table cfg.perfil_tela (
    perfil_id       uuid not null references cfg.perfil_acesso (id) on delete cascade,
    codigo_tela     text not null check (codigo_tela ~ '^[A-Z]{2,5}-[0-9]{2}$'),
    ve_dado_nominal boolean not null default false,
    primary key (perfil_id, codigo_tela)
);

create table cfg.parametro (
    empresa_id  uuid not null references cfg.empresa (id),
    chave       text not null,
    valor       jsonb not null,
    vigente_de  date not null,
    vigente_ate date,
    primary key (empresa_id, chave, vigente_de),
    check (vigente_ate is null or vigente_ate >= vigente_de)
);

-- ---------------------------------------------------------------------------
-- seg: usuários e vínculos (antes de cfg.regra_lancamento, que referencia usuário)
-- ---------------------------------------------------------------------------

create table seg.usuario (
    id              uuid primary key default gen_random_uuid(),
    object_id_entra uuid not null unique,
    email           text not null,
    tipo            text not null check (tipo in ('cliente', 'lann')),
    ativo           boolean not null default true
);

create unique index usuario_email_unico on seg.usuario (lower(email));

create table seg.usuario_cliente (
    usuario_id uuid not null references seg.usuario (id) on delete cascade,
    cliente_id uuid not null references cfg.cliente (id),
    papel      text not null check (papel in ('leitor', 'gestor', 'admin')),
    primary key (usuario_id, cliente_id)
);

-- cliente_id é redundante de propósito: as FKs compostas garantem que o perfil
-- e a empresa liberados sejam do mesmo cliente do vínculo.
create table seg.usuario_perfil (
    usuario_id uuid not null,
    cliente_id uuid not null,
    perfil_id  uuid not null,
    primary key (usuario_id, perfil_id),
    foreign key (usuario_id, cliente_id)
        references seg.usuario_cliente (usuario_id, cliente_id) on delete cascade,
    foreign key (perfil_id, cliente_id)
        references cfg.perfil_acesso (id, cliente_id) on delete cascade
);

create table seg.usuario_empresa (
    usuario_id uuid not null,
    cliente_id uuid not null,
    empresa_id uuid not null,
    primary key (usuario_id, empresa_id),
    foreign key (usuario_id, cliente_id)
        references seg.usuario_cliente (usuario_id, cliente_id) on delete cascade,
    foreign key (empresa_id, cliente_id)
        references cfg.empresa (id, cliente_id) on delete cascade
);

-- Logs sem FK: precisam sobreviver à exclusão do que registram.
create table seg.log_acesso (
    id              bigint generated always as identity primary key,
    em              timestamptz not null default now(),
    usuario_id      uuid,
    object_id_entra uuid,
    cliente_id      uuid,
    empresa_id      uuid,
    codigo_tela     text not null,
    resultado       text not null check (resultado in ('permitido', 'negado')),
    etapa_negada    smallint check (etapa_negada between 1 and 6),
    motivo          text,
    check ((resultado = 'negado') = (etapa_negada is not null))
);

create index log_acesso_em on seg.log_acesso (em);
create index log_acesso_usuario on seg.log_acesso (usuario_id, em);

create table seg.log_alteracao (
    id          bigint generated always as identity primary key,
    em          timestamptz not null default now(),
    usuario_id  uuid not null,
    tabela      text not null,
    registro_id text not null,
    operacao    text not null check (operacao in ('insert', 'update', 'delete')),
    antes       jsonb,
    depois      jsonb
);

create index log_alteracao_tabela on seg.log_alteracao (tabela, registro_id, em);

-- ---------------------------------------------------------------------------
-- cfg: regras e configuração da carga
-- ---------------------------------------------------------------------------

create table cfg.regra_lancamento (
    id                 bigint generated always as identity primary key,
    empresa_id         uuid not null references cfg.empresa (id),
    fonte              text not null,
    campo              text not null,
    operador           text not null check (operador in ('=', '<>', 'in', 'not in', 'like', 'between')),
    valor              text not null,
    acao               text not null check (acao in ('reclassificar', 'excluir')),
    id_descritivo_novo integer,
    prioridade         integer not null default 100,
    vigente_de         date not null,
    vigente_ate        date,
    motivo             text not null,
    alterado_por       uuid not null references seg.usuario (id),
    alterado_em        timestamptz not null default now(),
    check (vigente_ate is null or vigente_ate >= vigente_de),
    check ((acao = 'reclassificar') = (id_descritivo_novo is not null))
);

create table cfg.fonte_carga (
    id             bigint generated by default as identity primary key,
    cliente_id     uuid not null references cfg.cliente (id),
    tipo           text not null check (tipo in ('lancamento')),
    -- relativo ao bucket, ex.: dataflow/dbo_f_x/dbo_f_x.parquet
    caminho_bucket text not null check (
        caminho_bucket ~ '^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*\.parquet$'
        and caminho_bucket !~ '(^|/)\.\.(/|$)'
    ),
    ativo          boolean not null default true,
    unique (cliente_id, tipo, caminho_bucket),
    unique (id, cliente_id)
);

-- Valor da coluna Empresa no DW → cfg.empresa.id do mesmo cliente.
create table cfg.mapa_empresa (
    cliente_id     uuid not null references cfg.cliente (id),
    empresa_origem text not null,
    empresa_id     uuid not null,
    primary key (cliente_id, empresa_origem),
    foreign key (empresa_id, cliente_id) references cfg.empresa (id, cliente_id)
);

-- ---------------------------------------------------------------------------
-- mart
-- ---------------------------------------------------------------------------

create table mart.lancamento (
    empresa_id    uuid not null references cfg.empresa (id),
    mes           date not null check (mes = date_trunc('month', mes)::date),
    id_descritivo integer not null,
    valor         numeric(18, 2) not null,
    primary key (empresa_id, mes, id_descritivo)
);

create table mart.carga (
    id       bigint generated always as identity primary key,
    inicio   timestamptz not null default now(),
    fim      timestamptz,
    status   text not null default 'rodando' check (status in ('rodando', 'ok', 'erro')),
    mensagem text
);

create table mart.carga_fonte (
    id              bigint generated always as identity primary key,
    carga_id        bigint not null references mart.carga (id) on delete cascade,
    fonte_id        bigint not null,
    cliente_id      uuid not null,
    data_snapshot   date not null,
    status          text not null default 'rodando' check (status in ('rodando', 'ok', 'erro')),
    linhas_lidas    integer check (linhas_lidas >= 0),
    linhas_gravadas integer check (linhas_gravadas >= 0),
    mensagem        text,
    inicio          timestamptz not null default now(),
    fim             timestamptz,
    -- cliente_id precisa ser o da fonte
    foreign key (fonte_id, cliente_id) references cfg.fonte_carga (id, cliente_id)
);

create index carga_fonte_ultima_ok
    on mart.carga_fonte (cliente_id, fonte_id, inicio desc) where status = 'ok';

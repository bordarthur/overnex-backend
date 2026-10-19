-- Migration 9 — feriados do cliente, para o calendário de dias úteis.
--
-- Os feriados nacionais vêm da biblioteca `holidays`, na API. Aqui ficam os que valem só
-- para o cliente: estaduais, municipais, ponto facultativo que a empresa não trabalha
-- (ex.: Carnaval). Dia útil = segunda a sexta, fora dos feriados nacionais e destes.
--
-- Só adição.

create table cfg.feriado (
    cliente_id uuid not null references cfg.cliente (id),
    data       date not null,
    nome       text not null check (length(trim(nome)) > 0),
    primary key (cliente_id, data)
);

comment on table cfg.feriado is
    'Feriados do cliente (estaduais, municipais, ponto facultativo) para os dias úteis. '
    'Os nacionais vêm da biblioteca holidays, na API.';

alter table cfg.feriado enable row level security;
alter table cfg.feriado force row level security;

revoke all on cfg.feriado from public;
grant select on cfg.feriado to api_site;

create policy api_site_select on cfg.feriado
    for select to api_site
    using (cliente_id in (select seg.clientes_do_usuario()));

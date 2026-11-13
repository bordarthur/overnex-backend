"""Empresas na Gestão: a barra lateral (todas, com o estado das camadas) e as abas de uma
empresa (implantação, prontidão dos dados, painéis, matriz de acesso, pessoas, preferências,
feriados e cargas), mais as gravações de painel ligado, matriz e feriado.

"Empresa" na tela é o cfg.cliente. As leituras de um cliente rodam com ele escolhido
(app.cliente_id, pela checagem do admin); a barra lateral lê todos pelas políticas do admin
(migration 27). Nada aqui lê dado de negócio: só cadastro e controle da carga.

Regras:
- painel disponível = alguma fonte ativa que ele exige já teve carga `ok` (catalogo.py);
  painel sem dado não liga (pode desligar);
- camada ativa = todos os painéis dela ligados e com dado; em implantação = parte;
  desligada = nenhum ligado;
- ao ligar um painel pela primeira vez no cliente, os perfis fixos recebem a matriz padrão;
- a matriz só mexe nos 3 perfis fixos do cliente; o Admin BORD vê tudo o que está ligado;
- linha nova na matriz vê dado nominal só nas telas que exigem (Equipe comercial e Receber
  e pagar); o dado nominal de Pessoas (nomes de Cargos e salários) continua por SQL.
"""

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any
from uuid import UUID

from app.admin import textos, usuarios
from app.admin.catalogo import CAMADAS, PAINEIS, POR_CODIGO, Painel, tem_dado, usada_por
from app.admin.contrato import (
    CamadaDoCliente,
    CamadaResumo,
    CargaDoCliente,
    Chip,
    ClienteDaGestao,
    ClienteDaLista,
    ClientePayload,
    Feriado,
    FonteDoCliente,
    GestaoPayload,
    GrupoDoMenu,
    ItemDoMenu,
    LinhaDaMatriz,
    Matriz,
    OpcaoPreferencia,
    OpcaoSessao,
    PainelDoCliente,
    PerfilDaGestao,
    PerfilDaMatriz,
    PreferenciaDoCliente,
    Status,
    UsuarioDoCliente,
)
from app.admin.usuarios import NaoEncontrado, RegraInvalida, registrar
from app.core.cache import por_requisicao
from app.core.db import Conexao, em_uma_ida
from app.seguranca import perfis
from app.telas.moldura import cliente_da_linha
from app.telas.visao_geral import preferencias

MSG_PAINEL = "Painel não encontrado."
MSG_PERFIL = "Perfil não encontrado."
MSG_ADMIN_FIXO = "Admin BORD vê tudo e não pode ser alterado."
MSG_SEM_DADO = "Este painel ainda não tem dado nesta empresa."
MSG_PREFERENCIA = "Preferência não encontrada."
MSG_OPCAO = "Opção inválida."
MSG_FERIADO = "Feriado não encontrado."

ESTADOS: dict[str, tuple[str, Status]] = {
    "ativa": ("Ativa", "saudavel"),
    "implantacao": ("Em implantação", "atencao"),
    "desligada": ("Desligada", "neutro"),
}
CARGAS_NA_LISTA = 10
OBSERVACAO_MAXIMA = 200

# ---------- consultas ----------

SQL_CLIENTES = "select id, nome, slug from cfg.cliente where ativo order by nome, id"
SQL_LIGADAS = "select cliente_id, codigo_tela from cfg.cliente_tela where ligada"
# tabelas de fontes ativas que já tiveram carga ok
SQL_COM_CARGA = """
    select distinct f.cliente_id, f.tabela_destino
      from cfg.fonte_carga f
     where f.ativo
       and f.tipo = 'tabela'
       and exists (select 1 from mart.carga_fonte cf
                    where cf.fonte_id = f.id and cf.status = 'ok')
"""
SQL_PERFIS = """
    select p.perfil,
           seg.sessao_padrao(p.perfil) as minutos,
           (select count(*) from seg.usuario u where u.perfil = p.perfil and u.ativo) as pessoas
      from unnest(%s::text[]) as p (perfil)
"""

SQL_CLIENTE = "select id, nome, slug, logo_url from cfg.cliente where id = %s"
SQL_TELAS = "select codigo_tela, ligada from cfg.cliente_tela where cliente_id = %s"
SQL_FONTES = """
    select coalesce(f.tabela_destino, 'lançamentos (formato antigo)') as tabela,
           f.tipo, f.ativo,
           u.status as ultimo_status, u.mensagem as ultima_mensagem,
           ok.data_snapshot, ok.linhas_gravadas
      from cfg.fonte_carga f
      left join lateral (
            select cf.status, cf.mensagem
              from mart.carga_fonte cf
             where cf.fonte_id = f.id
             order by cf.inicio desc, cf.id desc
             limit 1
           ) u on true
      left join lateral (
            select cf.data_snapshot, cf.linhas_gravadas
              from mart.carga_fonte cf
             where cf.fonte_id = f.id and cf.status = 'ok'
             order by cf.inicio desc, cf.id desc
             limit 1
           ) ok on true
     where f.cliente_id = %s
     order by tabela, f.id
"""
SQL_MATRIZ = """
    select p.perfil, pt.codigo_tela
      from cfg.perfil_acesso p
      join cfg.perfil_tela pt on pt.perfil_id = p.id
     where p.cliente_id = %s
       and p.perfil is not null
"""
SQL_FERIADOS = "select data, nome from cfg.feriado where cliente_id = %s order by data"
SQL_CARGAS = """
    select cf.carga_id,
           min(cf.inicio) as inicio,
           case when bool_or(cf.fim is null) then null else max(cf.fim) end as fim,
           count(*) as fontes,
           count(*) filter (where cf.status = 'erro') as erros,
           count(*) filter (where cf.status = 'rodando') as rodando,
           (array_agg(cf.mensagem order by cf.id)
               filter (where cf.status = 'erro' and cf.mensagem is not null))[1] as mensagem
      from mart.carga_fonte cf
     where cf.cliente_id = %s
     group by cf.carga_id
     order by cf.carga_id desc
     limit %s
"""

# ---------- gravações ----------

SQL_LIGAR = """
    insert into cfg.cliente_tela (cliente_id, codigo_tela, ligada)
    values (%(cliente)s, %(codigo)s, %(ligada)s)
    on conflict (cliente_id, codigo_tela) do update set ligada = excluded.ligada
"""
SQL_LIBERAR = """
    insert into cfg.perfil_tela (perfil_id, codigo_tela, ve_dado_nominal)
    select p.id, %(codigo)s, %(nominal)s
      from cfg.perfil_acesso p
     where p.cliente_id = %(cliente)s
       and p.perfil = any(%(perfis)s::text[])
    on conflict do nothing
"""
SQL_TIRAR = """
    delete from cfg.perfil_tela pt
     using cfg.perfil_acesso p
     where p.id = pt.perfil_id
       and p.cliente_id = %(cliente)s
       and p.perfil = %(perfil)s
       and pt.codigo_tela = %(codigo)s
"""
SQL_GRAVAR_FERIADO = """
    insert into cfg.feriado (cliente_id, data, nome)
    values (%(cliente)s, %(data)s, %(nome)s)
    on conflict (cliente_id, data) do update set nome = excluded.nome
"""
SQL_APAGAR_FERIADO = """
    delete from cfg.feriado where cliente_id = %(cliente)s and data = %(data)s returning nome
"""


# ---------- camadas e painéis ----------


def _estado_da_camada(
    paineis: Sequence[Painel], ligadas: frozenset[str], com_carga: frozenset[str]
) -> tuple[str, list[str]]:
    """Estado da camada e o que falta para ela ficar ativa."""
    pendencias = [
        f"{p.nome}: desligado" if tem_dado(p, com_carga) else f"{p.nome}: sem dado"
        for p in paineis
        if p.codigo not in ligadas or not tem_dado(p, com_carga)
    ]
    if not any(p.codigo in ligadas for p in paineis):
        return "desligada", pendencias
    return ("ativa" if not pendencias else "implantacao"), pendencias


def camadas(ligadas: frozenset[str], com_carga: frozenset[str]) -> list[CamadaDoCliente]:
    saida = []
    for camada in CAMADAS:
        estado, pendencias = _estado_da_camada(
            [p for p in PAINEIS if p.camada == camada.id], ligadas, com_carga
        )
        rotulo, status = ESTADOS[estado]
        saida.append(
            CamadaDoCliente(
                id=camada.id,
                rotulo=camada.rotulo,
                estado=estado,
                estado_rotulo=rotulo,
                status=status,
                grupo=camada.grupo,
                pendencias=pendencias,
            )
        )
    return saida


def paineis(ligadas: frozenset[str], com_carga: frozenset[str]) -> list[PainelDoCliente]:
    saida = []
    for p in PAINEIS:
        disponivel = tem_dado(p, com_carga)
        faltam = " ou ".join(f"{prefixo}*" for prefixo in p.exige)
        saida.append(
            PainelDoCliente(
                codigo=p.codigo,
                nome=p.nome,
                grupo=p.grupo,
                ligado=p.codigo in ligadas,
                pode_ligar=disponivel,
                status="saudavel" if disponivel else "neutro",
                status_rotulo="disponível" if disponivel else "indisponível",
                nota=None if disponivel else f"Sem carga de {faltam} nesta empresa.",
            )
        )
    return saida


# ---------- barra lateral ----------


def gestao(conn: Conexao) -> GestaoPayload:
    """Todas as empresas com o estado das camadas, os 4 perfis e as opções de sessão."""
    linhas = em_uma_ida(
        conn,
        {
            "clientes": (SQL_CLIENTES, ()),
            "ligadas": (SQL_LIGADAS, ()),
            "com_carga": (SQL_COM_CARGA, ()),
            "perfis": (SQL_PERFIS, ([p.id for p in perfis.PERFIS],)),
        },
    )
    ligadas: dict[UUID, set[str]] = {}
    for r in linhas["ligadas"]:
        ligadas.setdefault(r["cliente_id"], set()).add(r["codigo_tela"])
    com_carga: dict[UUID, set[str]] = {}
    for r in linhas["com_carga"]:
        com_carga.setdefault(r["cliente_id"], set()).add(r["tabela_destino"])
    do_perfil = {r["perfil"]: r for r in linhas["perfis"]}

    return GestaoPayload(
        clientes=[
            ClienteDaLista(
                slug=c["slug"],
                nome=c["nome"],
                camadas=[
                    CamadaResumo(
                        id=k.id,
                        rotulo=k.rotulo,
                        estado=k.estado,
                        estado_rotulo=k.estado_rotulo,
                        status=k.status,
                    )
                    for k in camadas(
                        frozenset(ligadas.get(c["id"], ())), frozenset(com_carga.get(c["id"], ()))
                    )
                ],
            )
            for c in linhas["clientes"]
        ],
        perfis=[
            PerfilDaGestao(
                id=p.id,
                rotulo=p.rotulo,
                descricao=p.descricao,
                clientes=p.clientes,
                sessao_padrao_minutos=do_perfil[p.id]["minutos"],
                sessao_padrao_rotulo=textos.sessao(do_perfil[p.id]["minutos"]),
                pessoas=do_perfil[p.id]["pessoas"],
            )
            for p in perfis.PERFIS
        ],
        sessoes=[
            OpcaoSessao(
                minutos=m, rotulo=textos.sessao(m), so_admin=m > usuarios.SESSAO_MAXIMA_COMUM
            )
            for m in usuarios.SESSOES
        ],
    )


# ---------- abas de uma empresa ----------


def _fonte(r: Mapping[str, Any]) -> FonteDoCliente:
    status: Status
    if not r["ativo"]:
        status, rotulo = "neutro", "inativa"
    elif r["ultimo_status"] is None:
        status, rotulo = "critico", "sem carga"
    elif r["ultimo_status"] == "erro":
        status, rotulo = "critico", "erro"
    elif r["ultimo_status"] == "rodando":
        status, rotulo = "atencao", "rodando"
    else:
        status, rotulo = "saudavel", "ok"
    mensagem: str | None = r["ultima_mensagem"] if r["ultimo_status"] == "erro" else None
    return FonteDoCliente(
        tabela=r["tabela"],
        usada_por=usada_por(r["tabela"]) if r["tipo"] == "tabela" else [],
        linhas=r["linhas_gravadas"],
        ultima_data=f"{r['data_snapshot']:%d/%m/%Y}" if r["data_snapshot"] else None,
        status=status,
        status_rotulo=rotulo,
        observacao=mensagem[:OBSERVACAO_MAXIMA] if mensagem else None,
    )


def _carga(r: Mapping[str, Any], hoje: date) -> CargaDoCliente:
    inicio: datetime = r["inicio"]
    status: Status
    if r["rodando"]:
        status, rotulo = "atencao", "rodando"
    elif r["erros"]:
        status, rotulo = "critico", "erro" if r["erros"] == r["fontes"] else "parcial"
    else:
        status, rotulo = "saudavel", "ok"
    observacao = None
    if r["erros"]:
        plural = "fonte com erro" if r["erros"] == 1 else "fontes com erro"
        detalhe = f": {r['mensagem'][:OBSERVACAO_MAXIMA]}" if r["mensagem"] else ""
        observacao = f"{r['erros']} {plural}{detalhe}"
    return CargaDoCliente(
        data=textos.dia(inicio.astimezone(textos.FORTALEZA).date(), hoje),
        inicio=textos.hora(inicio),
        duracao=textos.duracao(inicio, r["fim"]),
        tabelas=r["fontes"],
        status=status,
        status_rotulo=rotulo,
        observacao=observacao,
    )


def _preferencias(conn: Conexao, cliente_id: UUID) -> list[PreferenciaDoCliente]:
    """As escolhas salvas do cliente que a Gestão também muda (hoje, Linhas de custo)."""
    return [
        PreferenciaDoCliente(
            id=p.id,
            rotulo=p.rotulo,
            descricao=p.nota,
            opcoes=[OpcaoPreferencia(id=id_, rotulo=rotulo) for id_, rotulo in p.opcoes],
            valor=preferencias.valor(conn, cliente_id, p.id),
            editavel=True,
        )
        for p in preferencias.PREFERENCIAS.values()
    ]


def cliente(conn: Conexao, cliente_id: UUID, convite_horas: int) -> ClientePayload:
    """Tudo das abas de uma empresa. O cliente já está escolhido na transação."""
    agora = textos.agora()
    hoje = agora.date()
    c = (cliente_id,)
    linhas = em_uma_ida(
        conn,
        {
            "cliente": (SQL_CLIENTE, c),
            "telas": (SQL_TELAS, c),
            "fontes": (SQL_FONTES, c),
            "matriz": (SQL_MATRIZ, c),
            "feriados": (SQL_FERIADOS, c),
            "cargas": (SQL_CARGAS, (cliente_id, CARGAS_NA_LISTA)),
            "usuarios": (usuarios.SQL_USUARIOS, ()),
            "admins": (usuarios.SQL_ADMINS_ATIVOS, ()),
            "total_clientes": (usuarios.SQL_TOTAL_CLIENTES, ()),
        },
    )
    if not linhas["cliente"]:  # a checagem do admin já garantiu o cliente
        raise LookupError("cliente da Gestão não encontrado")
    dados = linhas["cliente"][0]
    base = cliente_da_linha(dados)

    ligadas = frozenset(r["codigo_tela"] for r in linhas["telas"] if r["ligada"])
    com_carga = frozenset(
        r["tabela"]
        for r in linhas["fontes"]
        if r["tipo"] == "tabela" and r["ativo"] and r["data_snapshot"] is not None
    )
    lista_camadas = camadas(ligadas, com_carga)
    lista_paineis = paineis(ligadas, com_carga)
    no_ar = [p for p in PAINEIS if p.codigo in ligadas]

    ve: dict[str, set[str]] = {p.id: set() for p in perfis.DO_CLIENTE}
    for r in linhas["matriz"]:
        ve.setdefault(r["perfil"], set()).add(r["codigo_tela"])

    admins = linhas["admins"][0]["total"]
    total_clientes = linhas["total_clientes"][0]["total"]
    pessoas = []
    for r in linhas["usuarios"]:
        p = usuarios.pessoa_da_linha(r)
        if p.admin or dados["slug"] not in {k.slug for k in p.clientes}:
            continue
        u = usuarios.usuario(p, agora, convite_horas, total_clientes, admins)
        vistos = sum(1 for painel in no_ar if painel.codigo in ve.get(p.perfil, set()))
        pessoas.append(
            UsuarioDoCliente(
                **u.model_dump(),
                outros_clientes=[k for k in p.clientes if k.slug != dados["slug"]],
                paineis_que_ve=f"{vistos if p.ativo else 0} de {len(no_ar)}",
            )
        )

    ativas = sum(1 for k in lista_camadas if k.estado == "ativa")
    ativos = sum(1 for u in pessoas if u.situacao.id != "inativo")
    ultima = linhas["cargas"][0] if linhas["cargas"] else None
    resumo = [
        Chip(
            rotulo=f"{ativas} de {len(lista_camadas)} camadas ativas",
            status="saudavel" if ativas == len(lista_camadas) else "atencao",
        ),
        Chip(rotulo=f"{ativos} {'usuário' if ativos == 1 else 'usuários'}", status="neutro"),
        Chip(
            rotulo=f"Última carga: {textos.quando(ultima['inicio'], agora)}"
            if ultima
            else "Sem carga ainda",
            status=_carga(ultima, hoje).status if ultima else "critico",
        ),
    ]

    menu: dict[str, list[ItemDoMenu]] = {}
    for painel in no_ar:
        if painel.codigo in ve[perfis.CLIENTE_PRINCIPAL] and tem_dado(painel, com_carga):
            menu.setdefault(painel.grupo, []).append(ItemDoMenu(nome=painel.nome))

    with por_requisicao():
        lista_preferencias = _preferencias(conn, cliente_id)

    return ClientePayload(
        cliente=ClienteDaGestao(slug=dados["slug"], nome=base.nome, logo_url=base.logo_url),
        resumo=resumo,
        camadas=lista_camadas,
        fontes=[_fonte(r) for r in linhas["fontes"]],
        paineis=lista_paineis,
        acesso=Matriz(
            perfis=[
                PerfilDaMatriz(id=perfis.ADMIN, rotulo=perfis.rotulo(perfis.ADMIN), fixo=True),
                *(PerfilDaMatriz(id=p.id, rotulo=p.rotulo, fixo=False) for p in perfis.DO_CLIENTE),
            ],
            linhas=[
                LinhaDaMatriz(
                    codigo=painel.codigo,
                    nome=painel.nome,
                    ve={
                        perfis.ADMIN: True,
                        **{p.id: painel.codigo in ve[p.id] for p in perfis.DO_CLIENTE},
                    },
                )
                for painel in no_ar
            ],
        ),
        usuarios=pessoas,
        preferencias=lista_preferencias,
        feriados=[Feriado(data=r["data"], nome=r["nome"]) for r in linhas["feriados"]],
        cargas=[_carga(r, hoje) for r in linhas["cargas"]],
        menu=[GrupoDoMenu(grupo=g, itens=itens) for g, itens in menu.items()],
    )


# ---------- gravações ----------


def _painel(codigo: str) -> Painel:
    painel = POR_CODIGO.get(codigo)
    if painel is None:
        raise NaoEncontrado(MSG_PAINEL)
    return painel


def ligar_painel(conn: Conexao, autor: UUID, cliente_id: UUID, codigo: str, ligado: bool) -> None:
    """Liga ou desliga o painel no cliente. Ligado pela primeira vez, os perfis fixos
    recebem a matriz padrão do painel. Não confirma a transação."""
    painel = _painel(codigo)
    linhas = em_uma_ida(
        conn, {"telas": (SQL_TELAS, (cliente_id,)), "fontes": (SQL_FONTES, (cliente_id,))}
    )
    antes = {r["codigo_tela"]: bool(r["ligada"]) for r in linhas["telas"]}
    if ligado:
        com_carga = frozenset(
            r["tabela"]
            for r in linhas["fontes"]
            if r["tipo"] == "tabela" and r["ativo"] and r["data_snapshot"] is not None
        )
        if not tem_dado(painel, com_carga):
            raise RegraInvalida(MSG_SEM_DADO)
    if antes.get(codigo, False) == ligado:
        return
    conn.execute(SQL_LIGAR, {"cliente": cliente_id, "codigo": codigo, "ligada": ligado})
    if ligado and codigo not in antes:
        conn.execute(
            SQL_LIBERAR,
            {
                "cliente": cliente_id,
                "codigo": codigo,
                "nominal": painel.nominal,
                "perfis": sorted(painel.padrao),
            },
        )
    registrar(
        conn,
        autor,
        "cfg.cliente_tela",
        f"{cliente_id}/{codigo}",
        "update" if codigo in antes else "insert",
        {"ligada": antes[codigo]} if codigo in antes else None,
        {"ligada": ligado},
    )


def mudar_matriz(
    conn: Conexao, autor: UUID, cliente_id: UUID, perfil: str, codigo: str, ve: bool
) -> None:
    """Põe ou tira o painel do perfil fixo do cliente. Não confirma a transação."""
    if perfil == perfis.ADMIN:
        raise RegraInvalida(MSG_ADMIN_FIXO)
    if perfil not in {p.id for p in perfis.DO_CLIENTE}:
        raise NaoEncontrado(MSG_PERFIL)
    painel = _painel(codigo)
    parametros = {"cliente": cliente_id, "codigo": codigo, "perfil": perfil}
    if ve:
        conn.execute(SQL_LIBERAR, {**parametros, "nominal": painel.nominal, "perfis": [perfil]})
    else:
        conn.execute(SQL_TIRAR, parametros)
    registrar(
        conn,
        autor,
        "cfg.perfil_tela",
        f"{cliente_id}/{perfil}/{codigo}",
        "insert" if ve else "delete",
        None,
        {"ve": ve},
    )


def gravar_preferencia(
    conn: Conexao, autor: UUID, cliente_id: UUID, id_: str, valor: str, hoje: date
) -> None:
    """Grava a escolha do cliente (a mesma do seletor da Visão geral). Não confirma."""
    p = preferencias.PREFERENCIAS.get(id_)
    if p is None:
        raise NaoEncontrado(MSG_PREFERENCIA)
    if not p.aceita(valor):
        raise RegraInvalida(MSG_OPCAO)
    preferencias.gravar(conn, cliente_id, autor, p, valor, hoje)


def gravar_feriado(conn: Conexao, autor: UUID, cliente_id: UUID, data: date, nome: str) -> None:
    """Cadastra o feriado do cliente (ou troca o nome). Não confirma a transação."""
    nome = nome.strip()
    conn.execute(SQL_GRAVAR_FERIADO, {"cliente": cliente_id, "data": data, "nome": nome})
    registrar(
        conn,
        autor,
        "cfg.feriado",
        f"{cliente_id}/{data.isoformat()}",
        "insert",
        None,
        {"nome": nome},
    )


def apagar_feriado(conn: Conexao, autor: UUID, cliente_id: UUID, data: date) -> None:
    """Tira o feriado do cliente; 404 se não havia. Não confirma a transação."""
    linha = conn.execute(SQL_APAGAR_FERIADO, {"cliente": cliente_id, "data": data}).fetchone()
    if linha is None:
        raise NaoEncontrado(MSG_FERIADO)
    registrar(
        conn,
        autor,
        "cfg.feriado",
        f"{cliente_id}/{data.isoformat()}",
        "delete",
        {"nome": linha["nome"]},
        None,
    )

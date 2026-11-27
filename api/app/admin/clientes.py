"""Empresas na Gestão: a barra lateral (todas, com o estado das camadas) e as abas de uma
empresa (implantação, unidades, prontidão dos dados, painéis, matriz de acesso, pessoas,
preferências, feriados e cargas), mais as gravações de painel ligado, matriz e feriado.

O Consultor por carteira lê uma versão reduzida (desde 25/11/2026): na barra lateral, só as
empresas dos vínculos dele; de cada uma, só Implantação, Unidades e Prontidão dos dados
(`abas`), sem pessoas, matriz, preferências, feriados nem cargas, e sem gravar nada
(`permissoes`).

"Empresa" na tela é o cfg.cliente. As leituras de um cliente rodam com ele escolhido
(app.cliente_id, pela checagem do admin); a barra lateral lê todos pelas políticas do admin
(migration 27). Nada aqui lê dado de negócio: só cadastro e controle da carga.

Empresa nova (migration 30): a Gestão cria o cfg.cliente com nome e slug, e já dá para
convidar pessoas para ela. Nasce sem unidade e com tudo desligado; unidades (cfg.empresa),
fontes da carga e de-para de contas continuam por script. O nome muda pela tela; o slug não
(está nas URLs), e a empresa não se desativa nem se apaga por aqui. A empresa não tem setor
nem segmento: os dois são de cada unidade (o "Ramo" da empresa saiu na migration 37).

Unidades (migrations 35 e 36): a aba Unidades lista as unidades da empresa (cfg.empresa)
com setor, segmento, grupo e endereço, e o admin edita cada uma (nome, setor, segmento,
grupo e endereço; `mudar_unidade`). Unidade nova e apagar unidade continuam por SQL
(supabase/producao/modelo_unidades.sql): a unidade precisa estar ligada aos dados da carga.
Os setores da empresa são os das unidades, sem repetir, e decidem os painéis por setor
(app/seguranca/setores.py): painel de um setor que nenhuma unidade tem fica com a chave
travada e a nota "Só para empresas com unidade do setor …", e sai da matriz, do menu e das
camadas; ligá-lo dá 422.

Regras:
- painel disponível = alguma fonte ativa que ele exige já teve carga `ok` (catalogo.py);
  painel sem dado não liga (pode desligar);
- camada = seção de painéis do menu (Comercial, Financeiro, Pessoas, Estoque), cada painel
  com o nível (Universal Básico, Universal Completo, Módulo Distribuidora); camada ativa =
  todos os painéis dela ligados e com dado; em implantação = algum ligado; desligada =
  nenhum ligado;
- ao ligar um painel pela primeira vez no cliente, os perfis fixos recebem a matriz padrão;
- a matriz só mexe nos 3 perfis fixos do cliente; o Admin BORD vê tudo o que está ligado;
- linha nova na matriz vê dado nominal só nas telas que exigem (Equipe comercial e Receber
  e pagar); nas que abrem sem nomes e têm a chave "Mostrar nomes" (Pessoas), a matriz tem a
  chave "vê nomes" por perfil (`ve_dado_nominal`), que nasce desligada.
"""

import logging
import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any
from uuid import UUID

from psycopg import errors

from app.admin import textos, usuarios
from app.admin.catalogo import (
    CAMADAS,
    NIVEIS,
    PAINEIS,
    POR_CODIGO,
    Painel,
    tem_dado,
    usada_por,
)
from app.admin.contrato import (
    AbaDaEmpresa,
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
    PainelDaCamada,
    PainelDoCliente,
    PerfilDaGestao,
    PerfilDaMatriz,
    PermissoesEmpresa,
    PermissoesGestao,
    PreferenciaDoCliente,
    Rotulado,
    Status,
    UnidadeDaGestao,
    UsuarioDoCliente,
)
from app.admin.usuarios import NaoEncontrado, RegraInvalida, registrar
from app.core.cache import por_requisicao
from app.core.db import Conexao, Consulta, em_uma_ida
from app.negocios.arvore import Unidade, ordenar
from app.seguranca import perfis, setores
from app.telas.moldura import cliente_da_linha
from app.telas.visao_geral import preferencias

MSG_PAINEL = "Painel não encontrado."
MSG_PERFIL = "Perfil não encontrado."
MSG_ADMIN_FIXO = "Admin BORD vê tudo e não pode ser alterado."
MSG_SEM_NOMES = "Este painel não tem nomes para liberar."
MSG_SEM_PAINEL = "Este perfil não vê o painel."
MSG_SEM_DADO = "Este painel ainda não tem dado nesta empresa."
MSG_PREFERENCIA = "Preferência não encontrada."
MSG_OPCAO = "Opção inválida."
MSG_FERIADO = "Feriado não encontrado."
MSG_NOME = "Informe o nome da empresa."

logger = logging.getLogger(__name__)


class GravacaoBarrada(RuntimeError):
    """O banco não deixou gravar o que a checagem de acesso já tinha liberado (política ou
    grant faltando, em geral migration não aplicada). Vira 500, com o motivo no log."""


MSG_SLUG = "O identificador só aceita letras minúsculas, números e hífen (ex.: pj-cortes)."
MSG_SLUG_EM_USO = "Já existe uma empresa com o identificador {}."
MSG_CRIADA = (
    "Empresa criada. Já dá para convidar as pessoas; os painéis ligam quando a primeira "
    "carga de dados chegar."
)
SLUG = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")

ESTADOS: dict[str, tuple[str, Status]] = {
    "ativa": ("Ativa", "saudavel"),
    "implantacao": ("Em implantação", "atencao"),
    "desligada": ("Desligada", "neutro"),
}
CARGAS_NA_LISTA = 10
OBSERVACAO_MAXIMA = 200

# ---------- consultas ----------

SQL_CLIENTES = "select id, nome, slug from cfg.cliente where ativo order by nome, id"
SQL_SETORES = "select id, rotulo from cfg.setor order by ordem, rotulo"
# os setores das unidades de cada cliente (admin: todos; consultor: os da carteira)
SQL_SETORES_DOS_CLIENTES = """
    select distinct e.cliente_id, e.setor from cfg.empresa e where e.setor is not null
"""
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
SQL_UNIDADES = """
    select id, nome, setor, segmento, grupo, uf, cidade, bairro, endereco
      from cfg.empresa
     where cliente_id = %s
"""
# só os setores das unidades, para a checagem de ligar painel
SQL_SETORES_DO_CLIENTE = """
    select distinct setor from cfg.empresa where cliente_id = %s and setor is not null
"""
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
    select p.perfil, pt.codigo_tela, pt.ve_dado_nominal
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

# sem returning: a linha nova ainda não passa pela política de leitura deste comando
SQL_CRIAR = """
    insert into cfg.cliente (nome, slug)
    values (%(nome)s, %(slug)s)
"""
# uma ida: troca e devolve como estava, para o log
SQL_MUDAR_EMPRESA = """
    update cfg.cliente c
       set nome = %(nome)s
      from (select nome from cfg.cliente where id = %(cliente)s) antes
     where c.id = %(cliente)s
    returning antes.nome as nome_antes
"""
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


# ---------- empresa nova ----------


def slug_do_nome(nome: str) -> str:
    """Identificador a partir do nome: sem acento, minúsculas, o resto vira hífen."""
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", sem_acento.lower()).strip("-")


def criar(
    conn: Conexao,
    autor: UUID,
    *,
    nome: str,
    slug: str | None,
) -> str:
    """Cria a empresa (cfg.cliente), sem unidade, e devolve o slug. Não confirma a
    transação."""
    nome = " ".join(nome.split())
    if not nome:
        raise RegraInvalida(MSG_NOME)
    slug = (slug or "").strip() or slug_do_nome(nome)
    if not SLUG.fullmatch(slug):
        # nome só com símbolos não dá identificador
        raise RegraInvalida(MSG_SLUG)
    try:
        conn.execute(SQL_CRIAR, {"nome": nome, "slug": slug})
    except errors.UniqueViolation as exc:
        raise RegraInvalida(MSG_SLUG_EM_USO.format(slug)) from exc
    registrar(conn, autor, "cfg.cliente", slug, "insert", None, {"nome": nome, "slug": slug})
    return slug


def mudar(conn: Conexao, autor: UUID, cliente_id: UUID, *, nome: str) -> None:
    """Troca o nome da empresa. Não confirma a transação."""
    nome = " ".join(nome.split())
    if not nome:
        raise RegraInvalida(MSG_NOME)
    antes = conn.execute(SQL_MUDAR_EMPRESA, {"cliente": cliente_id, "nome": nome}).fetchone()
    if antes is None:
        # o slug já foi achado na checagem do admin: se o update não devolveu a linha, foi o
        # RLS (ou o grant) que barrou, não a empresa que não existe
        logger.error(
            "UPDATE em cfg.cliente não alcançou a linha do cliente %s: confira se a "
            "migration 31 (política api_site_admin_update e grant de update em nome) está "
            "aplicada neste banco",
            cliente_id,
        )
        raise GravacaoBarrada("update em cfg.cliente barrado pelo banco")
    registrar(
        conn,
        autor,
        "cfg.cliente",
        str(cliente_id),
        "update",
        {"nome": antes["nome_antes"]},
        {"nome": nome},
    )


# ---------- unidade ----------

MSG_UNIDADE = "Unidade não encontrada."
MSG_NOME_UNIDADE = "Informe o nome da unidade."
MSG_UNIDADE_REPETIDA = "Já existe uma unidade com esse nome nesta empresa."
MSG_SETOR_INVALIDO = "Setor inválido."
MSG_UF_INVALIDA = "UF inválida."
# as 27 siglas
UFS = frozenset(
    {
        *("AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA"),
        *("PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO"),
    }
)
CAMPOS_UNIDADE = ("nome", "setor", "segmento", "grupo", "uf", "cidade", "bairro", "endereco")

# uma ida: a unidade como estava (só se for do cliente), se outra do cliente já tem o nome
# (sem diferenciar maiúsculas) e a troca, que só acontece sem conflito
SQL_MUDAR_UNIDADE = """
    with antes as (
        select id, nome, setor, segmento, grupo, uf, cidade, bairro, endereco
          from cfg.empresa
         where id = %(id)s and cliente_id = %(cliente)s
    ),
    conflito as (
        select 1
          from cfg.empresa e
         where e.cliente_id = %(cliente)s
           and e.id <> %(id)s
           and lower(e.nome) = lower(%(nome)s)
    ),
    mudou as (
        update cfg.empresa e
           set nome = %(nome)s, setor = %(setor)s, segmento = %(segmento)s,
               grupo = %(grupo)s, uf = %(uf)s, cidade = %(cidade)s, bairro = %(bairro)s,
               endereco = %(endereco)s
          from antes
         where e.id = antes.id
           and not exists (select 1 from conflito)
        returning e.id
    )
    select (select to_jsonb(antes) - 'id' from antes) as antes,
           exists (select 1 from conflito) as conflito,
           exists (select 1 from mudou) as mudou
"""


def _texto(valor: str | None) -> str | None:
    """Sem espaços nas pontas e repetidos; em branco = None."""
    return " ".join((valor or "").split()) or None


def mudar_unidade(
    conn: Conexao,
    autor: UUID,
    cliente_id: UUID,
    unidade_id: UUID,
    campos: Mapping[str, str | None],
) -> None:
    """Troca nome, setor, segmento, grupo e endereço da unidade do cliente (`campos`, os de
    CAMPOS_UNIDADE). 404 se a unidade não é do cliente; 422 para nome em branco, nome
    repetido no cliente, setor fora de cfg.setor e UF fora das 27. Não confirma a transação."""
    novo = {c: _texto(campos.get(c)) for c in CAMPOS_UNIDADE}
    if novo["nome"] is None:
        raise RegraInvalida(MSG_NOME_UNIDADE)
    if novo["uf"] is not None:
        novo["uf"] = novo["uf"].upper()
        if novo["uf"] not in UFS:
            raise RegraInvalida(MSG_UF_INVALIDA)
    try:
        linha = conn.execute(
            SQL_MUDAR_UNIDADE, {**novo, "id": unidade_id, "cliente": cliente_id}
        ).fetchone()
    except errors.ForeignKeyViolation as exc:  # o único FK que a troca toca é o do setor
        raise RegraInvalida(MSG_SETOR_INVALIDO) from exc
    except errors.UniqueViolation as exc:  # outra gravação levou o nome antes
        raise RegraInvalida(MSG_UNIDADE_REPETIDA) from exc
    if linha is None or linha["antes"] is None:
        raise NaoEncontrado(MSG_UNIDADE)
    if linha["conflito"]:
        raise RegraInvalida(MSG_UNIDADE_REPETIDA)
    if not linha["mudou"]:
        # a unidade foi achada: se o update não a trocou, foi o RLS (ou o grant) que barrou
        logger.error(
            "UPDATE em cfg.empresa não alcançou a unidade %s: confira se a migration 36 "
            "(política api_site_admin_update e grant de update nas colunas) está aplicada "
            "neste banco",
            unidade_id,
        )
        raise GravacaoBarrada("update em cfg.empresa barrado pelo banco")
    registrar(conn, autor, "cfg.empresa", str(unidade_id), "update", linha["antes"], novo)


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


def _rotulado(setor: str | None, rotulos: Mapping[str, str]) -> Rotulado | None:
    if not setor:
        return None
    return Rotulado(id=setor, rotulo=rotulos.get(setor, setor))


def _rotulados(
    setores_da_empresa: frozenset[str], lista: Sequence[Mapping[str, Any]]
) -> list[Rotulado]:
    """Os setores da empresa na ordem de cfg.setor (`lista`, as linhas de SQL_SETORES)."""
    return [
        Rotulado(id=r["id"], rotulo=r["rotulo"]) for r in lista if r["id"] in setores_da_empresa
    ]


def _unidades(
    linhas: Sequence[Mapping[str, Any]], lista: Sequence[Mapping[str, Any]]
) -> list[UnidadeDaGestao]:
    """As unidades da empresa na ordem da árvore da bandeja "Negócios"."""
    rotulos = {r["id"]: r["rotulo"] for r in lista}
    ordem = {r["id"]: i for i, r in enumerate(lista)}
    por_id = {r["id"]: r for r in linhas}
    ordenadas = ordenar(
        (Unidade(id=r["id"], nome=r["nome"], setor=r["setor"], grupo=r["grupo"]) for r in linhas),
        ordem,
    )
    return [
        UnidadeDaGestao(
            id=u.id,
            nome=u.nome,
            setor=_rotulado(u.setor, rotulos),
            segmento=por_id[u.id]["segmento"],
            grupo=u.grupo,
            uf=por_id[u.id]["uf"],
            cidade=por_id[u.id]["cidade"],
            bairro=por_id[u.id]["bairro"],
            endereco=por_id[u.id]["endereco"],
        )
        for u in ordenadas
    ]


def _niveis() -> list[Rotulado]:
    """Os níveis do catálogo, na ordem, inteiros (o front ignora o que a empresa não tem)."""
    return [Rotulado(id=n.id, rotulo=n.rotulo) for n in NIVEIS]


def no_setor(setores_da_empresa: frozenset[str]) -> list[Painel]:
    """Painéis do catálogo que valem nos setores das unidades da empresa, na ordem do
    catálogo."""
    return [p for p in PAINEIS if setores.vale_no_setor(p.setores, setores_da_empresa)]


def camadas(
    ligadas: frozenset[str],
    com_carga: frozenset[str],
    setores_da_empresa: frozenset[str] = frozenset(),
) -> list[CamadaDoCliente]:
    """Seções que existem nos setores da empresa, cada uma só com os painéis deles, na ordem
    do menu. Seção sem nenhum painel desses setores também sai (não teria como ficar
    ativa)."""
    validos = no_setor(setores_da_empresa)
    saida = []
    for camada in CAMADAS:
        da_camada = [p for p in validos if p.camada == camada.id]
        if not da_camada or not setores.vale_no_setor(camada.setores, setores_da_empresa):
            continue
        estado, pendencias = _estado_da_camada(da_camada, ligadas, com_carga)
        rotulo, status = ESTADOS[estado]
        saida.append(
            CamadaDoCliente(
                id=camada.id,
                rotulo=camada.rotulo,
                estado=estado,
                estado_rotulo=rotulo,
                status=status,
                paineis=[
                    PainelDaCamada(codigo=p.codigo, nome=p.nome, nivel=p.nivel) for p in da_camada
                ],
                pendencias=pendencias,
            )
        )
    return saida


def paineis(
    ligadas: frozenset[str],
    com_carga: frozenset[str],
    setores_da_empresa: frozenset[str] = frozenset(),
    rotulos_dos_setores: Mapping[str, str] | None = None,
) -> list[PainelDoCliente]:
    """Todos os painéis do catálogo, por seção e na ordem do menu; o de um setor que nenhuma
    unidade tem vem travado, com a nota do setor."""
    saida = []
    for p in PAINEIS:
        nota: str | None
        if p.setores is not None and not setores.vale_no_setor(p.setores, setores_da_empresa):
            disponivel = False
            nota = setores.nota_fora_do_setor(p.setores, rotulos_dos_setores or {})
        else:
            disponivel = tem_dado(p, com_carga)
            faltam = " ou ".join(f"{prefixo}*" for prefixo in p.exige)
            nota = None if disponivel else f"Sem carga de {faltam} nesta empresa."
        saida.append(
            PainelDoCliente(
                codigo=p.codigo,
                nome=p.nome,
                secao=p.secao,
                nivel=p.nivel,
                ligado=p.codigo in ligadas,
                pode_ligar=disponivel,
                status="saudavel" if disponivel else "neutro",
                status_rotulo="disponível" if disponivel else "indisponível",
                nota=nota,
            )
        )
    return saida


# ---------- barra lateral ----------


def gestao(conn: Conexao, *, consultor: bool = False) -> GestaoPayload:
    """Todas as empresas com o estado das camadas, os 4 perfis e as opções de sessão.

    `consultor`: só as empresas da carteira (o RLS da migration 33 já restringe as três
    leituras) e nada de perfis e sessões, que servem ao convite e à edição de pessoas."""
    consultas: dict[str, Consulta] = {
        "clientes": (SQL_CLIENTES, ()),
        "ligadas": (SQL_LIGADAS, ()),
        "com_carga": (SQL_COM_CARGA, ()),
        "setores": (SQL_SETORES, ()),
        "setores_dos_clientes": (SQL_SETORES_DOS_CLIENTES, ()),
    }
    if not consultor:
        consultas["perfis"] = (SQL_PERFIS, ([p.id for p in perfis.PERFIS],))
    linhas = em_uma_ida(conn, consultas)
    ligadas: dict[UUID, set[str]] = {}
    for r in linhas["ligadas"]:
        ligadas.setdefault(r["cliente_id"], set()).add(r["codigo_tela"])
    com_carga: dict[UUID, set[str]] = {}
    for r in linhas["com_carga"]:
        com_carga.setdefault(r["cliente_id"], set()).add(r["tabela_destino"])
    do_perfil = {r["perfil"]: r for r in linhas.get("perfis", [])}
    setores_de: dict[UUID, set[str]] = {}
    for r in linhas["setores_dos_clientes"]:
        setores_de.setdefault(r["cliente_id"], set()).add(r["setor"])

    return GestaoPayload(
        clientes=[
            ClienteDaLista(
                slug=c["slug"],
                nome=c["nome"],
                setores=_rotulados(frozenset(setores_de.get(c["id"], ())), linhas["setores"]),
                camadas=[
                    CamadaResumo(
                        id=k.id,
                        rotulo=k.rotulo,
                        estado=k.estado,
                        estado_rotulo=k.estado_rotulo,
                        status=k.status,
                    )
                    for k in camadas(
                        frozenset(ligadas.get(c["id"], ())),
                        frozenset(com_carga.get(c["id"], ())),
                        frozenset(setores_de.get(c["id"], ())),
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
            if not consultor
        ],
        sessoes=[
            OpcaoSessao(
                minutos=m, rotulo=textos.sessao(m), so_admin=m > usuarios.SESSAO_MAXIMA_COMUM
            )
            for m in usuarios.SESSOES
            if not consultor
        ],
        permissoes=PermissoesGestao(criar_empresa=not consultor, usuarios=not consultor),
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


ABAS: tuple[AbaDaEmpresa, ...] = (
    AbaDaEmpresa(id="implantacao", rotulo="Implantação"),
    AbaDaEmpresa(id="unidades", rotulo="Unidades"),
    AbaDaEmpresa(id="dados", rotulo="Prontidão dos dados"),
    AbaDaEmpresa(id="paineis", rotulo="Painéis"),
    AbaDaEmpresa(id="preferencias", rotulo="Preferências da empresa"),
    AbaDaEmpresa(id="acesso", rotulo="Usuários e acesso"),
    AbaDaEmpresa(id="cargas", rotulo="Cargas"),
)
# o consultor só lê: o andamento da implantação, as unidades e a prontidão dos dados
ABAS_CONSULTOR = frozenset({"implantacao", "unidades", "dados"})


def _resumo(
    lista_camadas: Sequence[CamadaDoCliente],
    ultima: Mapping[str, Any] | None,
    agora: datetime,
    usuarios_ativos: int | None,
) -> list[Chip]:
    """Chips do topo: camadas ativas, usuários (só para o admin) e a última carga."""
    ativas = sum(1 for k in lista_camadas if k.estado == "ativa")
    chips = [
        Chip(
            rotulo=f"{ativas} de {len(lista_camadas)} camadas ativas",
            status="saudavel" if ativas == len(lista_camadas) else "atencao",
        )
    ]
    if usuarios_ativos is not None:
        plural = "usuário" if usuarios_ativos == 1 else "usuários"
        chips.append(Chip(rotulo=f"{usuarios_ativos} {plural}", status="neutro"))
    chips.append(
        Chip(
            rotulo=f"Última carga: {textos.quando(ultima['inicio'], agora)}"
            if ultima
            else "Sem carga ainda",
            status=_carga(ultima, agora.date()).status if ultima else "critico",
        )
    )
    return chips


def _cliente_consultor(conn: Conexao, cliente_id: UUID) -> ClientePayload:
    """A empresa para o Consultor por carteira: só Implantação, Unidades e Prontidão dos
    dados, sem pessoas, matriz, preferências, feriados nem a lista de cargas, e sem gravar
    nada. As unidades são as dos vínculos dele (o RLS recorta)."""
    agora = textos.agora()
    c = (cliente_id,)
    linhas = em_uma_ida(
        conn,
        {
            "cliente": (SQL_CLIENTE, c),
            "telas": (SQL_TELAS, c),
            "fontes": (SQL_FONTES, c),
            "cargas": (SQL_CARGAS, (cliente_id, 1)),
            "setores": (SQL_SETORES, ()),
            "unidades": (SQL_UNIDADES, c),
        },
    )
    if not linhas["cliente"]:  # a checagem já garantiu o vínculo
        raise LookupError("cliente da Gestão não encontrado")
    dados = linhas["cliente"][0]
    base = cliente_da_linha(dados)
    setores_da_empresa = frozenset(r["setor"] for r in linhas["unidades"] if r["setor"])
    ligadas = frozenset(r["codigo_tela"] for r in linhas["telas"] if r["ligada"])
    com_carga = frozenset(
        r["tabela"]
        for r in linhas["fontes"]
        if r["tipo"] == "tabela" and r["ativo"] and r["data_snapshot"] is not None
    )
    lista_camadas = camadas(ligadas, com_carga, setores_da_empresa)
    return ClientePayload(
        cliente=ClienteDaGestao(
            slug=dados["slug"],
            nome=base.nome,
            setores=_rotulados(setores_da_empresa, linhas["setores"]),
            logo_url=base.logo_url,
        ),
        abas=[a for a in ABAS if a.id in ABAS_CONSULTOR],
        permissoes=PermissoesEmpresa(editar=False, convidar=False, unidades=False),
        resumo=_resumo(
            lista_camadas, linhas["cargas"][0] if linhas["cargas"] else None, agora, None
        ),
        camadas=lista_camadas,
        niveis=_niveis(),
        unidades=_unidades(linhas["unidades"], linhas["setores"]),
        opcoes_setor=[],
        fontes=[_fonte(r) for r in linhas["fontes"]],
        paineis=[],
        acesso=Matriz(perfis=[], linhas=[]),
        usuarios=[],
        preferencias=[],
        feriados=[],
        cargas=[],
        menu=[],
    )


def cliente(
    conn: Conexao, cliente_id: UUID, convite_horas: int, *, consultor: bool = False
) -> ClientePayload:
    """Tudo das abas de uma empresa. O cliente já está escolhido na transação.
    `consultor`: a versão só de leitura (_cliente_consultor)."""
    if consultor:
        return _cliente_consultor(conn, cliente_id)
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
            "setores": (SQL_SETORES, ()),
            "unidades": (SQL_UNIDADES, c),
        },
    )
    if not linhas["cliente"]:  # a checagem do admin já garantiu o cliente
        raise LookupError("cliente da Gestão não encontrado")
    dados = linhas["cliente"][0]
    base = cliente_da_linha(dados)
    rotulos = {r["id"]: r["rotulo"] for r in linhas["setores"]}
    setores_da_empresa = frozenset(r["setor"] for r in linhas["unidades"] if r["setor"])

    ligadas = frozenset(r["codigo_tela"] for r in linhas["telas"] if r["ligada"])
    com_carga = frozenset(
        r["tabela"]
        for r in linhas["fontes"]
        if r["tipo"] == "tabela" and r["ativo"] and r["data_snapshot"] is not None
    )
    lista_camadas = camadas(ligadas, com_carga, setores_da_empresa)
    lista_paineis = paineis(ligadas, com_carga, setores_da_empresa, rotulos)
    # ligados e dos setores da empresa: os da matriz, do menu e do "x de y" de cada pessoa
    no_ar = [p for p in no_setor(setores_da_empresa) if p.codigo in ligadas]

    ve: dict[str, set[str]] = {p.id: set() for p in perfis.DO_CLIENTE}
    ve_nomes: dict[str, set[str]] = {p.id: set() for p in perfis.DO_CLIENTE}
    for r in linhas["matriz"]:
        ve.setdefault(r["perfil"], set()).add(r["codigo_tela"])
        if r["ve_dado_nominal"]:
            ve_nomes.setdefault(r["perfil"], set()).add(r["codigo_tela"])

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

    ativos = sum(1 for u in pessoas if u.situacao.id != "inativo")
    ultima = linhas["cargas"][0] if linhas["cargas"] else None
    resumo = _resumo(lista_camadas, ultima, agora, ativos)

    menu: dict[str, list[ItemDoMenu]] = {}
    for painel in no_ar:
        if painel.codigo in ve[perfis.CLIENTE_PRINCIPAL] and tem_dado(painel, com_carga):
            # na ordem do catálogo (a do menu): Visão, Comercial, Financeiro, Pessoas, Estoque
            menu.setdefault(painel.menu, []).append(ItemDoMenu(nome=painel.nome))

    with por_requisicao():
        lista_preferencias = _preferencias(conn, cliente_id)

    return ClientePayload(
        cliente=ClienteDaGestao(
            slug=dados["slug"],
            nome=base.nome,
            setores=_rotulados(setores_da_empresa, linhas["setores"]),
            logo_url=base.logo_url,
        ),
        abas=list(ABAS),
        permissoes=PermissoesEmpresa(editar=True, convidar=True, unidades=True),
        resumo=resumo,
        camadas=lista_camadas,
        niveis=_niveis(),
        unidades=_unidades(linhas["unidades"], linhas["setores"]),
        opcoes_setor=[Rotulado(id=r["id"], rotulo=r["rotulo"]) for r in linhas["setores"]],
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
                    nomes={
                        perfis.ADMIN: True,
                        **{p.id: painel.codigo in ve_nomes[p.id] for p in perfis.DO_CLIENTE},
                    }
                    if painel.nomes_opcionais
                    else None,
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
    recebem a matriz padrão do painel. Painel de um setor que nenhuma unidade do cliente tem
    não liga (desliga). Não confirma a transação."""
    painel = _painel(codigo)
    consultas: dict[str, Consulta] = {
        "telas": (SQL_TELAS, (cliente_id,)),
        "fontes": (SQL_FONTES, (cliente_id,)),
    }
    confere_setor = ligado and painel.setores is not None
    if confere_setor:
        consultas["setores_do_cliente"] = (SQL_SETORES_DO_CLIENTE, (cliente_id,))
        consultas["setores"] = (SQL_SETORES, ())
    linhas = em_uma_ida(conn, consultas)
    antes = {r["codigo_tela"]: bool(r["ligada"]) for r in linhas["telas"]}
    if confere_setor and painel.setores is not None:
        do_cliente = frozenset(r["setor"] for r in linhas["setores_do_cliente"])
        if not setores.vale_no_setor(painel.setores, do_cliente):
            rotulos = {r["id"]: r["rotulo"] for r in linhas["setores"]}
            raise RegraInvalida(setores.msg_ligar_fora_do_setor(painel.setores, rotulos))
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


# a linha sai e volta com a marca nova: o api_site insere e apaga em cfg.perfil_tela (políticas
# da migration 27), mas não atualiza, e a linha não tem outras colunas a perder
SQL_TIRAR_PARA_TROCAR = """
    delete from cfg.perfil_tela pt
     using cfg.perfil_acesso p
     where p.id = pt.perfil_id
       and p.cliente_id = %(cliente)s
       and p.perfil = %(perfil)s
       and pt.codigo_tela = %(codigo)s
    returning pt.perfil_id, pt.ve_dado_nominal
"""
SQL_POR_DE_VOLTA = """
    insert into cfg.perfil_tela (perfil_id, codigo_tela, ve_dado_nominal)
    values (%(perfil_id)s, %(codigo)s, %(nominal)s)
"""


def mudar_nomes(
    conn: Conexao, autor: UUID, cliente_id: UUID, perfil: str, codigo: str, ve_nomes: bool
) -> None:
    """Liga ou desliga a chave "vê nomes" do perfil fixo no painel. Só nos painéis com nomes
    opcionais e onde o perfil já vê o painel. Não confirma a transação."""
    if perfil == perfis.ADMIN:
        raise RegraInvalida(MSG_ADMIN_FIXO)
    if perfil not in {p.id for p in perfis.DO_CLIENTE}:
        raise NaoEncontrado(MSG_PERFIL)
    if not _painel(codigo).nomes_opcionais:
        raise RegraInvalida(MSG_SEM_NOMES)
    antes = conn.execute(
        SQL_TIRAR_PARA_TROCAR, {"cliente": cliente_id, "perfil": perfil, "codigo": codigo}
    ).fetchone()
    if antes is None:
        raise RegraInvalida(MSG_SEM_PAINEL)
    conn.execute(
        SQL_POR_DE_VOLTA,
        {"perfil_id": antes["perfil_id"], "codigo": codigo, "nominal": ve_nomes},
    )
    registrar(
        conn,
        autor,
        "cfg.perfil_tela",
        f"{cliente_id}/{perfil}/{codigo}",
        "update",
        {"ve_nomes": antes["ve_dado_nominal"]},
        {"ve_nomes": ve_nomes},
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

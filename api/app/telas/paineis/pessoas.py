"""PES-01 — Pessoas. REGRAS PROVISÓRIAS: confirmar com o negócio (ver TODO.md).

Painel restrito (dado de folha): o cadastro libera PES-01 só aos perfis de diretoria e RH.
Não mostra nomes; eles nem saem do banco (a pessoa vira o md5 do nome).

Fontes do cliente, pelo prefixo da tabela (cfg.fonte_carga), com a coluna de cada papel
(padrão abaixo, o da PJ Cortes, trocado por cfg.fonte_carga.colunas_modelo):

- folha (f_folha_*): uma linha por pessoa, mês e lançamento: data, pessoa, setor, cargo,
  lançamento e valor;
- faturado do mês: mart.resumo_mensal das tabelas f_vendas_* (o mesmo da Visão geral).

Regras:
- colaborador: nome distinto (sem diferença de acento, caixa e espaços) com lançamento no
  mês.
  Setor e cargo são os do mês; os que só mudam em acento, caixa ou espaço viram um;
- folha com encargos: todos os lançamentos do mês. Salário: SALÁRIO; ajuda de custo:
  ALIMENTAÇÃO e TRANSPORTE; o resto (FGTS, INSS, 13º, férias, plano de saúde, premiação)
  só entra no total;
- mês da tela: o escolhido ou, sem folha nele, o último com folha antes dele. Janela: os
  12 meses até ele (menos, se a folha começa depois);
- admissão: o primeiro mês do nome na folha; desligamento: o primeiro mês com folha depois
  do último mês do nome (quem some e volta não sai). O primeiro mês da folha não tem mês
  anterior: fica fora das contas de admissão, desligamento e turnover;
- turnover do mês: desligamentos ÷ colaboradores do mês; do período: desligamentos ÷ média
  de colaboradores, com o farol `turnover` (padrão: até 15% em 12 meses, saudável; até 30%,
  atenção), proporcional quando o período tem menos de 12 meses. Setor com média de menos
  de 3 pessoas fica fora das contas de turnover por setor;
- tempo de casa: meses do primeiro mês na folha até o mês da tela. Quem já estava no começo
  da folha tem pelo menos isso: a faixa em que o começo cai vira "Mais de ..." e as
  seguintes somem. Com menos de 1 ano: até 20%, saudável; até 35%, atenção;
- faturamento por colaborador: faturado ÷ colaboradores, contra o primeiro mês da janela
  (alta, saudável; queda de até 5%, atenção; mais, crítico);
- produtividade por setor: só o Comercial, faturado ÷ pessoas do setor, contra a média dos
  6 meses anteriores (mesmo farol).

Fora, sem fonte ou sem regra (ver TODO.md): orçado da folha, entregas, toneladas expedidas,
títulos processados, despesa administrativa sobre a receita e salário por nome.
"""

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from itertools import pairwise
from typing import Any

from psycopg import sql

from app.core.cache import existe
from app.core.db import Conexao, em_uma_ida
from app.farol.parametro import Farol, deslocado, farol
from app.farol.status import Faixa
from app.seguranca.acesso import ContextoConsolidado
from app.telas.contrato import Status
from app.telas.moldura import UltimaCarga
from app.telas.paineis.base import (
    Consulta,
    Conteudo,
    Tipo,
    em_fileiras,
    escolher,
    parametros,
    rotulo,
    selecao,
    slug,
)
from app.telas.paineis.contrato import (
    Aviso,
    Barra,
    BarraHorizontal,
    Cartao,
    CartaoBarrasHorizontais,
    CartaoDestaques,
    CartaoGraficos,
    CartaoListaDetalhe,
    CartaoNumeroGrafico,
    CartaoTabela,
    Celula,
    ColunaTabela,
    DetalheLista,
    Escala,
    GraficoBarras,
    GraficoCartao,
    GraficoEmpilhado,
    GraficoLinhas,
    GrupoBarras,
    ItemDestaque,
    ItemLegenda,
    ItemListaDetalhe,
    LinhaTabela,
    Marcador,
    NumeroDestaque,
    ReferenciaLinha,
    SerieEmpilhada,
    SerieLinha,
    TendenciaDre,
    VariacaoNumero,
    VisaoGraficos,
)
from app.telas.paineis.formato import (
    CEM,
    brl,
    mes_ano,
    moeda_mil,
    nome_mes,
    pct,
    pct_texto,
    q,
)
from app.telas.visao_geral.contra_referencia import MESES_CURTOS, rotulo_eixo
from app.telas.visao_geral.contrato import Kpi, Trecho, Unidade, Valor
from app.telas.visao_geral.texto import moeda, moeda_cheia
from app.telas.visao_geral.vendas import (
    COLUNAS_VENDAS,
    TabelaDoCliente,
    filtro_empresa,
    mes_menos,
    tabelas_do_cliente,
)

COLUNAS_FOLHA: Mapping[str, str] = {
    "data": "Data",
    "pessoa": "NOME",
    "setor": "SETOR",
    "cargo": "CARGO",
    "lancamento": "Lancamento",
    "valor": "Valor",
}
# valores de `lancamento`, em maiúsculas
SALARIO = ("SALÁRIO", "SALARIO")
AJUDA_DE_CUSTO = ("ALIMENTAÇÃO", "ALIMENTACAO", "TRANSPORTE")
# o nome sem acento, para a chave da pessoa (sem depender da extensão unaccent)
COM_ACENTO = "ÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇÑáàâãäéèêëíìîïóòôõöúùûüçñ"
SEM_ACENTO = "AAAAAEEEEIIIIOOOOOUUUUCNaaaaaeeeeiiiiooooouuuucn"

MESES_JANELA = 12
MESES_MEDIA = 6
ITENS_CARGOS = 12
# o empilhado tem 7 cores: do 7º setor em diante, "Outros"
SERIES_EMPILHADO = 7
# tempo de casa, em meses: (de, até, rótulo); até None = sem limite
FAIXAS_CASA: tuple[tuple[int, int | None, str], ...] = (
    (0, 6, "Até 6 meses"),
    (6, 12, "6 meses a 1 ano"),
    (12, 24, "1 a 2 anos"),
    (24, 60, "2 a 5 anos"),
    (60, None, "Mais de 5 anos"),
)
# a faixa em que o começo da folha cai fica aberta: "Mais de ..." pelo início dela
MAIS_DE = {6: "Mais de 6 meses", 12: "Mais de 1 ano", 24: "Mais de 2 anos"}
# % da equipe com menos de 1 ano de casa
NOVOS_ATENCAO = Decimal(20)
NOVOS_CRITICO = Decimal(35)
# queda (%) do faturamento por pessoa que ainda é atenção
QUEDA_ATENCAO = Decimal(-5)
# média de pessoas no período para o setor ter turnover próprio
MINIMO_SETOR = Decimal(3)
# setor (slug) → indicador de produtividade; só os que têm fonte (faturado ÷ pessoas)
INDICADORES_SETOR: Mapping[str, str] = {"comercial": "Faturamento por pessoa"}

AVISO = (
    "Painel restrito: só perfis de diretoria e RH enxergam. Salário por nome só aparece "
    "para o perfil de RH."
)
SEM_SETOR = "Sem setor"
SEM_CARGO = "Sem cargo"
OUTROS = "Outros"
ZERO = Decimal(0)


# ---------- leitura ----------


@dataclass(frozen=True)
class Linha:
    """Uma pessoa num mês."""

    setor: str
    cargo: str
    salario: Decimal
    ajuda: Decimal
    total: Decimal


@dataclass
class Folha:
    # mês → pessoa (md5 do nome) → linha
    meses: dict[date, dict[str, Linha]] = field(default_factory=dict)
    # slug → rótulo
    setores: dict[str, str] = field(default_factory=dict)
    cargos: dict[str, str] = field(default_factory=dict)
    faturado: dict[date, Decimal] = field(default_factory=dict)


CAMPOS_FOLHA: Mapping[str, tuple[str, Tipo]] = {
    "dia": ("data", "data"),
    "pessoa": ("pessoa", "texto"),
    "setor": ("setor", "texto"),
    "cargo": ("cargo", "texto"),
    "lancamento": ("lancamento", "texto"),
    "valor": ("valor", "numero"),
}


def consulta_folha(ctx: ContextoConsolidado, tabelas: list[TabelaDoCliente]) -> Consulta:
    """Uma linha por mês e pessoa (o histórico inteiro: a saída depende dos meses depois)."""
    return sql.SQL(
        """
        select date_trunc('month', dia)::date as mes,
               md5(upper(translate(regexp_replace(pessoa, '\\s+', ' ', 'g'),
                                   %(com_acento)s, %(sem_acento)s))) as pessoa,
               max(setor) as setor, max(cargo) as cargo,
               coalesce(sum(valor) filter (where upper(lancamento) = any(%(salario)s)), 0)
                   as salario,
               coalesce(sum(valor) filter (where upper(lancamento) = any(%(ajuda)s)), 0)
                   as ajuda,
               coalesce(sum(valor), 0) as total
          from ({folha}) t
         where dia is not null and pessoa is not null
         group by 1, 2
        """
    ).format(folha=selecao(tabelas, CAMPOS_FOLHA, filtro_empresa(ctx))), parametros(
        ctx,
        salario=list(SALARIO),
        ajuda=list(AJUDA_DE_CUSTO),
        com_acento=COM_ACENTO,
        sem_acento=SEM_ACENTO,
    )


def consulta_faturado(ctx: ContextoConsolidado, tabelas: list[TabelaDoCliente]) -> Consulta:
    return sql.SQL(
        """
        select mes, sum(valor) as valor
          from mart.resumo_mensal
         where cliente_id = %(cliente)s and tabela = any(%(tabelas)s)
           and medida = 'faturado' and {filtro}
         group by mes
        """
    ).format(filtro=filtro_empresa(ctx, "empresa_id")), parametros(
        ctx, tabelas=[t.tabela for t in tabelas]
    )


def tabelas_folha(conn: Conexao, ctx: ContextoConsolidado) -> list[TabelaDoCliente]:
    """Tabelas f_folha_* com as colunas de mês e pessoa (sem elas, a tabela não serve)."""
    return [
        t
        for t in tabelas_do_cliente(conn, ctx.cliente_id, "f_folha_", COLUNAS_FOLHA)
        if all(t.colunas.get(p) in t.existentes for p in ("data", "pessoa", "valor"))
    ]


def ler(conn: Conexao, ctx: ContextoConsolidado, folha: list[TabelaDoCliente]) -> Folha:
    """A folha e o faturado por mês, numa ida ao banco."""
    consultas = {"folha": consulta_folha(ctx, folha)}
    vendas = tabelas_do_cliente(conn, ctx.cliente_id, "f_vendas_", COLUNAS_VENDAS)
    if vendas and existe(conn, "mart.resumo_mensal"):
        consultas["faturado"] = consulta_faturado(ctx, vendas)
    linhas = em_uma_ida(conn, consultas)
    return folha_das_linhas(linhas["folha"], linhas.get("faturado", []))


def _rotulos(valores: Iterable[str]) -> dict[str, str]:
    """slug → rótulo da grafia mais apresentável ("Logística" antes de "LOGISTICA")."""
    grafias: dict[str, Counter[str]] = {}
    for v in valores:
        grafias.setdefault(slug(v), Counter())[" ".join(v.split())] += 1

    def preferencia(item: tuple[str, int]) -> tuple[bool, bool, int]:
        texto, vezes = item
        return (not texto.isupper(), not texto.isascii(), vezes)

    saida: dict[str, str] = {}
    for chave, contagem in grafias.items():
        texto = max(contagem.items(), key=preferencia)[0]
        # sigla curta fica como está ("PJ", "ADM")
        saida[chave] = texto if texto.isupper() and len(texto) <= 3 else rotulo(texto)
    return saida


def folha_das_linhas(
    linhas: Iterable[Mapping[str, Any]], faturado: Iterable[Mapping[str, Any]] = ()
) -> Folha:
    linhas = list(linhas)
    setores = _rotulos(r["setor"] for r in linhas if r["setor"] and slug(r["setor"]))
    cargos = _rotulos(r["cargo"] for r in linhas if r["cargo"] and slug(r["cargo"]))
    f = Folha(setores={**setores, "": SEM_SETOR}, cargos={**cargos, "": SEM_CARGO})
    for r in linhas:
        f.meses.setdefault(r["mes"], {})[r["pessoa"]] = Linha(
            setor=slug(r["setor"] or ""),
            cargo=slug(r["cargo"] or ""),
            salario=r["salario"] or ZERO,
            ajuda=r["ajuda"] or ZERO,
            total=r["total"] or ZERO,
        )
    f.faturado = {r["mes"]: r["valor"] for r in faturado if r["valor"]}
    return f


# ---------- contas ----------


@dataclass(frozen=True)
class Periodo:
    ref: date
    # primeiro e último mês da folha
    inicio: date
    fim: date
    # até 12 meses até ref, desde o início da folha
    janela: list[date]
    # os da janela com mês anterior na folha: admissão, desligamento e turnover
    movimento: list[date]

    @property
    def texto(self) -> str:
        """ "12 meses", "1 mês" (o período das contas de movimento)."""
        n = len(self.movimento)
        return "1 mês" if n == 1 else f"{n} meses"


def periodo(meses: Iterable[date], ref: date) -> Periodo:
    todos = sorted(meses)
    inicio = todos[0]
    janela = [m for k in range(MESES_JANELA - 1, -1, -1) if (m := mes_menos(ref, k)) >= inicio]
    return Periodo(
        ref=ref,
        inicio=inicio,
        fim=todos[-1],
        janela=janela,
        movimento=[m for m in janela if m > inicio],
    )


@dataclass(frozen=True)
class Movimento:
    """Admissões e desligamentos por mês e setor (o do primeiro e o do último mês)."""

    admissoes: dict[date, Counter[str]]
    desligamentos: dict[date, Counter[str]]

    def de(self, mes: date, setor: str | None = None, *, saidas: bool = False) -> int:
        c = (self.desligamentos if saidas else self.admissoes).get(mes, Counter())
        return c.total() if setor is None else c[setor]


def movimento(folha: Folha) -> Movimento:
    meses = sorted(folha.meses)
    inicio, fim = meses[0], meses[-1]
    seguinte = dict(pairwise(meses))
    primeiro: dict[str, date] = {}
    ultimo: dict[str, date] = {}
    for m in meses:
        for pessoa in folha.meses[m]:
            primeiro.setdefault(pessoa, m)
            ultimo[pessoa] = m
    admissoes: dict[date, Counter[str]] = {}
    desligamentos: dict[date, Counter[str]] = {}
    for pessoa, m in primeiro.items():
        if m > inicio:
            admissoes.setdefault(m, Counter())[folha.meses[m][pessoa].setor] += 1
    for pessoa, m in ultimo.items():
        if m < fim:
            saida = seguinte[m]
            desligamentos.setdefault(saida, Counter())[folha.meses[m][pessoa].setor] += 1
    return Movimento(admissoes, desligamentos)


def pessoas(folha: Folha, mes: date, setor: str | None = None) -> int:
    linhas = folha.meses.get(mes, {}).values()
    return len(linhas) if setor is None else sum(1 for x in linhas if x.setor == setor)


def total_folha(folha: Folha, mes: date, setor: str | None = None) -> Decimal:
    linhas = folha.meses.get(mes, {}).values()
    return sum((x.total for x in linhas if setor is None or x.setor == setor), ZERO)


def turnover(folha: Folha, mov: Movimento, mes: date, setor: str | None = None) -> Decimal | None:
    """Desligamentos do mês ÷ colaboradores do mês, em %."""
    n = pessoas(folha, mes, setor)
    return Decimal(mov.de(mes, setor, saidas=True)) / n * CEM if n else None


@dataclass(frozen=True)
class TurnoverPeriodo:
    admissoes: int
    desligamentos: int
    media_pessoas: Decimal
    # desligamentos ÷ média de pessoas, em %; None sem pessoas
    acumulado: Decimal | None
    # média dos turnovers mensais, em %
    media_mensal: Decimal | None


def turnover_periodo(
    folha: Folha, mov: Movimento, p: Periodo, setor: str | None = None
) -> TurnoverPeriodo:
    meses = p.movimento
    adm = sum(mov.de(m, setor) for m in meses)
    desl = sum(mov.de(m, setor, saidas=True) for m in meses)
    media = Decimal(sum(pessoas(folha, m, setor) for m in meses)) / len(meses) if meses else ZERO
    mensais = [t for m in meses if (t := turnover(folha, mov, m, setor)) is not None]
    return TurnoverPeriodo(
        admissoes=adm,
        desligamentos=desl,
        media_pessoas=media,
        acumulado=Decimal(desl) / media * CEM if media else None,
        media_mensal=sum(mensais, ZERO) / len(mensais) if mensais else None,
    )


def farol_do_periodo(f: Farol, meses: int) -> Farol:
    """O farol de 12 meses levado a um período mais curto (limites proporcionais)."""
    fator = Decimal(meses) / MESES_JANELA
    return Farol(
        faixa=Faixa(
            atencao=f.faixa.atencao * fator,
            critico=f.faixa.critico * fator,
            maior_melhor=f.faixa.maior_melhor,
        ),
        minimo=f.minimo * fator,
        maximo=f.maximo * fator,
        meta=f.meta * fator,
    )


def ordem_setores(folha: Folha, p: Periodo) -> list[str]:
    """Setores com alguém na janela: mais pessoas no mês primeiro."""
    soma: Counter[str] = Counter()
    for m in p.janela:
        soma.update(x.setor for x in folha.meses.get(m, {}).values())
    return sorted(
        soma, key=lambda s: (-pessoas(folha, p.ref, s), -soma[s], folha.setores.get(s, s))
    )


def variacao_status(v: Decimal) -> Status:
    """Variação (%) de um indicador em que maior é melhor."""
    if v >= 0:
        return "saudavel"
    return "atencao" if v >= QUEDA_ATENCAO else "critico"


def por_pessoa(folha: Folha, p: Periodo, setor: str | None = None) -> dict[date, Decimal]:
    """Faturado ÷ pessoas (do setor, se houver), nos meses da janela com os dois."""
    saida: dict[date, Decimal] = {}
    for m in p.janela:
        n = pessoas(folha, m, setor)
        if n and folha.faturado.get(m):
            saida[m] = folha.faturado[m] / n
    return saida


@dataclass(frozen=True)
class FaixaCasa:
    rotulo: str
    pessoas: int
    # a faixa aberta inclui quem já estava no começo da folha
    aberta: bool = False


def meses_entre(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + b.month - a.month


def tempo_de_casa(folha: Folha, p: Periodo) -> list[FaixaCasa]:
    """Pessoas do mês por faixa de tempo de casa; vazio se a folha não cobre 6 meses."""
    primeiro: dict[str, date] = {}
    for m in sorted(folha.meses):
        for pessoa in folha.meses[m]:
            primeiro.setdefault(pessoa, m)
    # quem está desde o começo da folha tem pelo menos `cobertura` meses: a faixa em que
    # ela cai não tem fim conhecido
    cobertura = meses_entre(p.inicio, p.ref)
    faixas: list[tuple[int | None, str]] = []
    for de, ate, nome in FAIXAS_CASA:
        if ate is not None and cobertura >= ate:
            faixas.append((ate, nome))
            continue
        if de == 0:
            return []
        faixas.append((None, nome if ate is None else MAIS_DE[de]))
        break
    contagem: Counter[int] = Counter()
    for pessoa in folha.meses.get(p.ref, {}):
        t = meses_entre(primeiro[pessoa], p.ref)
        contagem[next(i for i, (ate, _) in enumerate(faixas) if ate is None or t < ate)] += 1
    desde_o_inicio = bool(folha.meses.get(p.ref, {}).keys() & folha.meses[p.inicio].keys())
    return [
        FaixaCasa(nome, contagem[i], aberta=ate is None and desde_o_inicio)
        for i, (ate, nome) in enumerate(faixas)
    ]


def com_menos_de_um_ano(faixas: list[FaixaCasa]) -> Decimal | None:
    """% com menos de 12 meses; None se a folha não cobre um ano."""
    total = sum(f.pessoas for f in faixas)
    nomes = {nome for de, ate, nome in FAIXAS_CASA if ate is not None and ate <= 12}
    if not total or not nomes <= {f.rotulo for f in faixas}:
        return None
    return Decimal(sum(f.pessoas for f in faixas if f.rotulo in nomes)) / total * CEM


# ---------- montagem ----------


def montar(
    ctx: ContextoConsolidado,
    escolhido: date | None,
    carga: UltimaCarga,
    pedidos: Mapping[str, str | None],
    a_decidir: bool = False,
) -> Conteudo:
    # nenhum filtro próprio: o setor se escolhe dentro do cartão
    for valor in pedidos.values():
        escolher(None, valor)
    conn = ctx.sessao.conn
    tabelas = tabelas_folha(conn, ctx)
    if not tabelas:
        return Conteudo()
    folha = ler(conn, ctx, tabelas)
    alvo = escolhido or carga.data_snapshot.replace(day=1)
    meses = [m for m in folha.meses if m <= alvo]
    if not meses:
        return Conteudo()
    p = periodo(folha.meses, max(meses))
    return conteudo(
        folha,
        p,
        alvo,
        farol(conn, ctx.cliente_id, "turnover", p.ref),
        farol(conn, ctx.cliente_id, "folha_receita", p.ref),
    )


def conteudo(folha: Folha, p: Periodo, alvo: date, f_turnover: Farol, f_folha: Farol) -> Conteudo:
    mov = movimento(folha)
    f_periodo = farol_do_periodo(f_turnover, len(p.movimento))
    setores = ordem_setores(folha, p)
    # turnover por setor só com gente suficiente (1 saída num setor de 1 pessoa é 100%)
    com_turnover = [
        s for s in setores if turnover_periodo(folha, mov, p, s).media_pessoas >= MINIMO_SETOR
    ]
    casa = tempo_de_casa(folha, p)
    texto_aviso = [Trecho(texto=AVISO)]
    if p.ref < alvo:
        texto_aviso.append(
            Trecho(texto=f" Sem folha em {mes_ano(alvo)}: os números são de {mes_ano(p.ref)}.")
        )
    cartoes: list[Cartao | None] = [
        cartao_mudou(folha, mov, p, com_turnover, casa, f_periodo),
        cartao_faturamento(folha, p),
        cartao_por_setor(folha, p, setores),
        cartao_setor_mes(folha, p, setores),
        cartao_cargos(folha, p),
        cartao_folha_setor(folha, p),
        cartao_admissoes(folha, mov, p, com_turnover),
        cartao_turnover_setor(folha, mov, p, com_turnover, f_periodo),
        cartao_tempo_de_casa(casa, p),
        cartao_produtividade(folha, p, setores),
    ]
    return Conteudo(
        aviso=Aviso(tom="info", texto=texto_aviso),
        kpis=kpis(folha, p, f_folha),
        cartoes=em_fileiras([c for c in cartoes if c is not None]),
    )


def _qtd(n: int | Decimal) -> Valor:
    return Valor(valor=Decimal(n), unidade="qtd")


def _brl_mil(v: Decimal) -> Valor:
    return Valor(valor=q(v, 0), unidade="brl", casas_mil=1)


def _pessoas_texto(n: int) -> str:
    return "1 pessoa" if n == 1 else f"{n} pessoas"


def _saidas_texto(n: int) -> str:
    return "1 saída" if n == 1 else f"{n} saídas"


def _rotulo_setor(folha: Folha, s: str) -> str:
    return folha.setores.get(s, s)


# ---------- KPIs ----------


def kpis(folha: Folha, p: Periodo, f_folha: Farol) -> list[Kpi]:
    ref, base = p.ref, p.janela[0]
    n = pessoas(folha, ref)
    total = total_folha(folha, ref)
    saida = [
        Kpi(
            rotulo="Colaboradores",
            valor=_qtd(n),
            contexto=(
                f"eram {pessoas(folha, base)} em {mes_ano(base)}"
                if base < ref
                else f"em {mes_ano(ref)}"
            ),
        )
    ]
    faturado = folha.faturado.get(ref)
    if faturado:
        sobre = total / faturado * CEM
        anteriores = [
            total_folha(folha, m) / folha.faturado[m] * CEM
            for m in p.janela[:-1]
            if folha.faturado.get(m) and folha.meses.get(m)
        ]
        contexto = f"{pct_texto(sobre)} do faturamento"
        status: Status | None = None
        if anteriores:
            media = sum(anteriores, ZERO) / len(anteriores)
            contexto += f"; média de {len(anteriores)} meses {pct_texto(media)}"
            status = deslocado(f_folha, media).status(sobre)
        saida.append(
            Kpi(rotulo="Folha com encargos", valor=brl(total), contexto=contexto, status=status)
        )
    else:
        saida.append(
            Kpi(rotulo="Folha com encargos", valor=brl(total), contexto=f"em {mes_ano(ref)}")
        )
    serie = por_pessoa(folha, p)
    if ref in serie:
        primeiro = min(serie)
        status = None
        contexto = "por mês"
        if primeiro < ref:
            contexto += f"; {moeda_mil(serie[primeiro])} em {mes_ano(primeiro)}"
            status = variacao_status((serie[ref] / serie[primeiro] - 1) * CEM)
        saida.append(
            Kpi(
                rotulo="Faturamento por colaborador",
                valor=_brl_mil(serie[ref]),
                contexto=contexto,
                status=status,
            )
        )
    if n:
        saida.append(
            Kpi(
                rotulo="Custo médio por pessoa",
                valor=_brl_mil(total / n),
                contexto="salário, encargos, ajuda de custo e premiação",
            )
        )
    return saida


# ---------- cartões ----------


def cartao_mudou(
    folha: Folha,
    mov: Movimento,
    p: Periodo,
    setores: list[str],
    casa: list[FaixaCasa],
    f_periodo: Farol,
) -> CartaoDestaques | None:
    itens: list[ItemDestaque] = []
    if p.movimento:
        # o setor e o mês com mais saídas (empate: o mais recente)
        picos = [(mov.de(m, s, saidas=True), m, s) for m in p.movimento for s in setores]
        n, mes, setor = max(picos, key=lambda x: (x[0], x[1]), default=(0, p.ref, ""))
        if n:
            do_setor = turnover_periodo(folha, mov, p, setor)
            itens.append(
                ItemDestaque(
                    categoria="Saídas",
                    titulo=(
                        f"{_rotulo_setor(folha, setor)} perdeu {_pessoas_texto(n)} em "
                        f"{nome_mes(mes)}"
                    ),
                    texto=(
                        "O maior número de saídas de um setor num mês. Em "
                        f"{p.texto} foram {_saidas_texto(do_setor.desligamentos)} no setor."
                    ),
                    status=(
                        f_periodo.status(do_setor.acumulado)
                        if do_setor.acumulado is not None
                        else "atencao"
                    ),
                )
            )
        else:
            itens.append(
                ItemDestaque(
                    categoria="Saídas",
                    titulo=f"Nenhuma saída em {p.texto}",
                    texto="Ninguém deixou a folha no período.",
                    status="saudavel",
                )
            )
        todos = turnover_periodo(folha, mov, p)
        if todos.acumulado is not None:
            itens.append(
                ItemDestaque(
                    categoria="Rotatividade",
                    titulo=f"{pct_texto(todos.acumulado, 0)} em {p.texto}",
                    texto=(
                        f"Saíram {todos.desligamentos} e entraram {todos.admissoes}, para uma "
                        f"média de {_pessoas_texto(round(todos.media_pessoas))} na equipe."
                    ),
                    status=f_periodo.status(todos.acumulado),
                )
            )
    novos = com_menos_de_um_ano(casa)
    if novos is not None:
        s_novos: Status = (
            "saudavel"
            if novos <= NOVOS_ATENCAO
            else "atencao"
            if novos <= NOVOS_CRITICO
            else "critico"
        )
        itens.append(
            ItemDestaque(
                categoria="Tempo de casa",
                titulo=f"{pct_texto(novos, 0)} com menos de 1 ano",
                texto=(
                    "A maior parte da equipe tem mais de 1 ano de casa."
                    if s_novos == "saudavel"
                    else "Muita gente nova: treinamento e integração pesam."
                ),
                status=s_novos,
            )
        )
    serie = por_pessoa(folha, p)
    if p.ref in serie and min(serie) < p.ref:
        primeiro = min(serie)
        v = (serie[p.ref] / serie[primeiro] - 1) * CEM
        comparacao = (
            "o mesmo que"
            if q(v, 0) == 0
            else f"{pct_texto(abs(v), 0)} {'a mais' if v > 0 else 'a menos'} que"
        )
        itens.append(
            ItemDestaque(
                categoria="Produtividade",
                titulo=f"{moeda_mil(serie[p.ref])} por pessoa",
                texto=f"Cada colaborador gera {comparacao} em {mes_ano(primeiro)}.",
                status=variacao_status(v),
            )
        )
    if not itens:
        return None
    return CartaoDestaques(
        id="mudou-na-equipe",
        tipo="destaques",
        largura=12,
        titulo="O que mudou na equipe",
        subtitulo="Os fatos do mês, para ler em 10 segundos",
        itens=itens,
    )


def _linha_mensal(
    nome: str,
    valores: Mapping[date, Decimal],
    meses: list[date],
    unidade: Unidade,
    casas: int,
    texto: Callable[[Decimal], str],
    *,
    area: bool = False,
    casas_mil: int | None = None,
    piso: Decimal | None = None,
) -> GraficoLinhas:
    """Uma série mês a mês, com a média tracejada e o último valor marcado."""
    serie = [q(valores[m], casas + 1) if m in valores else None for m in meses]
    presentes = [valores[m] for m in meses if m in valores]
    media = sum(presentes, ZERO) / len(presentes)
    ultimo = max(i for i, v in enumerate(serie) if v is not None)
    return GraficoLinhas(
        tipo="linhas",
        unidade=unidade,
        casas=casas if unidade != "brl" else None,
        casas_mil=casas_mil,
        rotulos=[MESES_CURTOS[m.month - 1] for m in meses],
        titulos=[rotulo_eixo(m) for m in meses],
        series=[SerieLinha(nome=nome, valores=serie, estilo="principal", area=area)],
        referencia=ReferenciaLinha(valor=q(media, casas + 1), rotulo=f"média {texto(media)}"),
        marcadores=[Marcador(serie=0, i=ultimo, rotulo=texto(presentes[-1]))],
        piso=piso,
    )


def cartao_faturamento(folha: Folha, p: Periodo) -> CartaoNumeroGrafico | None:
    serie = por_pessoa(folha, p)
    if p.ref not in serie:
        return None
    primeiro = min(serie)
    media = sum(serie.values(), ZERO) / len(serie)
    variacao = None
    nota = f"A média do período é {moeda_mil(media)}."
    if primeiro < p.ref:
        v = (serie[p.ref] / serie[primeiro] - 1) * CEM
        n = meses_entre(primeiro, p.ref) + 1
        variacao = VariacaoNumero(valor=pct(v, 0), texto=f"em {n} meses", tom=variacao_status(v))
        nota = f"Em {mes_ano(primeiro)} eram {moeda_mil(serie[primeiro])}. " + nota
    return CartaoNumeroGrafico(
        id="faturamento-por-colaborador",
        tipo="numero-grafico",
        largura=12,
        titulo="Faturamento por colaborador",
        subtitulo="Quanto de faturamento cada pessoa da equipe gera por mês",
        numero=NumeroDestaque(
            valor=_brl_mil(serie[p.ref]),
            texto=[
                Trecho(texto="é quanto "),
                Trecho(texto="cada colaborador", destaque=True),
                Trecho(texto=" gera de faturamento por mês."),
            ],
            variacao=variacao,
            nota=nota,
        ),
        grafico=_linha_mensal(
            "Por colaborador", serie, p.janela, "brl", 0, moeda_mil, area=True, casas_mil=1
        ),
    )


def cartao_por_setor(folha: Folha, p: Periodo, setores: list[str]) -> CartaoGraficos | None:
    if not setores:
        return None
    proprios = setores if len(setores) <= SERIES_EMPILHADO else setores[: SERIES_EMPILHADO - 1]
    resto = [s for s in setores if s not in proprios]
    series = [
        SerieEmpilhada(
            nome=_rotulo_setor(folha, s),
            valores=[Decimal(pessoas(folha, m, s)) for m in p.janela],
        )
        for s in proprios
    ]
    if resto:
        series.append(
            SerieEmpilhada(
                nome=OUTROS,
                valores=[Decimal(sum(pessoas(folha, m, s) for s in resto)) for m in p.janela],
            )
        )
    return CartaoGraficos(
        id="colaboradores-por-setor",
        tipo="graficos",
        largura=12,
        titulo="Colaboradores por setor",
        subtitulo="Total no topo de cada barra",
        graficos=[
            GraficoCartao(
                grafico=GraficoEmpilhado(
                    tipo="empilhado",
                    unidade="qtd",
                    rotulos=[MESES_CURTOS[m.month - 1] for m in p.janela],
                    rotulos_nas_barras=True,
                    totais=[Decimal(pessoas(folha, m)) for m in p.janela],
                    series=series,
                )
            )
        ],
    )


def cartao_setor_mes(folha: Folha, p: Periodo, setores: list[str]) -> CartaoGraficos | None:
    if not setores:
        return None
    return CartaoGraficos(
        id="setor-mes-a-mes",
        tipo="graficos",
        largura=12,
        colunas=3,
        titulo="Cada setor, mês a mês",
        subtitulo="Escala própria em cada gráfico, para mostrar a variação",
        graficos=[
            GraficoCartao(
                titulo=_rotulo_setor(folha, s),
                grafico=GraficoBarras(
                    tipo="barras",
                    unidade="qtd",
                    rotulos_nas_barras=True,
                    grupos=[
                        GrupoBarras(
                            rotulo=MESES_CURTOS[m.month - 1][0].upper(),
                            titulo=rotulo_eixo(m),
                            barras=[
                                Barra(
                                    nome=_rotulo_setor(folha, s),
                                    valor=Decimal(pessoas(folha, m, s)),
                                    estilo="serie",
                                )
                            ],
                        )
                        for m in p.janela
                    ],
                ),
            )
            for s in setores
        ],
    )


@dataclass
class Cargo:
    pessoas: int = 0
    com_salario: int = 0
    salario: Decimal = ZERO
    ajuda: Decimal = ZERO
    total: Decimal = ZERO
    setores: Counter[str] = field(default_factory=Counter)


def cartao_cargos(folha: Folha, p: Periodo) -> CartaoTabela | None:
    cargos: dict[str, Cargo] = {}
    for x in folha.meses.get(p.ref, {}).values():
        c = cargos.setdefault(x.cargo, Cargo())
        c.pessoas += 1
        c.com_salario += 1 if x.salario > 0 else 0
        c.salario += x.salario
        c.ajuda += x.ajuda
        c.total += x.total
        c.setores[x.setor] += 1
    if not cargos:
        return None
    ordem = sorted(cargos, key=lambda k: (-cargos[k].total, folha.cargos.get(k, k)))
    mostrados, resto = ordem[:ITENS_CARGOS], ordem[ITENS_CARGOS:]

    def linha(k: str) -> LinhaTabela:
        c = cargos[k]
        ajuda = c.ajuda / c.pessoas
        return LinhaTabela(
            celulas=[
                Celula(
                    valor=folha.cargos.get(k, k),
                    sub=_rotulo_setor(folha, c.setores.most_common(1)[0][0]),
                ),
                Celula(valor=_qtd(c.pessoas)),
                Celula(
                    valor=Valor(
                        valor=q(c.salario / c.com_salario if c.com_salario else ZERO, 0),
                        unidade="brl",
                        compacto=False,
                    ),
                    sub=(
                        f"+ {moeda_cheia(ajuda)} de ajuda de custo"
                        if q(ajuda, 0) > 0
                        else "sem ajuda de custo"
                    ),
                ),
                Celula(valor=brl(c.total), negrito=True, sub="com encargos"),
            ]
        )

    nota = None
    if resto:
        n = sum(cargos[k].pessoas for k in resto)
        custo = sum((cargos[k].total for k in resto), ZERO)
        nota = (
            f"Mais {len(resto)} {'cargo' if len(resto) == 1 else 'cargos'}, com "
            f"{_pessoas_texto(n)} e {moeda(custo)} no mês."
        )
    return CartaoTabela(
        id="cargos-salarios",
        tipo="tabela",
        largura=7,
        variante="compacta",
        titulo="Cargos e salários",
        subtitulo=f"Agregado por cargo, sem nomes, em {nome_mes(p.ref)}. Maiores custos primeiro.",
        colunas=[
            ColunaTabela(rotulo="Cargo"),
            ColunaTabela(rotulo="Pessoas"),
            ColunaTabela(rotulo="Salário médio"),
            ColunaTabela(rotulo="Custo mensal"),
        ],
        linhas=[linha(k) for k in mostrados],
        nota=nota,
    )


def cartao_folha_setor(folha: Folha, p: Periodo) -> CartaoBarrasHorizontais | None:
    por_setor: dict[str, tuple[int, Decimal]] = {}
    for x in folha.meses.get(p.ref, {}).values():
        n, total = por_setor.get(x.setor, (0, ZERO))
        por_setor[x.setor] = (n + 1, total + x.total)
    if not por_setor:
        return None
    return CartaoBarrasHorizontais(
        id="folha-por-setor",
        tipo="barras-horizontais",
        largura=5,
        titulo="Folha por setor",
        subtitulo=f"{nome_mes(p.ref).capitalize()}, com encargos, e custo por pessoa",
        escala=Escala(unidade="brl"),
        itens=[
            BarraHorizontal(
                rotulo=_rotulo_setor(folha, s),
                sub=f"{_pessoas_texto(n)} · {moeda_mil(total / n)} por pessoa",
                valor=q(total),
            )
            for s, (n, total) in sorted(por_setor.items(), key=lambda x: -x[1][1])
        ],
    )


def _visao_movimento(
    folha: Folha, mov: Movimento, p: Periodo, setor: str | None
) -> VisaoGraficos | None:
    meses = p.movimento
    t = turnover_periodo(folha, mov, p, setor)
    mensal = {m: v for m in meses if (v := turnover(folha, mov, m, setor)) is not None}
    if not mensal or t.acumulado is None or t.media_mensal is None:
        return None
    nome = "Equipe toda" if setor is None else _rotulo_setor(folha, setor)
    return VisaoGraficos(
        id="todos" if setor is None else setor or "sem-setor",
        rotulo="Todos" if setor is None else nome,
        graficos=[
            GraficoCartao(
                titulo="Admissões e desligamentos",
                grafico=GraficoBarras(
                    tipo="barras",
                    unidade="qtd",
                    rotulos_nas_barras=True,
                    grupos=[
                        GrupoBarras(
                            rotulo=MESES_CURTOS[m.month - 1],
                            titulo=rotulo_eixo(m),
                            barras=[
                                Barra(
                                    nome="Admissões",
                                    valor=Decimal(mov.de(m, setor)),
                                    estilo="serie",
                                ),
                                Barra(
                                    nome="Desligamentos",
                                    valor=Decimal(mov.de(m, setor, saidas=True)),
                                    estilo="serie-clara",
                                ),
                            ],
                        )
                        for m in meses
                    ],
                ),
            ),
            GraficoCartao(
                titulo="Turnover mensal",
                grafico=_linha_mensal("Turnover", mensal, meses, "pct", 1, pct_texto, piso=ZERO),
            ),
        ],
        nota=[
            Trecho(texto=f"{nome}: turnover médio de "),
            Trecho(texto=f"{pct_texto(t.media_mensal)} ao mês", destaque=True),
            Trecho(texto=", "),
            Trecho(texto=f"{pct_texto(t.acumulado, 0)} em {p.texto}", destaque=True),
            Trecho(
                texto=(
                    f". {t.admissoes} {'admissão' if t.admissoes == 1 else 'admissões'} e "
                    f"{t.desligamentos} "
                    f"{'desligamento' if t.desligamentos == 1 else 'desligamentos'} no período."
                )
            ),
        ],
    )


def cartao_admissoes(
    folha: Folha, mov: Movimento, p: Periodo, setores: list[str]
) -> CartaoGraficos | None:
    todos = _visao_movimento(folha, mov, p, None)
    if todos is None:
        return None
    visoes = [todos, *(v for s in setores if (v := _visao_movimento(folha, mov, p, s)))]
    return CartaoGraficos(
        id="admissoes-turnover",
        tipo="graficos",
        largura=12,
        titulo="Admissões, desligamentos e turnover",
        subtitulo="Entrada e saída de nomes na folha e o turnover de cada mês contra a média",
        legenda=[
            ItemLegenda(nome="Admissões", marca="quadrado-escuro"),
            ItemLegenda(nome="Desligamentos", marca="quadrado-claro"),
        ],
        graficos=[],
        rotulo_visoes="Setor",
        visoes=visoes,
    )


def cartao_turnover_setor(
    folha: Folha, mov: Movimento, p: Periodo, setores: list[str], f_periodo: Farol
) -> CartaoBarrasHorizontais | None:
    itens = []
    for s in setores:
        t = turnover_periodo(folha, mov, p, s)
        if t.acumulado is not None:
            itens.append(
                BarraHorizontal(
                    rotulo=_rotulo_setor(folha, s),
                    sub=f"{_saidas_texto(t.desligamentos)} em {p.texto}",
                    valor=q(t.acumulado, 1),
                    status=f_periodo.status(t.acumulado),
                )
            )
    if not itens:
        return None
    return CartaoBarrasHorizontais(
        id="turnover-por-setor",
        tipo="barras-horizontais",
        largura=6,
        titulo="Turnover por setor",
        subtitulo=f"Saídas em {p.texto} sobre a média de pessoas do setor",
        escala=Escala(unidade="pct", casas=0),
        itens=sorted(itens, key=lambda x: -x.valor),
    )


def cartao_tempo_de_casa(casa: list[FaixaCasa], p: Periodo) -> CartaoBarrasHorizontais | None:
    total = sum(f.pessoas for f in casa)
    if not total:
        return None
    inicio = f"{MESES_CURTOS[p.inicio.month - 1]}/{p.inicio.year}"
    return CartaoBarrasHorizontais(
        id="tempo-de-casa",
        tipo="barras-horizontais",
        largura=6,
        titulo="Tempo de casa",
        subtitulo="Quanto tempo cada pessoa está na empresa, pelo primeiro mês na folha",
        escala=Escala(unidade="pct", casas=0),
        itens=[
            BarraHorizontal(
                rotulo=f.rotulo,
                sub=f"inclui quem já estava em {inicio}, o começo da folha" if f.aberta else None,
                valor=q(Decimal(f.pessoas) / total * CEM, 1),
            )
            for f in casa
        ],
    )


def cartao_produtividade(folha: Folha, p: Periodo, setores: list[str]) -> CartaoListaDetalhe | None:
    itens: list[ItemListaDetalhe] = []
    for s in setores:
        indicador = INDICADORES_SETOR.get(s)
        serie = por_pessoa(folha, p, s) if indicador else {}
        if indicador is None or p.ref not in serie:
            continue
        nome = _rotulo_setor(folha, s)
        anteriores = [serie[m] for m in p.janela[-MESES_MEDIA - 1 : -1] if m in serie]
        vs: Celula = Celula()
        if anteriores:
            v = (serie[p.ref] / (sum(anteriores, ZERO) / len(anteriores)) - 1) * CEM
            vs = Celula(valor=pct(v), com_sinal=True, tom=variacao_status(v))
        media = sum(serie.values(), ZERO) / len(serie)
        meses = [m for m in p.janela if m in serie]
        itens.append(
            ItemListaDetalhe(
                id=s,
                celulas=[
                    Celula(valor=nome, negrito=True),
                    Celula(valor=_qtd(pessoas(folha, p.ref, s))),
                    Celula(valor=indicador, tom="apagado"),
                    Celula(valor=brl(serie[p.ref]), negrito=True),
                    vs,
                ],
                tendencia=TendenciaDre(
                    realizado=[q(serie[m]) for m in meses],
                    comparacao=[q(media)] * len(meses),
                ),
                detalhe=DetalheLista(
                    titulo=f"{nome}: {indicador[0].lower()}{indicador[1:]}",
                    grafico=_linha_mensal(indicador, serie, p.janela, "brl", 0, moeda),
                ),
            )
        )
    if not itens:
        return None
    return CartaoListaDetalhe(
        id="produtividade-por-setor",
        tipo="lista-detalhe",
        largura=12,
        titulo="Produtividade por setor",
        subtitulo=(
            f"Um indicador por setor. Clique num setor para ver os {len(p.janela)} meses; o "
            "tracejado é a média."
        ),
        colunas=[
            ColunaTabela(rotulo="Setor"),
            ColunaTabela(rotulo="Pessoas", ocultar_no_celular=True),
            ColunaTabela(rotulo="Indicador", alinhar="esquerda", ocultar_no_celular=True),
            ColunaTabela(rotulo=nome_mes(p.ref).capitalize()),
            ColunaTabela(rotulo=f"vs {MESES_MEDIA}m"),
            ColunaTabela(rotulo=f"{len(p.janela)} meses", ocultar_no_celular=True),
        ],
        itens=itens,
    )

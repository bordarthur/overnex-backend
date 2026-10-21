"""Seção Custos da Visão geral. REGRAS PROVISÓRIAS: confirmar com o negócio (ver TODO.md).

Usa as tabelas de vendas (f_vendas_*): custo = en_precus (custo TOTAL da linha, não o
unitário), faturado = en_VALTOT, CMV = custo ÷ faturado, em %. A abertura é pela coluna do
papel "abertura": no DW padrão, o ramo do cliente (en_ramo); na PJ Cortes, o grupo do
produto (it_grupo). Colunas por papel: ver vendas.py. O nome da abertura nos textos vem de
custos.abertura em cfg.parametro_cliente (padrão: ramo do cliente).

- referência: o CMV dos até 6 meses anteriores (soma do custo ÷ soma do faturado); não há
  orçado por ramo;
- farol: limites em p.p. acima da referência (farol.cmv em cfg.parametro_cliente; padrão
  do mockup: +0,3 atenção, +1,5 crítico, escala de −4 a +6);
- velocímetros: os RAMOS_EM_DESTAQUE ramos com mais faturado no mês e "demais ramos";
- cartões: CMV do mês, margem bruta, lucro bruto e efeito no lucro do mês (diferença de
  CMV contra a referência, aplicada ao faturado projetado em dias úteis).

O histórico por ramo vem de mart.resumo_mensal com dimensão (migration 10); o mês de
referência vem da própria tabela de vendas.
"""

import logging
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from psycopg import sql
from pydantic import BaseModel, ValidationError

from app.core.db import Conexao
from app.farol.parametro import Farol, deslocado, farol
from app.seguranca.acesso import ContextoConsolidado
from app.telas.contrato import Status
from app.telas.moldura import MESES
from app.telas.visao_geral.contra_referencia import MESES_CURTOS, Mensal, Razao, rotulo_eixo
from app.telas.visao_geral.contrato import Faixa as FaixaGauge
from app.telas.visao_geral.contrato import (
    Gauge,
    Indicador,
    IndicadorPeriodo,
    Kpi,
    PeriodosIndicador,
    Secao,
    Tendencia,
    Valor,
)
from app.telas.visao_geral.projecao import projetar_faturado
from app.telas.visao_geral.texto import moeda, numero
from app.telas.visao_geral.vendas import (
    BaseVendas,
    SecaoDoMes,
    TabelaDoCliente,
    de_todas,
    filtro_empresa,
)

logger = logging.getLogger(__name__)

RAMOS_EM_DESTAQUE = 3
MESES_REFERENCIA = 6
MESES_TENDENCIA = 6
INDICADOR = "cmv"
CMV = Razao("custo", "faturado")
# rótulo da carga para linhas com o ramo vazio (carga/resumo.py, SEM_DIMENSAO)
SEM_RAMO = "(vazio)"


class Abertura(BaseModel):
    """Nome da abertura nos textos (custos.abertura em cfg.parametro_cliente)."""

    # "CMV sem ramo", "por ramo do cliente", "CMV dos demais ramos"
    nome: str = "ramo"
    descricao: str = "ramo do cliente"
    plural: str = "ramos"


def abertura(conn: Conexao, cliente_id: UUID) -> Abertura:
    existe = conn.execute(
        "select to_regclass('cfg.parametro_cliente') is not null as ok"
    ).fetchone()
    if not (existe and existe["ok"]):  # migration 8 ainda não aplicada
        return Abertura()
    linha = conn.execute(
        """
        select valor
          from cfg.parametro_cliente
         where cliente_id = %s
           and chave = 'custos.abertura'
           and vigente_de <= current_date
           and (vigente_ate is null or vigente_ate >= current_date)
         order by vigente_de desc
         limit 1
        """,
        (cliente_id,),
    ).fetchone()
    if linha is None:
        return Abertura()
    try:
        return Abertura.model_validate(linha["valor"])
    except ValidationError as exc:
        logger.warning("Parâmetro custos.abertura inválido (cliente %s): %s", cliente_id, exc)
        return Abertura()


def cmv(mensal: Mensal, meses: list[date]) -> Decimal | None:
    """CMV em % dos meses (soma ÷ soma)."""
    razao = CMV.valor(mensal, meses)
    return razao * 100 if razao is not None else None


@dataclass(frozen=True)
class _Referencia:
    valor: Decimal
    # "média de 6 meses" ou "mês anterior"
    nome: str
    artigo: str  # "a" ou "o"
    # "mar a ago"; None com um mês só
    intervalo: str | None = None

    @property
    def com_intervalo(self) -> str:
        """ "a média de 6 meses (mar a ago)" / "o mês anterior"."""
        sufixo = f" ({self.intervalo})" if self.intervalo else ""
        return f"{self.artigo} {self.nome}{sufixo}"

    @property
    def contraido(self) -> str:
        """ "da média de 6 meses" / "do mês anterior"."""
        return f"d{self.artigo} {self.nome}"


def _referencia(mensal: Mensal, ref: date) -> _Referencia | None:
    anteriores = [m for m in sorted(mensal) if m < ref and CMV.tem(mensal, m)][-MESES_REFERENCIA:]
    valor = cmv(mensal, anteriores)
    if valor is None:
        return None
    if len(anteriores) == 1:
        return _Referencia(valor, "mês anterior", "o")
    primeiro, ultimo = MESES_CURTOS[anteriores[0].month - 1], MESES_CURTOS[anteriores[-1].month - 1]
    return _Referencia(valor, f"média de {len(anteriores)} meses", "a", f"{primeiro} a {ultimo}")


def _pct(valor: Decimal) -> str:
    return f"{numero(valor, 1)}%"


def indicador_cmv(id_: str, nome: str, ref: date, mensal: Mensal, base: Farol) -> Indicador | None:
    """Velocímetro de CMV; None sem dado no mês, sem mês anterior ou com tendência curta."""
    meses = sorted(m for m in mensal if m <= ref and CMV.tem(mensal, m))
    if not meses or meses[-1] != ref:
        return None
    tendencia = meses[-MESES_TENDENCIA:]
    referencia = _referencia(mensal, ref)
    valor_mes = cmv(mensal, [ref])
    meses_ano = [m for m in meses if m.year == ref.year]
    valor_ano = cmv(mensal, meses_ano)
    if len(tendencia) < 2 or referencia is None or valor_mes is None or valor_ano is None:
        return None

    f = deslocado(base, referencia.valor)
    contra = f"{referencia.com_intervalo}: {_pct(referencia.valor)}"

    def periodo(valor: Decimal, texto: str) -> IndicadorPeriodo:
        return IndicadorPeriodo(
            status=f.status(valor),
            valor=Valor(valor=valor, unidade="pct", casas=1),
            ponteiro=f.ponteiro(valor),
            delta=Valor(valor=valor - referencia.valor, unidade="pp", casas=1),
            delta_ruim=valor > referencia.valor,
            referencia=texto,
        )

    primeiro = MESES_CURTOS[meses_ano[0].month - 1]
    intervalo = primeiro if len(meses_ano) == 1 else f"{primeiro} a {MESES_CURTOS[ref.month - 1]}"
    return Indicador(
        id=id_,
        nome=nome,
        origem="dw",
        gauge=Gauge(
            min=f.minimo,
            max=f.maximo,
            meta=f.meta,
            unidade="pct",
            faixas=[FaixaGauge(de=de, ate=ate, status=s) for de, ate, s in f.faixas()],
        ),
        periodos=PeriodosIndicador(
            mes=periodo(valor_mes, f"do faturado, contra {contra}"),
            ano=periodo(valor_ano, f"de {intervalo}, contra {contra}"),
        ),
        tendencia=Tendencia(
            rotulos=[rotulo_eixo(m) for m in tendencia],
            real=[cmv(mensal, [m]) or Decimal(0) for m in tendencia],
            referencia=[referencia.valor] * len(tendencia),
            # faixa saudável: do início da escala até o limite de atenção
            faixa=(f.minimo, f.faixa.atencao),
            unidade="pct",
            casas=1,
        ),
    )


def _id(ramo: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", ramo.lower()).strip("-")
    return f"cmv-{slug or 'ramo'}"


def _nome(ramo: str, a: Abertura) -> str:
    return f"CMV sem {a.nome}" if ramo == SEM_RAMO else f"CMV de {ramo.lower()}"


def _ramos_do_mes(
    conn: Conexao, ctx: ContextoConsolidado, tabelas: list[TabelaDoCliente], mes: date
) -> dict[str, dict[str, Decimal]]:
    """Faturado e custo do mês por ramo (abertura), das próprias tabelas de vendas."""
    campos = {"dia": "data", "ramo": "abertura", "valor": "valor", "custo": "custo"}
    return {
        r["ramo"]: {"faturado": r["faturado"], "custo": r["custo"]}
        for r in conn.execute(
            sql.SQL(
                """
                with v as ({vendas})
                select coalesce(nullif(ramo, ''), %(sem_ramo)s) as ramo,
                       coalesce(sum(valor), 0) as faturado,
                       coalesce(sum(custo), 0) as custo
                  from v
                 where dia >= %(mes)s and dia < %(mes)s::date + interval '1 month'
                 group by 1
                """
            ).format(vendas=de_todas(tabelas, campos, filtro_empresa(ctx))),
            {
                "cliente": ctx.cliente_id,
                "empresas": list(ctx.empresas),
                "mes": mes,
                "sem_ramo": SEM_RAMO,
            },
        )
    }


def _historico_por_ramo(
    conn: Conexao, ctx: ContextoConsolidado, tabelas: list[TabelaDoCliente], de: date, ate: date
) -> dict[str, Mensal]:
    """faturado_ramo e custo_ramo do resumo mensal; vazio sem a migration 10."""
    existe = conn.execute(
        """
        select exists (
            select 1 from information_schema.columns
             where table_schema = 'mart' and table_name = 'resumo_mensal'
               and column_name = 'dimensao'
        ) as ok
        """
    ).fetchone()
    if not (existe and existe["ok"]):
        return {}
    medidas = {"faturado_ramo": "faturado", "custo_ramo": "custo"}
    por_ramo: dict[str, Mensal] = {}
    for r in conn.execute(
        sql.SQL(
            """
            select dimensao, mes, medida, sum(valor) as valor
              from mart.resumo_mensal
             where cliente_id = %(cliente)s and tabela = any(%(tabelas)s) and {filtro}
               and medida = any(%(medidas)s) and dimensao <> ''
               and mes between %(de)s and %(ate)s
             group by dimensao, mes, medida
            """
        ).format(filtro=filtro_empresa(ctx, "empresa_id")),
        {
            "cliente": ctx.cliente_id,
            "empresas": list(ctx.empresas),
            "tabelas": [t.tabela for t in tabelas],
            "medidas": list(medidas),
            "de": de,
            "ate": ate,
        },
    ):
        mes = por_ramo.setdefault(r["dimensao"], {}).setdefault(r["mes"], {})
        mes[medidas[r["medida"]]] = r["valor"]
    return por_ramo


def _somar(mensais: list[Mensal]) -> Mensal:
    total: Mensal = {}
    for mensal in mensais:
        for mes, medidas in mensal.items():
            destino = total.setdefault(mes, {})
            for nome, valor in medidas.items():
                destino[nome] = destino.get(nome, Decimal(0)) + valor
    return total


def kpis_custos(
    ref: date,
    ultimo_dia: date,
    faturado: Decimal,
    custo: Decimal,
    mensal: Mensal,
    faturado_projetado: Decimal | None,
    base: Farol,
) -> list[Kpi]:
    """CMV do mês, margem bruta, lucro bruto e efeito no lucro (este só com referência)."""
    if not faturado:
        return []
    cmv_mes = custo / faturado * 100
    referencia = _referencia(mensal, ref)
    f = deslocado(base, referencia.valor) if referencia else None
    status: Status | None = f.status(cmv_mes) if f else None
    ate = ultimo_dia.strftime("%d/%m")

    if referencia is not None:
        diferenca = cmv_mes - referencia.valor
        lado = "acima" if diferenca > 0 else "abaixo"
        sinal = "+" if diferenca > 0 else ""
        contexto_cmv = (
            f"{sinal}{numero(diferenca, 1)} p.p. {lado} {referencia.contraido} "
            f"({_pct(referencia.valor)})"
        )
        contexto_margem = f"{referencia.nome}: {_pct(100 - referencia.valor)}"
    else:
        contexto_cmv = "custo sobre o faturado"
        contexto_margem = "faturado menos custo, sobre o faturado"

    kpis = [
        Kpi(
            rotulo="CMV do mês",
            valor=Valor(valor=cmv_mes, unidade="pct", casas=1),
            contexto=contexto_cmv,
            status=status,
        ),
        Kpi(
            rotulo="Margem bruta",
            valor=Valor(valor=100 - cmv_mes, unidade="pct", casas=1),
            contexto=contexto_margem,
            status=status,
        ),
        Kpi(
            rotulo=f"Lucro bruto até {ate}",
            valor=Valor(valor=faturado - custo, unidade="brl"),
            contexto=f"sobre {moeda(faturado)} de faturado",
        ),
    ]
    if referencia is not None and faturado_projetado is not None:
        efeito = (referencia.valor - cmv_mes) / 100 * faturado_projetado
        kpis.append(
            Kpi(
                rotulo="Efeito no lucro do mês",
                valor=Valor(valor=efeito, unidade="brl"),
                contexto=(
                    "CMV acima da referência, na projeção"
                    if efeito < 0
                    else "CMV abaixo da referência, na projeção"
                ),
                status=status if efeito < 0 else "saudavel",
            )
        )
    return kpis


def secao_custos(ctx: ContextoConsolidado, base: BaseVendas) -> SecaoDoMes | None:
    conn = ctx.sessao.conn
    v = base.mes
    # sem custo no mês (en_precus vazio), CMV de 0% enganaria: a seção não sai
    if not v.faturado or not v.custo:
        return None
    f = farol(conn, ctx.cliente_id, INDICADOR, v.mes)

    a = abertura(conn, ctx.cliente_id)
    do_mes = _ramos_do_mes(conn, ctx, base.tabelas, v.mes)
    historico = _historico_por_ramo(conn, ctx, base.tabelas, base.inicio, v.mes)
    for ramo, medidas in do_mes.items():
        historico.setdefault(ramo, {})[v.mes] = medidas

    destaque = sorted(do_mes, key=lambda r: do_mes[r]["faturado"], reverse=True)
    destaque = [r for r in destaque if r != SEM_RAMO][:RAMOS_EM_DESTAQUE]
    demais = _somar([m for r, m in historico.items() if r not in destaque])

    indicadores = [indicador_cmv(_id(r), _nome(r, a), v.mes, historico[r], f) for r in destaque]
    if demais:
        indicadores.append(
            indicador_cmv("cmv-demais", f"CMV dos demais {a.plural}", v.mes, demais, f)
        )

    nome_mes = MESES[v.mes.month - 1].lower()
    secao = Secao(
        id="custos",
        titulo="Custos",
        modulo=None,
        resumo=f"Dados de {nome_mes} de {v.mes.year}, por {a.descricao}.",
        kpis=kpis_custos(
            v.mes,
            v.ultimo_dia,
            v.faturado,
            v.custo,
            base.mensal,
            projetar_faturado(v.faturado, v.ultimo_dia, base.feriados),
            f,
        ),
        cartoes=[],
        indicadores=[i for i in indicadores if i is not None],
    )
    return SecaoDoMes(secao=secao, mes=v.mes)

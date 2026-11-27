"""Carteira fictícia do painel Estoque (parecida com o mockup M3.1.0: 15 famílias, 38
principais produtos, a Picanha sem estoque; na cauda, produtos zerados, em excesso e
parados) e o demo/estoque.json que sai dela pela regra de verdade
(app/telas/paineis/estoque.py, `montar_conteudo`).

O front não tinha fixture do Estoque: esta é a do back, para o front copiar. O teste
test_paineis_estoque.py confere que o JSON continua batendo com a regra. Mudou a regra ou a
carteira: gere de novo com

    uv run python -m tests.estoque_demo
"""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.telas.moldura import Cliente
from app.telas.paineis.contrato import PainelPayload
from app.telas.paineis.estoque import Produto, montar_conteudo
from app.telas.paineis.explicacoes import com_explicacao
from app.telas.paineis.router import DEMO

# dias ideais de estoque da demo (frigorífico: carne gira rápido); o mínimo é a metade
DIAS_IDEAIS = Decimal(12)
# o dia da carga da demo
DATA = date(2026, 10, 12)

# família, seção, produto, faturamento de 12 meses (R$ mil), margem bruta %, giro (None = sem
# estoque hoje)
CARTEIRA: list[tuple[str, str, str, int, int, float | None]] = [
    ("Cortes nobres", "BOVINOS", "Picanha", 6800, 38, None),
    ("Cortes nobres", "BOVINOS", "Contrafilé", 5200, 36, 5.6),
    ("Cortes nobres", "BOVINOS", "Alcatra", 4600, 34, 4.8),
    ("Cortes nobres", "BOVINOS", "Filé mignon", 4200, 41, 3.1),
    ("Cortes nobres", "BOVINOS", "Maminha", 2100, 35, 3.6),
    ("Cortes dianteiros", "BOVINOS", "Acém", 3900, 27, 6.2),
    ("Cortes dianteiros", "BOVINOS", "Paleta", 3100, 26, 5.4),
    ("Cortes dianteiros", "BOVINOS", "Músculo", 2300, 29, 4.4),
    ("Moídas", "BOVINOS", "Patinho moído", 2600, 31, 7.1),
    ("Moídas", "BOVINOS", "Carne moída de 2ª", 2200, 24, 6.8),
    ("Miúdos", "BOVINOS", "Fígado bovino", 900, 22, 2.1),
    ("Miúdos", "BOVINOS", "Bucho", 700, 25, 1.8),
    ("Cortes suínos", "SUÍNOS", "Pernil", 2700, 30, 4.6),
    ("Cortes suínos", "SUÍNOS", "Lombo", 2400, 33, 3.9),
    ("Cortes suínos", "SUÍNOS", "Costela suína", 1800, 35, 3.2),
    ("Embutidos", "SUÍNOS", "Linguiça toscana", 3300, 37, 5.2),
    ("Embutidos", "SUÍNOS", "Linguiça calabresa", 2500, 39, 4.3),
    ("Embutidos", "SUÍNOS", "Salsicha", 2000, 28, 5.9),
    ("Bacon e defumados", "SUÍNOS", "Bacon manta", 1500, 42, 2.6),
    ("Bacon e defumados", "SUÍNOS", "Bacon fatiado", 1100, 45, 2.2),
    ("Frango inteiro", "AVES", "Frango congelado", 5600, 18, 7.8),
    ("Frango inteiro", "AVES", "Frango resfriado", 3000, 20, 6.5),
    ("Cortes de frango", "AVES", "Peito de frango", 3600, 26, 5.0),
    ("Cortes de frango", "AVES", "Coxa e sobrecoxa", 2900, 24, 5.5),
    ("Cortes de frango", "AVES", "Filé de peito", 2200, 33, 4.1),
    ("Cortes de frango", "AVES", "Asa de frango", 1400, 31, 3.4),
    ("Empanados", "AVES", "Nuggets", 1300, 40, 2.4),
    ("Empanados", "AVES", "Empanado de frango", 900, 38, 2.0),
    ("Frios e laticínios", "MERCEARIA", "Mussarela", 2800, 30, 4.9),
    ("Frios e laticínios", "MERCEARIA", "Presunto", 1700, 34, 3.7),
    ("Frios e laticínios", "MERCEARIA", "Mortadela", 1600, 32, 4.5),
    ("Congelados", "MERCEARIA", "Batata palito", 1900, 36, 3.0),
    ("Congelados", "MERCEARIA", "Hambúrguer", 1200, 29, 2.8),
    ("Pescados", "MERCEARIA", "Filé de tilápia", 1000, 35, 1.9),
    ("Pescados", "MERCEARIA", "Merluza", 800, 30, 1.6),
    ("Temperos e carvão", "MERCEARIA", "Carvão", 950, 41, 4.0),
    ("Temperos e carvão", "MERCEARIA", "Sal grosso", 750, 44, 3.3),
    ("Bebidas", "MERCEARIA", "Refrigerante 2 l", 1000, 22, 6.0),
]
# a cauda: produtos pequenos, fora da curva A (somam ~24% dos principais: os 38 fazem 80%).
# De 9 em 9, um zerado; de 7 em 7, um com excesso (giro 1,2: 25 dias de cobertura)
CAUDA = 46
CAUDA_MIL = 480
# parados: estoque a custo (R$), seção, família e dias sem venda (None: sem venda em 12 meses)
PARADOS: list[tuple[str, str, str, int, int | None]] = [
    ("Charque ponta de agulha", "BOVINOS", "Charques", 212000, 124),
    ("Queijo coalho em barra", "MERCEARIA", "Frios e laticínios", 118000, 92),
    ("Kani-kama", "MERCEARIA", "Pescados", 96000, None),
    ("Paio", "SUÍNOS", "Embutidos", 41000, 150),
]


def _produto(
    codigo: str, familia: str, secao: str, nome: str, mil: int, margem: int, giro: float | None
) -> Produto:
    faturado = Decimal(mil) * 1000
    custo = faturado * (100 - margem) / 100
    estoque = Decimal(0) if giro is None else custo / 12 / Decimal(str(giro))
    return Produto(
        codigo=codigo,
        nome=nome,
        familia=familia,
        secao=secao,
        faturado=faturado,
        custo_vendas=custo,
        estoque=estoque.quantize(Decimal("0.01")),
        dias_ideais=DIAS_IDEAIS,
        ultima_venda=DATA,
    )


def _giro_cauda(i: int) -> float | None:
    if i % 9 == 0:
        return None
    return 1.2 if i % 7 == 0 else 2.5


def produtos() -> list[Produto]:
    lista = [
        _produto(f"{1001 + i}", fam, secao, nome, mil, margem, giro)
        for i, (fam, secao, nome, mil, margem, giro) in enumerate(CARTEIRA)
    ]
    for i in range(CAUDA):
        fam, secao, *_ = CARTEIRA[i % len(CARTEIRA)]
        lista.append(
            _produto(f"{2001 + i}", fam, secao, f"Item {i + 1}", CAUDA_MIL, 30, _giro_cauda(i))
        )
    for i, (nome, secao, fam, estoque, sem_venda) in enumerate(PARADOS):
        lista.append(
            Produto(
                codigo=f"{3001 + i}",
                nome=nome,
                familia=fam,
                secao=secao,
                # vendeu pouco no ano e parou (ou não vendeu nada)
                faturado=Decimal(0) if sem_venda is None else Decimal(9000),
                custo_vendas=Decimal(0) if sem_venda is None else Decimal(6300),
                estoque=Decimal(estoque),
                dias_ideais=DIAS_IDEAIS,
                ultima_venda=None if sem_venda is None else DATA - timedelta(days=sem_venda),
            )
        )
    return lista


def payload(
    secao: str | None = None, classe: str | None = None, lista: str | None = None
) -> PainelPayload:
    carteira = produtos()
    pedidos = {"secao": secao, "classe": classe, "lista": lista}
    conteudo = montar_conteudo(
        carteira, [p.secao for p in carteira if p.secao], pedidos, DATA, DATA
    )
    return PainelPayload(
        tela="estoque",
        versao_contrato="1.0",
        cliente=Cliente(nome="Distribuidora Overnex", sigla="DO", logo_url=None),
        dados_ate=DATA,
        atualizado_em=datetime(2026, 10, 12, 12, 0, tzinfo=UTC),
        frescor="Estoque de hoje, 12/10, atualizado às 9h",
        filtros=conteudo.filtros,
        aviso=conteudo.aviso,
        destaques=[],
        kpis=conteudo.kpis,
        apoios=[],
        cartoes=com_explicacao("estoque", conteudo.cartoes),
    )


def como_json(p: PainelPayload) -> dict[str, object]:
    dados: dict[str, object] = p.model_dump(mode="json", by_alias=True, exclude_defaults=True)
    return dados


if __name__ == "__main__":
    texto = json.dumps(como_json(payload()), ensure_ascii=False, indent=2) + "\n"
    (DEMO / "estoque.json").write_text(texto, "utf-8")
    print("gravado", DEMO / "estoque.json")

"""Textos do botão "i" dos cartões: o que o gráfico mostra e como ler.

Fixos por cartão (não dependem do cliente nem do mês), em texto puro. Vêm das fixtures do
front (campo `explicacao`) e precisam bater com a regra de verdade do cartão: texto que
descreve outra conta fica de fora até ser corrigido (o cartão sai sem o botão).

Fora por enquanto, porque a regra do back é outra (ver TODO.md):
- vendas/fatores-meta: o back decompõe em clientes × venda por cliente, não em três fatores;
- equipe-comercial/destaques-mes: "Precisa de apoio" é o menor atingimento, não quem está
  em queda há 3 meses; e podem sair menos de quatro cartões;
- equipe-comercial/rampa: a curva usa os 5 vendedores atuais com melhor atingimento nos
  últimos 6 meses, não "quem começou bem";
- equipe-comercial/mapa-produtividade: o tamanho da bolha é o valor vendido (R$), não o
  volume;
- equipe-comercial/report-equipe: não é o mesmo report do BI (sem Mix, por enquanto);
- clientes/atendimentos-ticket: cita outubro; o mês muda, e a última barra só é projeção
  com o mês em aberto.
"""

from collections.abc import Mapping

from app.telas.paineis.contrato import Cartao, TelaPainel

EXPLICACOES: Mapping[TelaPainel, Mapping[str, str]] = {
    "vendas": {
        "andamento-mes": (
            "A régua mostra o mês inteiro: vendido, o que falta no ritmo atual e o que falta "
            "para a meta. O gráfico mostra o acumulado por dia útil; as faixas cinzas são os "
            "dias sem venda."
        ),
        "ritmo-dia": (
            "Cada barra é o que foi vendido num dia útil. A linha cinza é a meta diária "
            "original; a amarela é quanto precisa vender por dia daqui para frente para bater "
            "a meta. Se a amarela está acima da cinza, a empresa está atrasada."
        ),
        "canais": (
            "Mostra a projeção de cada canal contra a soma das metas dos vendedores dele. O "
            "traço na barra é 100% da meta."
        ),
    },
    "equipe-comercial": {
        "ranking": (
            "Do maior para o menor atingimento. A barra é a projeção do mês e o traço é a meta "
            "de cada vendedor; por isso as metas aparecem em lugares diferentes."
        ),
        "atingimento-6-meses": (
            "Percentual da meta em cada mês. Azul, amarelo e vermelho seguem o farol. "
            "Vendedores novos são comparados com a curva de rampa, não com a meta cheia."
        ),
    },
    "clientes": {
        "faixas-valor": (
            "Divide os atendimentos pelo valor de cada venda e mostra quanto cada faixa pesa no "
            "movimento e na receita."
        ),
        "curva-abc": (
            "Ordena os itens do que mais vende para o que menos vende. A curva A são os poucos "
            "itens que fazem 80% da receita; a C, os muitos que fazem pouco. Serve para "
            "qualquer negócio: prato, peça, serviço."
        ),
        "cruzamento-abc": (
            "Compara a curva de receita com a curva de margem de cada item. O canto vermelho "
            "são itens que vendem muito e deixam pouco: o primeiro lugar para rever preço ou "
            "custo."
        ),
    },
}


def com_explicacao(tela: TelaPainel, cartoes: list[Cartao]) -> list[Cartao]:
    """Os cartões com o texto do "i" de cada um, quando há texto para o id."""
    textos = EXPLICACOES[tela]
    return [
        c.model_copy(update={"explicacao": textos[c.id]}) if c.id in textos else c for c in cartoes
    ]

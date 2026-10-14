class ErroCarga(Exception):
    """Falha esperada de uma fonte: a mensagem vai para mart.carga_fonte e para o resumo."""


class BancoSemEspaco(ErroCarga):
    """Disco cheio ou banco em modo só leitura: a carga inteira para (não adianta seguir)."""

    DICA = (
        "No Supabase, o banco fica só leitura quando o disco passa de ~95%. Libere espaço "
        "(desative fontes grandes, rode vacuum) ou aumente o disco antes de rodar de novo."
    )

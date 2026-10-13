class AcessoNegado(Exception):
    """Acesso negado por uma das 6 etapas de app.seguranca.acesso.

    O detalhe (etapa e motivo) vai só para seg.log_acesso; a resposta HTTP é genérica.
    """

    def __init__(self, etapa: int, motivo: str) -> None:
        super().__init__(f"etapa {etapa}: {motivo}")
        self.etapa = etapa
        self.motivo = motivo

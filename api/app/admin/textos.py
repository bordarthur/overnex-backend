"""Datas e durações da Gestão, em texto pronto e no horário de Fortaleza."""

from datetime import date, datetime, timedelta, timezone

# Fortaleza não tem horário de verão: UTC−3 fixo (sem depender de tzdata na máquina)
FORTALEZA = timezone(timedelta(hours=-3), "America/Fortaleza")


def agora() -> datetime:
    return datetime.now(FORTALEZA)


def hora(momento: datetime) -> str:
    """9h00, 18h40."""
    local = momento.astimezone(FORTALEZA)
    return f"{local.hour}h{local.minute:02d}"


def dia(data: date, hoje: date) -> str:
    """13/10; com o ano se não for o de hoje: 13/10/2025."""
    return f"{data:%d/%m}" if data.year == hoje.year else f"{data:%d/%m/%Y}"


def quando(momento: datetime, referencia: datetime | None = None) -> str:
    """Ex.: "hoje, 9h14", "ontem, 18h40", "04/11, 10h22"."""
    local = momento.astimezone(FORTALEZA)
    hoje = (referencia or agora()).astimezone(FORTALEZA).date()
    if local.date() == hoje:
        prefixo = "hoje"
    elif local.date() == hoje - timedelta(days=1):
        prefixo = "ontem"
    else:
        prefixo = dia(local.date(), hoje)
    return f"{prefixo}, {hora(local)}"


def duracao(inicio: datetime, fim: datetime | None) -> str | None:
    """Ex.: "2 min 04 s", "58 s", "1 h 05 min"; None se ainda não terminou."""
    if fim is None:
        return None
    segundos = max(int((fim - inicio).total_seconds()), 0)
    if segundos < 60:
        return f"{segundos} s"
    minutos, resto = divmod(segundos, 60)
    if minutos < 60:
        return f"{minutos} min {resto:02d} s"
    horas, minutos = divmod(minutos, 60)
    return f"{horas} h {minutos:02d} min"


def sessao(minutos: int) -> str:
    """30 min, 1 h, 4 h, 24 h."""
    if minutos < 60:
        return f"{minutos} min"
    horas, resto = divmod(minutos, 60)
    return f"{horas} h" if not resto else f"{horas} h {resto:02d} min"

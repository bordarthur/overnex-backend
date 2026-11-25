from datetime import date

import pytest

from demo.config import Config


@pytest.fixture
def ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir("tests")  # longe de um .env em jobs/demo
    monkeypatch.setenv("SUPABASE_URL", "https://projeto.supabase.co/")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_teste")
    monkeypatch.delenv("DATA_SNAPSHOT", raising=False)


@pytest.mark.usefixtures("ambiente")
def test_hoje_no_fuso() -> None:
    c = Config()
    assert c.supabase_url == "https://projeto.supabase.co"
    # precisa achar o fuso também no Windows (pacote tzdata)
    assert isinstance(c.hoje(), date)


@pytest.mark.usefixtures("ambiente")
def test_data_snapshot_manda(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_SNAPSHOT", "2026-11-25")
    assert Config().hoje() == date(2026, 11, 25)

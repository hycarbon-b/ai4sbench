from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_local_production_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    # Explicit smtp_enabled=True test settings still exercise the mocked SMTP runner.
    monkeypatch.setenv("TBCP_SMTP_ENABLED", "false")
    # A developer's production .env must not reject Starlette's testserver host.
    monkeypatch.setenv("TBCP_ALLOWED_HOSTS", "testserver,127.0.0.1,localhost")

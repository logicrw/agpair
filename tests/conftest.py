import os

import pytest


@pytest.fixture(autouse=True)
def isolate_antigravity_cli(monkeypatch, tmp_path):
    if os.environ.get("AGPAIR_ALLOW_REAL_ANTIGRAVITY_CLI_IN_TESTS") == "1":
        return

    fake_agy = tmp_path / "fake-agy"
    fake_agy.write_text("#!/bin/sh\nprintf '{}\\n'\n", encoding="utf-8")
    fake_agy.chmod(0o755)

    monkeypatch.delenv("AGPAIR_ANTIGRAVITY_CLI_BIN", raising=False)
    monkeypatch.delenv("AGPAIR_ANTIGRAVITY_APPROVAL_MODE", raising=False)
    monkeypatch.delenv("AGPAIR_ANTIGRAVITY_PRINT_TIMEOUT", raising=False)
    monkeypatch.setenv("AGPAIR_ANTIGRAVITY_CLI", str(fake_agy))

"""Guarda contra o modo de falha silenciosa desta suite.

Quase todo teste aqui precisa de um Postgres real e se pula sozinho quando nao ha
DSN. Isso e bom na maquina de quem nao subiu banco -- e pessimo no CI, onde uma
suite inteiramente pulada reporta verde sem ter exercitado uma linha de SQL.

`PGTEST_REQUIRED=1` transforma a ausencia de DSN em erro de uso. O CI define essa
variavel; localmente ela nao existe e os testes seguem pulando em silencio.
"""
from __future__ import annotations

import os

import pytest


def _dsn() -> str | None:
    return os.environ.get("PGTEST_DSN") or os.environ.get("RFB_ATOS_DSN")


def pytest_configure(config: pytest.Config) -> None:
    if os.environ.get("PGTEST_REQUIRED") and not _dsn():
        raise pytest.UsageError(
            "PGTEST_REQUIRED esta definido e nao ha PGTEST_DSN nem RFB_ATOS_DSN. "
            "A suite seria pulada inteira e o CI passaria sem testar SQL nenhum."
        )


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    """Com PGTEST_REQUIRED, teste pulado tambem reprova: significa que o banco
    ficou fora do alcance de parte da suite, nao que ela rodou."""
    if not os.environ.get("PGTEST_REQUIRED"):
        return
    pulados = terminalreporter.stats.get("skipped", [])
    if pulados:
        terminalreporter.write_line("")
        terminalreporter.write_line(
            f"ERRO: {len(pulados)} teste(s) pulado(s) com PGTEST_REQUIRED ativo.",
            red=True,
        )
        for rel in pulados:
            terminalreporter.write_line(f"  - {rel.nodeid}")
        pytest.exit("suite incompleta com PGTEST_REQUIRED", returncode=1)

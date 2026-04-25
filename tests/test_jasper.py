"""Tests CLI jasper via typer.testing.CliRunner."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from core.jasper import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def jasper_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirige JASPER_ROOT vers un répertoire temporaire isolé."""
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "inbox.md").write_text("", encoding="utf-8")
    (tmp_path / "stm" / "wiki").mkdir(parents=True)
    (tmp_path / "ltm").mkdir()
    monkeypatch.setenv("JASPER_ROOT", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------- capture


def test_capture_cree_entree(jasper_root: Path) -> None:
    result = runner.invoke(app, ["capture", "idée: tester jasper"])
    assert result.exit_code == 0
    assert "Capturé" in result.output
    content = (jasper_root / "inbox" / "inbox.md").read_text(encoding="utf-8")
    assert "idée: tester jasper" in content
    assert "---" in content  # séparateur horodaté


def test_capture_multiple(jasper_root: Path) -> None:
    runner.invoke(app, ["capture", "première entrée"])
    runner.invoke(app, ["capture", "deuxième entrée"])
    content = (jasper_root / "inbox" / "inbox.md").read_text(encoding="utf-8")
    assert "première entrée" in content
    assert "deuxième entrée" in content


# ---------------------------------------------------------------- ingest


def test_ingest_inbox_vide(jasper_root: Path) -> None:
    result = runner.invoke(app, ["ingest"])
    assert result.exit_code == 0
    assert "vide" in result.output.lower()


def test_ingest_apres_capture(jasper_root: Path) -> None:
    runner.invoke(app, ["capture", "idée: maison: pergola jardin"])
    result = runner.invoke(app, ["ingest"])
    assert result.exit_code == 0
    assert "ingérée" in result.output


def test_ingest_cree_pages_wiki(jasper_root: Path) -> None:
    runner.invoke(app, ["capture", "idée: pergola"])
    runner.invoke(app, ["ingest"])
    wiki = jasper_root / "stm" / "wiki"
    pages = list(wiki.rglob("*.md"))
    assert len(pages) > 0


# ---------------------------------------------------------------- govern


def test_govern_wiki_vide(jasper_root: Path) -> None:
    result = runner.invoke(app, ["govern"])
    assert result.exit_code == 0
    assert "Gouvernance" in result.output


def test_govern_apres_ingest(jasper_root: Path) -> None:
    runner.invoke(app, ["capture", "idée: pergola"])
    runner.invoke(app, ["ingest"])
    result = runner.invoke(app, ["govern"])
    assert result.exit_code == 0


# ---------------------------------------------------------------- ask


def test_ask_retourne_contexte(jasper_root: Path) -> None:
    runner.invoke(app, ["capture", "idée: pergola jardin"])
    runner.invoke(app, ["ingest"])
    result = runner.invoke(app, ["ask", "pergola"])
    assert result.exit_code == 0
    assert "STM" in result.output
    assert "LTM" in result.output


def test_ask_sans_donnees(jasper_root: Path) -> None:
    result = runner.invoke(app, ["ask", "quelque chose"])
    assert result.exit_code == 0


# ---------------------------------------------------------------- status


def test_status_affiche_stats(jasper_root: Path) -> None:
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "STM pages" in result.output
    assert "LTM nœuds" in result.output


def test_status_apres_ingest(jasper_root: Path) -> None:
    runner.invoke(app, ["capture", "idée: maison: pergola"])
    runner.invoke(app, ["ingest"])
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    # Il doit y avoir au moins 1 page
    assert "STM pages" in result.output

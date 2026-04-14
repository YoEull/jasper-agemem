"""Tests unitaires du parseur inbox."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from core.inbox_parser import (
    InboxEntry,
    extract_entities,
    make_callable_client,
    parse_entry,
    parse_inbox,
)


SAMPLE = """\
--- 2026-04-14T22:30:00 ---
idée: maison: pergola jardin côté sud, cèdre rouge,
mesurer largeur mur avant devis menuisier

--- 2026-04-15T08:00:00 ---
tech: essayer ryugraph pour le LTM, remplace kuzu

--- 2026-04-15T09:30:00 ---
note sans tag, juste un rappel
"""


def test_parse_inbox_fichier_absent(tmp_path: Path) -> None:
    assert parse_inbox(tmp_path / "absent.md") == []


def test_parse_inbox_fichier_vide(tmp_path: Path) -> None:
    p = tmp_path / "inbox.md"
    p.write_text("", encoding="utf-8")
    assert parse_inbox(p) == []


def test_parse_inbox_trois_entrees(tmp_path: Path) -> None:
    p = tmp_path / "inbox.md"
    p.write_text(SAMPLE, encoding="utf-8")
    entries = parse_inbox(p)
    assert len(entries) == 3
    assert entries[0].timestamp == datetime(2026, 4, 14, 22, 30)
    assert entries[0].tags == ["idée", "maison"]
    assert "pergola" in entries[0].text
    assert entries[1].tags == ["tech"]
    assert entries[2].tags == []  # pas de tag
    assert entries[2].text.startswith("note sans tag")


def test_parse_entry_simple() -> None:
    raw = "--- 2026-01-01T00:00:00 ---\nidée: test court\n"
    e = parse_entry(raw)
    assert e.timestamp == datetime(2026, 1, 1)
    assert e.tags == ["idée"]
    assert e.text == "test court"


def test_parse_entry_sans_separateur_leve() -> None:
    with pytest.raises(ValueError):
        parse_entry("pas de séparateur ici")


def test_parse_entry_corps_vide_ignore() -> None:
    # Une entrée avec corps vide n'est pas produite.
    raw = "--- 2026-01-01T00:00:00 ---\n\n"
    with pytest.raises(ValueError):
        parse_entry(raw)


def test_tags_multiples_chaines() -> None:
    raw = "--- 2026-01-01T00:00:00 ---\nidée: maison: tech: foo bar\n"
    e = parse_entry(raw)
    assert e.tags == ["idée", "maison", "tech"]
    assert e.text == "foo bar"


def test_tag_trop_long_non_consomme() -> None:
    # Un "mot" de plus de 20 caractères suivi de ':' n'est pas un tag,
    # c'est probablement une phrase (ex. "Note importante: ...").
    raw = (
        "--- 2026-01-01T00:00:00 ---\n"
        "unmottreslonguiseraitabusivement: contenu\n"
    )
    e = parse_entry(raw)
    assert e.tags == []
    assert "unmottreslong" in e.text


def test_multiligne_preserve() -> None:
    raw = (
        "--- 2026-01-01T00:00:00 ---\n"
        "idée: ligne 1\n"
        "ligne 2\n"
        "ligne 3\n"
    )
    e = parse_entry(raw)
    assert e.text.count("\n") == 2
    assert e.tags == ["idée"]


def test_extract_entities_sans_api_ni_client() -> None:
    # Pas d'ANTHROPIC_API_KEY, pas de client : retourne [].
    # On force l'absence de la var d'env via monkeypatch-like check.
    import os

    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        assert extract_entities("pergola cèdre") == []
    finally:
        if saved is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved


def test_extract_entities_avec_client_mock() -> None:
    client = make_callable_client(
        lambda text: ["pergola", "cèdre rouge", "menuisier"]
    )
    result = extract_entities("peu importe le texte", client=client)
    assert result == ["pergola", "cèdre rouge", "menuisier"]


def test_inbox_entry_dataclass_defaults() -> None:
    e = InboxEntry(
        timestamp=datetime(2026, 1, 1), tags=["x"], text="y"
    )
    assert e.entities == []

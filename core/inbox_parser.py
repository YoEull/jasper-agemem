"""
inbox_parser — découpage de l'inbox append-only en entrées structurées.

Format attendu d'une entrée :
    --- 2026-04-14T22:30:00 ---
    idée: maison: pergola jardin côté sud, cèdre rouge...

Exporte :
    InboxEntry       dataclass (timestamp, tags, text, entities)
    parse_inbox      fichier complet -> list[InboxEntry]
    parse_entry      bloc brut -> InboxEntry
    extract_entities texte -> list[str] (via Claude API)
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class InboxEntry:
    timestamp: datetime
    tags: list[str]
    text: str
    entities: list[str]


def parse_inbox(path: Path) -> list["InboxEntry"]:
    raise NotImplementedError


def parse_entry(raw: str) -> "InboxEntry":
    raise NotImplementedError


def extract_entities(text: str) -> list[str]:
    raise NotImplementedError

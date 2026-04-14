"""
inbox_parser — découpage de l'inbox append-only en entrées structurées.

Format attendu d'une entrée :
    --- 2026-04-14T22:30:00 ---
    idée: maison: pergola jardin côté sud, cèdre rouge...

Les tags `mot:` en début d'entrée sont extraits ; le reste forme le
corps `text`. L'extraction d'entités utilise l'API Claude et est
isolée dans `extract_entities` pour faciliter le mock en tests.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

# Séparateur d'entrée : --- ISO8601 ---
_SEP_RE = re.compile(
    r"^---\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\s*---\s*$",
    re.MULTILINE,
)

# Tag en début de ligne/entrée : "mot:" (lettres, chiffres, -, _)
_TAG_RE = re.compile(r"([a-zA-Zà-ÿ0-9_\-]+)\s*:\s*")


@dataclass
class InboxEntry:
    timestamp: datetime
    tags: list[str]
    text: str
    entities: list[str] = field(default_factory=list)


class _ClaudeClient(Protocol):
    """Interface minimale pour l'extraction d'entités (mockable)."""

    def extract(self, text: str) -> list[str]: ...


def parse_inbox(path: Path) -> list[InboxEntry]:
    """Lit l'inbox complète et retourne la liste des entrées.

    Retourne une liste vide si le fichier n'existe pas ou est vide.
    Les entités ne sont pas extraites ici (appel LLM séparé).
    """
    if not path.exists():
        return []
    content = path.read_text(encoding="utf-8")
    return _split_entries(content)


def _split_entries(content: str) -> list[InboxEntry]:
    """Découpe le contenu en entrées selon les séparateurs ISO8601."""
    matches = list(_SEP_RE.finditer(content))
    if not matches:
        return []
    entries: list[InboxEntry] = []
    for i, m in enumerate(matches):
        ts = datetime.fromisoformat(m.group(1))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        body = content[start:end].strip()
        if not body:
            continue
        tags, text = _extract_tags(body)
        entries.append(InboxEntry(timestamp=ts, tags=tags, text=text))
    return entries


def parse_entry(raw: str) -> InboxEntry:
    """Parse un bloc brut (avec son séparateur) en InboxEntry.

    Lève ValueError si aucun séparateur n'est trouvé.
    """
    entries = _split_entries(raw)
    if not entries:
        raise ValueError("Aucune entrée valide trouvée dans le bloc fourni")
    return entries[0]


def _extract_tags(body: str) -> tuple[list[str], str]:
    """Extrait la chaîne de tags `mot:` en tête, retourne (tags, texte).

    Les tags sont consommés tant qu'ils apparaissent collés en début,
    séparés uniquement par espaces. Dès qu'un token n'est plus un tag
    pur (pas de `:` final), on s'arrête et le reste devient le texte.
    """
    tags: list[str] = []
    rest = body
    while True:
        m = _TAG_RE.match(rest)
        if not m:
            break
        # Heuristique : un "tag" est un mot court (< 20 chars) suivi d'un ':'.
        # On évite de manger des phrases entières contenant ':' par erreur.
        token = m.group(1)
        if len(token) > 20:
            break
        tags.append(token.lower())
        rest = rest[m.end():]
    return tags, rest.strip()


def extract_entities(
    text: str,
    client: _ClaudeClient | None = None,
) -> list[str]:
    """Extrait les entités nommées d'un texte via Claude.

    Si `client` est None et qu'aucune clé API n'est configurée, retourne
    une liste vide (mode offline / tests). En production, injecter un
    vrai client Claude.
    """
    if client is not None:
        return client.extract(text)
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return []
    # Import paresseux : ne rien imposer aux tests unitaires.
    from anthropic import Anthropic  # type: ignore[import-not-found]

    anthro = Anthropic(api_key=api_key)
    prompt = (
        "Extrais les entités nommées (personnes, lieux, projets, outils, "
        "concepts) de ce texte. Réponds uniquement par un JSON array de "
        "chaînes, sans commentaire.\n\nTexte :\n" + text
    )
    msg = anthro.messages.create(
        model="claude-opus-4-6",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
    except json.JSONDecodeError:
        pass
    return []


def make_callable_client(fn: Callable[[str], list[str]]) -> _ClaudeClient:
    """Utilitaire : transforme une simple fonction en _ClaudeClient.

    Pratique pour les tests et pour brancher un backend alternatif.
    """

    class _Wrap:
        def extract(self, text: str) -> list[str]:
            return fn(text)

    return _Wrap()

"""
stm_manager — écriture/lecture du wiki STM (pattern Karpathy).

Deux couches :

1. Mécanique (déterministe, testable) :
   - CRUD des pages markdown + frontmatter YAML
   - slugification, backlinks, index.md, log.md
   - expiration (archivage vers `_expired/`)
   - touch (incrément access_count, maj last_accessed)

2. Rédactionnelle (LLM, injectable) :
   - fusion de contenu via `PageWriter` (interface).
   - En absence de writer LLM, fallback "stub" déterministe qui
     initialise la page avec l'entrée brute. Suffisant pour tester
     la mécanique ; en production on injecte un writer Claude.

Voir ARCHITECTURE.md §2.2 et stm/SCHEMA.md pour les invariants.
"""

from __future__ import annotations

import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

import frontmatter  # python-frontmatter
import yaml

from core.inbox_parser import InboxEntry

# --------------------------------------------------------------------- types

PageType = str  # "source" | "entity" | "concept" | "synthesis"


@dataclass
class PageMeta:
    slug: str
    type: PageType
    path: Path
    created_at: date
    last_accessed: date
    access_count: int
    links: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    promoted_to_ltm: bool = False
    sources: list[str] = field(default_factory=list)


class PageWriter(Protocol):
    """Rédacteur LLM pour le contenu d'une page.

    `existing` vaut None lors d'une création, sinon contient le corps
    actuel de la page à enrichir (hors frontmatter).
    """

    def write(
        self,
        slug: str,
        page_type: PageType,
        entry: InboxEntry,
        existing: str | None,
    ) -> str: ...


class _StubWriter:
    """Writer par défaut, déterministe, sans LLM.

    Crée une page minimale avec titre + texte de l'entrée. En présence
    d'un contenu existant, ajoute le nouveau texte comme bullet sous
    une section "Ajouts".
    """

    def write(
        self,
        slug: str,
        page_type: PageType,
        entry: InboxEntry,
        existing: str | None,
    ) -> str:
        titre = slug.replace("-", " ").capitalize()
        if existing is None:
            return f"# {titre}\n\n{entry.text.strip()}\n"
        sep = "\n\n## Ajouts\n"
        if sep in existing:
            return existing.rstrip() + f"\n- {entry.text.strip()}\n"
        return existing.rstrip() + sep + f"- {entry.text.strip()}\n"


# --------------------------------------------------------------------- utils

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """Normalise en slug ASCII minuscule séparé par tirets."""
    norm = unicodedata.normalize("NFKD", text)
    ascii_str = norm.encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_STRIP.sub("-", ascii_str.lower()).strip("-")
    return slug or "sans-titre"


def _today() -> date:
    return datetime.now().date()


# ----------------------------------------------------------------- manager


class STMManager:
    """Gestion de la fenêtre glissante du wiki STM."""

    # Sous-dossiers par type.
    _TYPE_DIRS: dict[PageType, str] = {
        "source": "sources",
        "entity": "entities",
        "concept": "concepts",
        "synthesis": "synthesis",
    }

    def __init__(
        self,
        wiki_dir: Path,
        writer: PageWriter | None = None,
    ) -> None:
        self.wiki_dir = wiki_dir
        self.writer: PageWriter = writer or _StubWriter()
        # Création paresseuse de l'arborescence attendue.
        for sub in self._TYPE_DIRS.values():
            (self.wiki_dir / sub).mkdir(parents=True, exist_ok=True)
        (self.wiki_dir / "_expired").mkdir(exist_ok=True)

    # ------------------------------------------------------------ ingest

    def ingest(self, entry: InboxEntry) -> list[Path]:
        """Crée/met à jour les pages liées à une entrée inbox.

        Crée systématiquement une page `source/`, puis une page
        `concept/` ou `entity/` pour chaque entité listée dans
        `entry.entities`. Met à jour les backlinks symétriques.
        """
        touched: list[Path] = []
        src_slug = self._source_slug(entry)
        src_links = [slugify(e) for e in entry.entities]
        src_path = self._upsert(
            slug=src_slug,
            page_type="source",
            entry=entry,
            extra_links=src_links,
        )
        touched.append(src_path)

        for ent in entry.entities:
            ent_slug = slugify(ent)
            ent_type: PageType = "entity"  # heuristique simple pour MVP
            ent_path = self._upsert(
                slug=ent_slug,
                page_type=ent_type,
                entry=entry,
                extra_links=[src_slug],
            )
            touched.append(ent_path)

        self._log(
            f"ingest {src_slug} "
            f"(+{len(entry.entities)} entités) "
            f"@ {entry.timestamp.isoformat()}"
        )
        self.rebuild_index()
        return touched

    # -------------------------------------------------------------- CRUD

    def read_page(self, slug: str) -> str:
        path = self._find(slug)
        if path is None:
            raise FileNotFoundError(f"Page inconnue : {slug}")
        return path.read_text(encoding="utf-8")

    def touch(self, slug: str) -> None:
        path = self._find(slug)
        if path is None:
            raise FileNotFoundError(f"Page inconnue : {slug}")
        post = frontmatter.load(path)
        post["last_accessed"] = _today().isoformat()
        post["access_count"] = int(post.get("access_count", 0)) + 1
        _dump(post, path)

    def list_pages(self) -> list[PageMeta]:
        pages: list[PageMeta] = []
        for page_type, sub in self._TYPE_DIRS.items():
            for p in sorted((self.wiki_dir / sub).glob("*.md")):
                pages.append(_meta_from_path(p, page_type))
        return pages

    def expire(self, slug: str) -> None:
        path = self._find(slug)
        if path is None:
            raise FileNotFoundError(f"Page inconnue : {slug}")
        dest = self.wiki_dir / "_expired" / path.name
        shutil.move(str(path), str(dest))
        self._log(f"expire {slug}")

    def rebuild_index(self) -> None:
        pages = self.list_pages()
        lines = ["# Index\n"]
        for page_type in ("source", "entity", "concept", "synthesis"):
            subset = [p for p in pages if p.type == page_type]
            if not subset:
                continue
            lines.append(f"\n## {page_type.capitalize()}\n")
            for p in subset:
                promoted = " ⇧" if p.promoted_to_ltm else ""
                lines.append(
                    f"- [[{p.slug}]] — accès:{p.access_count}"
                    f" last:{p.last_accessed.isoformat()}{promoted}"
                )
        (self.wiki_dir / "index.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    # --------------------------------------------------------- internals

    def _source_slug(self, entry: InboxEntry) -> str:
        return "inbox-" + entry.timestamp.strftime("%Y-%m-%d-%H%M")

    def _find(self, slug: str) -> Path | None:
        for sub in self._TYPE_DIRS.values():
            p = self.wiki_dir / sub / f"{slug}.md"
            if p.exists():
                return p
        return None

    def _upsert(
        self,
        slug: str,
        page_type: PageType,
        entry: InboxEntry,
        extra_links: list[str],
    ) -> Path:
        existing = self._find(slug)
        if existing is not None:
            post = frontmatter.load(existing)
            body = self.writer.write(slug, page_type, entry, post.content)
            post.content = body
            links = sorted(set(list(post.get("links", [])) + extra_links))
            post["links"] = [l for l in links if l != slug]
            post["last_accessed"] = _today().isoformat()
            post["access_count"] = int(post.get("access_count", 0)) + 1
            # Tags et sources accumulés.
            tags = sorted(set(list(post.get("tags", [])) + entry.tags))
            post["tags"] = tags
            sources = sorted(
                set(
                    list(post.get("sources", []))
                    + [self._source_slug(entry)]
                )
            )
            post["sources"] = sources
            _dump(post, existing)
            return existing

        # Création
        sub = self._TYPE_DIRS[page_type]
        path = self.wiki_dir / sub / f"{slug}.md"
        body = self.writer.write(slug, page_type, entry, None)
        post = frontmatter.Post(
            content=body,
            id=slug,
            type=page_type,
            created_at=_today().isoformat(),
            last_accessed=_today().isoformat(),
            access_count=1,
            sources=[self._source_slug(entry)],
            links=sorted(set(l for l in extra_links if l != slug)),
            tags=list(entry.tags),
            promoted_to_ltm=False,
        )
        _dump(post, path)
        return path

    def _log(self, message: str) -> None:
        log = self.wiki_dir / "log.md"
        stamp = datetime.now().isoformat(timespec="seconds")
        with log.open("a", encoding="utf-8") as f:
            f.write(f"- {stamp} {message}\n")


# ---------------------------------------------------------------- helpers


def _dump(post: frontmatter.Post, path: Path) -> None:
    """Écrit le post avec un dumper YAML stable (ordre + style).

    python-frontmatter sérialise via pyyaml ; on force default_flow_style
    False pour un rendu humain.
    """
    text = frontmatter.dumps(
        post,
        Dumper=yaml.SafeDumper,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=True,
    )
    path.write_text(text + "\n", encoding="utf-8")


def _meta_from_path(path: Path, page_type: PageType) -> PageMeta:
    post = frontmatter.load(path)
    return PageMeta(
        slug=str(post.get("id", path.stem)),
        type=page_type,
        path=path,
        created_at=_as_date(post.get("created_at")),
        last_accessed=_as_date(post.get("last_accessed")),
        access_count=int(post.get("access_count", 0)),
        links=list(post.get("links", [])),
        tags=list(post.get("tags", [])),
        promoted_to_ltm=bool(post.get("promoted_to_ltm", False)),
        sources=list(post.get("sources", [])),
    )


def _as_date(value) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        return date.fromisoformat(value)
    return _today()

"""
stm_manager — écriture/lecture du wiki STM (pattern Karpathy).

Le LLM est le libraire : cette classe orchestre les appels Claude
pour créer/mettre à jour les pages markdown à frontmatter YAML.

API :
    STMManager(wiki_dir)
        ingest(entry)           -> list[Path]
        read_page(slug)         -> str
        touch(slug)             -> None (incrémente access_count)
        rebuild_index()         -> None
        list_pages()            -> list[PageMeta]
        expire(slug)            -> None  (archive vers _expired/)
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class PageMeta:
    slug: str
    type: str                 # source | entity | concept | synthesis
    path: Path
    created_at: datetime
    last_accessed: datetime
    access_count: int
    links: list[str]
    tags: list[str]
    promoted_to_ltm: bool


class STMManager:
    def __init__(self, wiki_dir: Path) -> None:
        raise NotImplementedError

    def ingest(self, entry) -> list[Path]:
        raise NotImplementedError

    def read_page(self, slug: str) -> str:
        raise NotImplementedError

    def touch(self, slug: str) -> None:
        raise NotImplementedError

    def rebuild_index(self) -> None:
        raise NotImplementedError

    def list_pages(self) -> list[PageMeta]:
        raise NotImplementedError

    def expire(self, slug: str) -> None:
        raise NotImplementedError

"""Tests de STMManager (mécanique uniquement, writer stub par défaut)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import frontmatter

from core.inbox_parser import InboxEntry
from governance.stm_manager import (
    PageMeta,
    STMManager,
    slugify,
)


def _entry(text: str = "pergola", entities=None, tags=None) -> InboxEntry:
    return InboxEntry(
        timestamp=datetime(2026, 4, 14, 22, 30),
        tags=list(tags or ["idée", "maison"]),
        text=text,
        entities=list(entities or ["pergola", "cèdre rouge"]),
    )


# ------------------------------------------------------------ slugify


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Pergola jardin", "pergola-jardin"),
        ("Cèdre Rouge", "cedre-rouge"),
        ("  multiples   espaces  ", "multiples-espaces"),
        ("é*#^!", "e"),
        ("", "sans-titre"),
    ],
)
def test_slugify(raw: str, expected: str) -> None:
    assert slugify(raw) == expected


# ------------------------------------------------------------- ingest


def test_ingest_cree_arborescence(tmp_path: Path) -> None:
    mgr = STMManager(tmp_path)
    for sub in ("sources", "entities", "concepts", "synthesis", "_expired"):
        assert (tmp_path / sub).is_dir()


def test_ingest_cree_pages_source_et_entites(tmp_path: Path) -> None:
    mgr = STMManager(tmp_path)
    paths = mgr.ingest(_entry())
    # 1 source + 2 entités
    assert len(paths) == 3
    src = tmp_path / "sources" / "inbox-2026-04-14-2230.md"
    assert src.exists()
    assert (tmp_path / "entities" / "pergola.md").exists()
    assert (tmp_path / "entities" / "cedre-rouge.md").exists()


def test_frontmatter_page_creee(tmp_path: Path) -> None:
    mgr = STMManager(tmp_path)
    mgr.ingest(_entry())
    post = frontmatter.load(tmp_path / "entities" / "pergola.md")
    assert post["id"] == "pergola"
    assert post["type"] == "entity"
    assert post["access_count"] == 1
    assert "inbox-2026-04-14-2230" in post["sources"]
    assert "idée" in post["tags"]
    # backlink vers la source
    assert "inbox-2026-04-14-2230" in post["links"]
    assert post["promoted_to_ltm"] is False


def test_ingest_reecriture_incremente_access(tmp_path: Path) -> None:
    mgr = STMManager(tmp_path)
    mgr.ingest(_entry(text="première mention"))
    mgr.ingest(
        _entry(text="deuxième mention", entities=["pergola"], tags=["tech"])
    )
    post = frontmatter.load(tmp_path / "entities" / "pergola.md")
    assert post["access_count"] == 2
    # tags et sources agrégés
    assert set(post["tags"]) >= {"idée", "maison", "tech"}
    # contenu enrichi par le stub writer (section "Ajouts")
    assert "Ajouts" in post.content
    assert "deuxième mention" in post.content


def test_index_genere(tmp_path: Path) -> None:
    mgr = STMManager(tmp_path)
    mgr.ingest(_entry())
    idx = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert "## Source" in idx
    assert "## Entity" in idx
    assert "[[pergola]]" in idx
    assert "[[cedre-rouge]]" in idx


def test_log_append(tmp_path: Path) -> None:
    mgr = STMManager(tmp_path)
    mgr.ingest(_entry())
    mgr.ingest(_entry(text="autre"))
    log = (tmp_path / "log.md").read_text(encoding="utf-8")
    assert log.count("ingest inbox-") == 2


# ------------------------------------------------------- CRUD additionnels


def test_read_page_ok_et_inconnue(tmp_path: Path) -> None:
    mgr = STMManager(tmp_path)
    mgr.ingest(_entry())
    content = mgr.read_page("pergola")
    assert "Pergola" in content
    with pytest.raises(FileNotFoundError):
        mgr.read_page("inexistante")


def test_touch_incremente(tmp_path: Path) -> None:
    mgr = STMManager(tmp_path)
    mgr.ingest(_entry())
    mgr.touch("pergola")
    post = frontmatter.load(tmp_path / "entities" / "pergola.md")
    assert post["access_count"] == 2


def test_touch_inconnue_leve(tmp_path: Path) -> None:
    mgr = STMManager(tmp_path)
    with pytest.raises(FileNotFoundError):
        mgr.touch("absent")


def test_list_pages_retourne_metas(tmp_path: Path) -> None:
    mgr = STMManager(tmp_path)
    mgr.ingest(_entry())
    pages = mgr.list_pages()
    slugs = {p.slug for p in pages}
    assert {"pergola", "cedre-rouge", "inbox-2026-04-14-2230"} <= slugs
    assert all(isinstance(p, PageMeta) for p in pages)


def test_expire_deplace_vers_expired(tmp_path: Path) -> None:
    mgr = STMManager(tmp_path)
    mgr.ingest(_entry())
    mgr.expire("pergola")
    assert not (tmp_path / "entities" / "pergola.md").exists()
    assert (tmp_path / "_expired" / "pergola.md").exists()


def test_expire_inconnue_leve(tmp_path: Path) -> None:
    mgr = STMManager(tmp_path)
    with pytest.raises(FileNotFoundError):
        mgr.expire("absent")


# ------------------------------------------------------------ writer injecté


def test_writer_injecte_utilise(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []

    class _W:
        def write(self, slug, page_type, entry, existing):
            calls.append((slug, page_type))
            return f"contenu-custom de {slug}"

    mgr = STMManager(tmp_path, writer=_W())
    mgr.ingest(_entry(entities=["pergola"]))
    content = (tmp_path / "entities" / "pergola.md").read_text(
        encoding="utf-8"
    )
    assert "contenu-custom de pergola" in content
    assert ("pergola", "entity") in calls
    assert ("inbox-2026-04-14-2230", "source") in calls

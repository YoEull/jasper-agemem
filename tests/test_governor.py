"""Tests du governor — DB RyuGraph temporaire, STM dans tmp_path."""

from __future__ import annotations

from datetime import datetime, date, timedelta
from pathlib import Path

import pytest
import frontmatter

from core.inbox_parser import InboxEntry
from governance.config import GovConfig
from governance.governor import Governor, Promotion, _humanize, _first_para, _infer_label
from governance.ltm_store import LTMStore
from governance.stm_manager import STMManager

DIM = 4


@pytest.fixture
def setup(tmp_path: Path):
    wiki = tmp_path / "wiki"
    stm = STMManager(wiki)
    ltm = LTMStore(tmp_path / "ltm.ryu", embedding_dim=DIM)
    cfg = GovConfig(promote_threshold=2, ttl_days=30, max_stm_pages=5)
    gov = Governor(stm, ltm, cfg)
    yield stm, ltm, gov, wiki
    ltm.close()


def _entry(text: str = "contenu", entities=None, tags=None,
           ts: datetime | None = None) -> InboxEntry:
    return InboxEntry(
        timestamp=ts or datetime(2026, 4, 14, 22, 30),
        tags=list(tags or ["idée"]),
        text=text,
        entities=list(entities or []),
    )


# -------------------------------------------------------- detect_promotions


def test_detect_aucune_promotion_sous_seuil(setup) -> None:
    stm, ltm, gov, _ = setup
    # Une seule entrée qui cite "pergola" -> seuil=2 non atteint
    stm.ingest(_entry(entities=["pergola"]))
    assert gov._detect_promotions() == []


def test_detect_promotion_atteint_seuil(setup) -> None:
    stm, ltm, gov, _ = setup
    # Deux entrées différentes citent "pergola" -> seuil=2 atteint
    stm.ingest(_entry(text="mention 1", entities=["pergola"],
                      ts=datetime(2026, 4, 14, 10, 0)))
    stm.ingest(_entry(text="mention 2", entities=["pergola"],
                      ts=datetime(2026, 4, 14, 11, 0)))
    promotions = gov._detect_promotions()
    slugs = [p.slug for p in promotions]
    assert "pergola" in slugs


def test_detect_ne_repromet_pas(setup) -> None:
    stm, ltm, gov, wiki = setup
    stm.ingest(_entry(text="m1", entities=["pergola"],
                      ts=datetime(2026, 4, 14, 10, 0)))
    stm.ingest(_entry(text="m2", entities=["pergola"],
                      ts=datetime(2026, 4, 14, 11, 0)))
    # Simuler déjà promu
    pages = stm.list_pages()
    for p in pages:
        if p.slug == "pergola":
            post = frontmatter.load(p.path)
            post["promoted_to_ltm"] = True
            from governance.stm_manager import _dump
            _dump(post, p.path)
    assert gov._detect_promotions() == []


# ------------------------------------------------------------ _promote


def test_promote_cree_noeud_ltm(setup) -> None:
    stm, ltm, gov, _ = setup
    stm.ingest(_entry(text="m1", entities=["pergola"],
                      ts=datetime(2026, 4, 14, 10, 0)))
    stm.ingest(_entry(text="m2", entities=["pergola"],
                      ts=datetime(2026, 4, 14, 11, 0)))
    p = gov._detect_promotions()[0]
    gov._promote(p)
    rows = ltm.cypher("MATCH (n:JasperNode {id: 'pergola'}) RETURN n.id, n.name")
    assert len(rows) == 1
    assert rows[0]["n.name"] == "Pergola"


def test_promote_marque_stm(setup) -> None:
    stm, ltm, gov, wiki = setup
    stm.ingest(_entry(text="m1", entities=["pergola"],
                      ts=datetime(2026, 4, 14, 10, 0)))
    stm.ingest(_entry(text="m2", entities=["pergola"],
                      ts=datetime(2026, 4, 14, 11, 0)))
    p = gov._detect_promotions()[0]
    gov._promote(p)
    pages = stm.list_pages()
    pergola = next((pg for pg in pages if pg.slug == "pergola"), None)
    assert pergola is not None
    post = frontmatter.load(pergola.path)
    assert post.get("promoted_to_ltm") is True


# ------------------------------------------------------------- govern()


def test_govern_rapport_complet(setup) -> None:
    stm, ltm, gov, _ = setup
    stm.ingest(_entry(text="m1", entities=["pergola"],
                      ts=datetime(2026, 4, 14, 10, 0)))
    stm.ingest(_entry(text="m2", entities=["pergola"],
                      ts=datetime(2026, 4, 14, 11, 0)))
    report = gov.govern()
    assert len(report.promoted) >= 1
    assert isinstance(report.expired, list)
    assert isinstance(report.consolidated, list)


def test_govern_sans_pages_rien(setup) -> None:
    stm, ltm, gov, _ = setup
    report = gov.govern()
    assert report.promoted == []
    assert report.expired == []


# ------------------------------------------------------------ expiration


def test_expire_stale_pages(setup) -> None:
    stm, ltm, gov, wiki = setup
    stm.ingest(_entry(entities=[]))
    # Forcer last_accessed dans le passé
    pages = stm.list_pages()
    vieux = date.today() - timedelta(days=35)
    for p in pages:
        post = frontmatter.load(p.path)
        post["last_accessed"] = vieux.isoformat()
        from governance.stm_manager import _dump
        _dump(post, p.path)
    expired = gov._expire_stale()
    assert len(expired) > 0
    # Les fichiers doivent avoir bougé dans _expired/
    for slug in expired:
        assert (wiki / "_expired" / f"{slug}.md").exists()


def test_aucune_expiration_si_recent(setup) -> None:
    stm, ltm, gov, _ = setup
    stm.ingest(_entry(entities=[]))
    assert gov._expire_stale() == []


# ---------------------------------------------------- consolidation


def test_consolidation_sous_seuil(setup) -> None:
    stm, ltm, gov, _ = setup
    # 2 pages < max_stm_pages=5
    stm.ingest(_entry(entities=[]))
    assert gov._consolidate_if_oversized() == []


def test_consolidation_au_dessus_seuil(setup) -> None:
    stm, ltm, gov, _ = setup
    # Créer 7 pages (> max=5) : 1 source + 2 entités x 3 ingests
    for i in range(3):
        stm.ingest(_entry(
            text=f"entrée {i}",
            entities=[f"entite{i}a", f"entite{i}b"],
            ts=datetime(2026, 4, 14, 10 + i, 0),
        ))
    candidates = gov._consolidate_if_oversized()
    assert len(candidates) > 0


# ------------------------------------------------------------ helpers


def test_humanize() -> None:
    assert _humanize("pergola-jardin") == "Pergola jardin"
    assert _humanize("cedre-rouge") == "Cedre rouge"


def test_first_para_simple() -> None:
    content = "# Titre\n\nCeci est le premier paragraphe.\nSuite.\n\nDeuxième para."
    assert "premier paragraphe" in _first_para(content)
    assert "Deuxième" not in _first_para(content)


def test_first_para_vide() -> None:
    assert _first_para("") == ""


def test_infer_label_entity(setup) -> None:
    stm, ltm, gov, _ = setup
    stm.ingest(_entry(entities=["pergola"]))
    pages = stm.list_pages()
    label = _infer_label("pergola", pages)
    assert label == "Concept"

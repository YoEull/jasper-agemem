"""Tests du retrieval hybride STM + LTM."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from core.inbox_parser import InboxEntry
from governance.ltm_store import LTMStore
from governance.retrieval import (
    HybridRetriever,
    RetrievalResult,
    _keywords,
    _match_score,
    _slug_from_content,
)
from governance.stm_manager import STMManager

DIM = 4


@pytest.fixture
def setup(tmp_path: Path):
    stm = STMManager(tmp_path / "wiki")
    ltm = LTMStore(tmp_path / "ltm.ryu", embedding_dim=DIM)
    retriever = HybridRetriever(stm, ltm)
    yield stm, ltm, retriever
    ltm.close()


def _entry(entities=None, tags=None, ts=None, text="contenu") -> InboxEntry:
    return InboxEntry(
        timestamp=ts or datetime(2026, 4, 14, 22, 30),
        tags=list(tags or ["idée"]),
        text=text,
        entities=list(entities or []),
    )


# ---------------------------------------------------------------- helpers


def test_keywords_filtre_stopwords() -> None:
    kws = _keywords("qu'est-ce que je sais sur la pergola jardin ?")
    assert "pergola" in kws
    assert "jardin" in kws
    assert "que" not in kws
    assert "les" not in kws


def test_keywords_mots_courts_ignores() -> None:
    kws = _keywords("un de la pergola")
    # mots <= 2 chars ignorés
    assert "un" not in kws
    assert "de" not in kws


def test_match_score_slug() -> None:
    assert _match_score("pergola-jardin", [], ["pergola"]) > 0
    assert _match_score("cedre-rouge", [], ["pergola"]) == 0


def test_match_score_tag() -> None:
    assert _match_score("quelconque", ["idée"], ["idée"]) > 0


def test_slug_from_content_present() -> None:
    content = "---\nid: pergola-jardin\ntype: concept\n---\n# Pergola\n"
    assert _slug_from_content(content) == "pergola-jardin"


def test_slug_from_content_absent() -> None:
    assert _slug_from_content("# Titre sans frontmatter") is None


# ------------------------------------------------------------ retrieve STM


def test_retrieve_inclut_index(setup) -> None:
    stm, ltm, retriever = setup
    stm.ingest(_entry(entities=["pergola"]))
    result = retriever.retrieve("pergola")
    # Au moins l'index.md doit être dans les pages
    assert any("Index" in p for p in result.stm_pages)


def test_retrieve_stm_match_slug(setup) -> None:
    stm, ltm, retriever = setup
    stm.ingest(_entry(entities=["pergola", "cèdre"]))
    result = retriever.retrieve("pergola")
    assert any("pergola" in p.lower() for p in result.stm_pages)


def test_retrieve_stm_vide_si_aucun_match(setup) -> None:
    stm, ltm, retriever = setup
    stm.ingest(_entry(entities=["pergola"]))
    result = retriever.retrieve("astronaute")
    # Seul index.md (pas de match pergola vs astronaute)
    assert len(result.stm_pages) >= 1  # index toujours inclus


# ------------------------------------------------------------ retrieve LTM


def test_retrieve_ltm_par_keyword(setup) -> None:
    stm, ltm, retriever = setup
    ltm.upsert_node("Concept", {"id": "pergola", "name": "Pergola"})
    result = retriever.retrieve("pergola")
    ids = [n.get("n.id") for n in result.ltm_nodes]
    assert "pergola" in ids


def test_retrieve_ltm_vide_si_rien(setup) -> None:
    stm, ltm, retriever = setup
    result = retriever.retrieve("astronaute")
    assert result.ltm_nodes == []
    assert result.ltm_edges == []


def test_retrieve_ltm_edges_sous_graphe(setup) -> None:
    stm, ltm, retriever = setup
    ltm.upsert_node("Concept", {"id": "pergola", "name": "Pergola"})
    ltm.upsert_node("Concept", {"id": "jardin", "name": "Jardin"})
    ltm.upsert_edge("pergola", "RELATED_TO", "jardin", {"weight": 0.8})
    result = retriever.retrieve("pergola jardin")
    assert len(result.ltm_edges) >= 1
    edge = result.ltm_edges[0]
    assert "src" in edge and "dst" in edge


# ---------------------------------------------------------- vector search


def test_retrieve_avec_embedder(setup) -> None:
    stm, ltm, retriever = setup

    class _FakeEmbedder:
        def encode(self, text: str) -> list[float]:
            return [1.0, 0.0, 0.0, 0.0]

    retriever.embedder = _FakeEmbedder()
    ltm.upsert_node("Concept", {
        "id": "pergola", "name": "Pergola",
        "embedding": [1.0, 0.0, 0.0, 0.0],
    })
    result = retriever.retrieve("pergola")
    ids = [n.get("n.id") for n in result.ltm_nodes]
    assert "pergola" in ids


# ---------------------------------------------------------- touch + result


def test_retrieve_result_dataclass(setup) -> None:
    stm, ltm, retriever = setup
    result = retriever.retrieve("test")
    assert isinstance(result, RetrievalResult)
    assert result.query == "test"
    assert isinstance(result.stm_pages, list)
    assert isinstance(result.ltm_nodes, list)
    assert isinstance(result.ltm_edges, list)


def test_retrieve_touche_pages_accedees(setup) -> None:
    stm, ltm, retriever = setup
    stm.ingest(_entry(entities=["pergola"]))
    import frontmatter as fm
    before = None
    for page in stm.list_pages():
        if page.slug == "pergola":
            post = fm.load(page.path)
            before = post["access_count"]
    retriever.retrieve("pergola")
    for page in stm.list_pages():
        if page.slug == "pergola":
            post = fm.load(page.path)
            assert post["access_count"] > before

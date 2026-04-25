"""
Tests d'intégration ltm_store — utilise une vraie DB RyuGraph temporaire.
(Pas de mock : RyuGraph est embarqué, zéro serveur, peu coûteux.)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governance.ltm_store import LTMStore, _cosine

DIM = 4  # petit vecteur pour les tests


@pytest.fixture
def store(tmp_path: Path) -> LTMStore:
    """LTMStore avec embedding_dim=4 et DB temporaire."""
    s = LTMStore(tmp_path / "test.ryu", embedding_dim=DIM)
    yield s
    s.close()


def _vec(*values: float) -> list[float]:
    return list(values)


# ---------------------------------------------------------------- upsert_node


def test_upsert_node_creation(store: LTMStore) -> None:
    nid = store.upsert_node("Concept", {"id": "pergola", "name": "Pergola"})
    assert nid == "pergola"
    rows = store.cypher("MATCH (n:JasperNode {id: 'pergola'}) RETURN n.id, n.label, n.name")
    assert len(rows) == 1
    assert rows[0]["n.label"] == "Concept"
    assert rows[0]["n.name"] == "Pergola"


def test_upsert_node_mise_a_jour(store: LTMStore) -> None:
    store.upsert_node("Concept", {"id": "p", "name": "v1", "confidence": 0.5})
    store.upsert_node("Concept", {"id": "p", "name": "v2", "confidence": 0.9})
    rows = store.cypher("MATCH (n:JasperNode {id: 'p'}) RETURN n.name, n.confidence")
    assert len(rows) == 1  # pas de doublon
    assert rows[0]["n.name"] == "v2"
    assert abs(rows[0]["n.confidence"] - 0.9) < 1e-6


def test_upsert_node_label_invalide(store: LTMStore) -> None:
    with pytest.raises(ValueError, match="Label inconnu"):
        store.upsert_node("Animal", {"id": "chien", "name": "Chien"})


def test_upsert_node_sans_id_leve(store: LTMStore) -> None:
    with pytest.raises(ValueError, match="'id' ou 'name'"):
        store.upsert_node("Concept", {})


def test_upsert_node_avec_embedding(store: LTMStore) -> None:
    emb = _vec(0.1, 0.2, 0.3, 0.4)
    store.upsert_node("Concept", {"id": "x", "name": "X", "embedding": emb})
    rows = store.cypher("MATCH (n:JasperNode {id: 'x'}) RETURN n.embedding")
    stored = rows[0]["n.embedding"]
    assert len(stored) == DIM
    assert abs(stored[0] - 0.1) < 1e-5


def test_upsert_node_embedding_mauvaise_taille(store: LTMStore) -> None:
    with pytest.raises(ValueError, match="dimensions"):
        store.upsert_node("Concept", {"id": "y", "name": "Y", "embedding": [0.1, 0.2]})


def test_tous_les_labels_valides(store: LTMStore) -> None:
    labels = ["Person", "Place", "Project", "Concept", "Tool", "Event", "Idea"]
    for i, label in enumerate(labels):
        nid = store.upsert_node(label, {"id": f"n{i}", "name": f"Node{i}"})
        assert nid == f"n{i}"
    rows = store.cypher("MATCH (n:JasperNode) RETURN n.id")
    assert len(rows) == len(labels)


# ---------------------------------------------------------------- upsert_edge


def test_upsert_edge_creation(store: LTMStore) -> None:
    store.upsert_node("Concept", {"id": "a", "name": "A"})
    store.upsert_node("Concept", {"id": "b", "name": "B"})
    store.upsert_edge("a", "RELATED_TO", "b", {"weight": 0.8})
    rows = store.cypher(
        "MATCH (x:JasperNode)-[e:RELATED_TO]->(y:JasperNode) RETURN e.weight"
    )
    assert len(rows) == 1
    assert abs(rows[0]["e.weight"] - 0.8) < 1e-6


def test_upsert_edge_mise_a_jour(store: LTMStore) -> None:
    store.upsert_node("Concept", {"id": "a", "name": "A"})
    store.upsert_node("Concept", {"id": "b", "name": "B"})
    store.upsert_edge("a", "RELATED_TO", "b", {"weight": 0.5})
    store.upsert_edge("a", "RELATED_TO", "b", {"weight": 0.9})
    rows = store.cypher(
        "MATCH (x:JasperNode)-[e:RELATED_TO]->(y:JasperNode) RETURN e.weight"
    )
    assert len(rows) == 1  # pas de doublon
    assert abs(rows[0]["e.weight"] - 0.9) < 1e-6


def test_upsert_edge_type_invalide(store: LTMStore) -> None:
    store.upsert_node("Concept", {"id": "a", "name": "A"})
    store.upsert_node("Concept", {"id": "b", "name": "B"})
    with pytest.raises(ValueError, match="Type d'edge inconnu"):
        store.upsert_edge("a", "AIME_BIEN", "b")


def test_upsert_edge_noeud_absent_leve(store: LTMStore) -> None:
    store.upsert_node("Concept", {"id": "a", "name": "A"})
    with pytest.raises(ValueError, match="Nœud introuvable"):
        store.upsert_edge("a", "RELATED_TO", "absent")


def test_tous_les_types_edge(store: LTMStore) -> None:
    store.upsert_node("Concept", {"id": "x", "name": "X"})
    store.upsert_node("Concept", {"id": "y", "name": "Y"})
    types = ["RELATED_TO", "PART_OF", "CREATED_BY", "HAPPENED_AT",
             "CONTRADICTS", "REINFORCES", "EVOLVED_FROM"]
    for t in types:
        store.upsert_edge("x", t, "y")  # pas d'exception


# ----------------------------------------------------------------- cypher


def test_cypher_retourne_dicts(store: LTMStore) -> None:
    store.upsert_node("Idea", {"id": "i1", "name": "Idée test"})
    rows = store.cypher(
        "MATCH (n:JasperNode {label: $lbl}) RETURN n.id, n.name",
        {"lbl": "Idea"},
    )
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0]["n.id"] == "i1"
    assert rows[0]["n.name"] == "Idée test"


def test_cypher_vide_retourne_liste_vide(store: LTMStore) -> None:
    rows = store.cypher("MATCH (n:JasperNode {id: 'absent'}) RETURN n.id")
    assert rows == []


# ------------------------------------------------------------ vector_search


def test_vector_search_retrouve_proche(store: LTMStore) -> None:
    store.upsert_node("Concept", {
        "id": "proche", "name": "Proche",
        "embedding": _vec(1.0, 0.0, 0.0, 0.0),
    })
    store.upsert_node("Concept", {
        "id": "loin", "name": "Loin",
        "embedding": _vec(0.0, 0.0, 0.0, 1.0),
    })
    results = store.vector_search(_vec(1.0, 0.0, 0.0, 0.0), k=1)
    assert len(results) == 1
    assert results[0]["n.id"] == "proche"
    assert results[0]["_score"] > 0.99


def test_vector_search_filtre_label(store: LTMStore) -> None:
    store.upsert_node("Concept", {
        "id": "c1", "name": "C",
        "embedding": _vec(1.0, 0.0, 0.0, 0.0),
    })
    store.upsert_node("Person", {
        "id": "p1", "name": "P",
        "embedding": _vec(1.0, 0.0, 0.0, 0.0),
    })
    results = store.vector_search(_vec(1.0, 0.0, 0.0, 0.0), k=5, label="Person")
    ids = [r["n.id"] for r in results]
    assert "p1" in ids
    assert "c1" not in ids


def test_vector_search_mauvaise_taille(store: LTMStore) -> None:
    with pytest.raises(ValueError, match="dimensions"):
        store.vector_search([0.1, 0.2])


def test_vector_search_label_invalide(store: LTMStore) -> None:
    with pytest.raises(ValueError, match="Label inconnu"):
        store.vector_search(_vec(1.0, 0.0, 0.0, 0.0), label="Animal")


# ------------------------------------------------------------------ _cosine


def test_cosine_identiques() -> None:
    assert abs(_cosine([1.0, 0.0], [1.0, 0.0]) - 1.0) < 1e-9


def test_cosine_orthogonaux() -> None:
    assert abs(_cosine([1.0, 0.0], [0.0, 1.0])) < 1e-9


def test_cosine_vecteur_nul() -> None:
    assert _cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_cosine_tailles_differentes() -> None:
    assert _cosine([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0

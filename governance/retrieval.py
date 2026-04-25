"""
retrieval — récupération hybride STM (context direct) + LTM (graphe/vecteurs).

Stratégie en deux passes :
    1. STM : index.md toujours chargé + pages dont le slug ou les tags
       matchent les mots-clés de la requête.
    2. LTM : sous-graphe Cypher sur les nœuds dont le nom contient un
       mot-clé + vecteur search si embedder fourni.
    3. Fusion dans RetrievalResult, prêt à être injecté dans un prompt.

L'embedder est optionnel (injectable) pour rester offline / testable
sans sentence-transformers. En production, passer un `Embedder` réel.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from governance.ltm_store import LTMStore
from governance.stm_manager import STMManager


# ----------------------------------------------------------------- types


@dataclass
class RetrievalResult:
    query: str
    stm_pages: list[str] = field(default_factory=list)   # contenu markdown
    ltm_nodes: list[dict] = field(default_factory=dict)  # nœuds LTM
    ltm_edges: list[dict] = field(default_factory=dict)  # edges sous-graphe


class Embedder(Protocol):
    """Interface minimale pour transformer une requête en vecteur."""

    def encode(self, text: str) -> list[float]: ...


# --------------------------------------------------------------- retriever


class HybridRetriever:
    """Retrieval hybride STM (fichiers) + LTM (Cypher + vecteurs)."""

    def __init__(
        self,
        stm: STMManager,
        ltm: LTMStore,
        embedder: Embedder | None = None,
    ) -> None:
        self.stm = stm
        self.ltm = ltm
        self.embedder = embedder

    def retrieve(
        self,
        query: str,
        k_stm: int = 10,
        k_ltm: int = 10,
    ) -> RetrievalResult:
        """Recherche hybride. Retourne STM pages + sous-graphe LTM."""
        keywords = _keywords(query)

        stm_pages = self._retrieve_stm(keywords, k=k_stm)
        ltm_nodes, ltm_edges = self._retrieve_ltm(query, keywords, k=k_ltm)

        # Incrémenter access_count des pages STM touchées
        for content in stm_pages:
            slug = _slug_from_content(content)
            if slug:
                try:
                    self.stm.touch(slug)
                except FileNotFoundError:
                    pass

        return RetrievalResult(
            query=query,
            stm_pages=stm_pages,
            ltm_nodes=ltm_nodes,
            ltm_edges=ltm_edges,
        )

    # ---------------------------------------------------------- STM

    def _retrieve_stm(self, keywords: list[str], k: int) -> list[str]:
        """Charge l'index + les pages matchant les mots-clés."""
        pages_content: list[str] = []

        # Index toujours inclus en premier
        try:
            idx_path = self.stm.wiki_dir / "index.md"
            if idx_path.exists():
                pages_content.append(idx_path.read_text(encoding="utf-8"))
        except OSError:
            pass

        # Pages matchant par slug ou contenu
        matched: list[tuple[int, str]] = []
        for page_meta in self.stm.list_pages():
            score = _match_score(page_meta.slug, page_meta.tags, keywords)
            if score > 0:
                matched.append((score, page_meta.slug))

        matched.sort(reverse=True)
        for _, slug in matched[: k - 1]:  # -1 pour l'index déjà inclus
            try:
                pages_content.append(self.stm.read_page(slug))
            except FileNotFoundError:
                pass

        return pages_content

    # ---------------------------------------------------------- LTM

    def _retrieve_ltm(
        self, query: str, keywords: list[str], k: int
    ) -> tuple[list[dict], list[dict]]:
        """Sous-graphe LTM : Cypher keyword + vector search optionnel."""
        node_ids: set[str] = set()

        # 1. Cypher : nœuds dont le nom contient un mot-clé
        for kw in keywords:
            rows = self.ltm.cypher(
                "MATCH (n:JasperNode) "
                "WHERE LOWER(n.name) CONTAINS LOWER($kw) "
                "RETURN n.id, n.label, n.name, n.definition, n.source",
                {"kw": kw},
            )
            for row in rows:
                node_ids.add(row["n.id"])

        # 2. Vector search si embedder disponible
        if self.embedder is not None:
            embedding = self.embedder.encode(query)
            vec_results = self.ltm.vector_search(embedding, k=k)
            for row in vec_results:
                node_ids.add(row["n.id"])

        if not node_ids:
            return [], []

        # 3. Récupérer les nœuds complets
        nodes: list[dict] = []
        for nid in list(node_ids)[:k]:
            rows = self.ltm.cypher(
                "MATCH (n:JasperNode {id: $id}) "
                "RETURN n.id, n.label, n.name, n.definition, n.source",
                {"id": nid},
            )
            nodes.extend(rows)

        # 4. Edges entre ces nœuds (sous-graphe induit)
        # type(e) non supporté en requête paramétrée → interroger par type.
        edges: list[dict] = []
        if len(node_ids) > 1:
            ids_list = list(node_ids)[:k]
            from governance.ltm_store import EDGE_TYPES
            for etype in EDGE_TYPES:
                rows = self.ltm.cypher(
                    f"MATCH (a:JasperNode)-[e:{etype}]->(b:JasperNode) "
                    f"WHERE a.id IN $ids AND b.id IN $ids "
                    f"RETURN a.id AS src, b.id AS dst, e.weight",
                    {"ids": ids_list},
                )
                for row in rows:
                    edges.append({**row, "rel": etype})

        return nodes, edges


# ---------------------------------------------------------------- helpers


def _keywords(query: str) -> list[str]:
    """Extrait les mots significatifs de la requête (> 2 caractères)."""
    words = re.findall(r"[a-zA-Zà-ÿ0-9_]{3,}", query.lower())
    # Stopwords FR minimaux
    stops = {
        "que", "qui", "quoi", "les", "des", "une", "pour", "dans",
        "sur", "avec", "par", "est", "son", "ses", "mes", "mais",
        "donc", "car", "sais", "avais", "décidé", "fais",
    }
    return [w for w in words if w not in stops]


def _match_score(slug: str, tags: list[str], keywords: list[str]) -> int:
    """Score de pertinence d'une page STM pour une liste de mots-clés."""
    score = 0
    slug_words = set(slug.replace("-", " ").split())
    tag_set = set(tags)
    for kw in keywords:
        if kw in slug_words:
            score += 2
        if kw in tag_set:
            score += 1
        if any(kw in word for word in slug_words):
            score += 1
    return score


def _slug_from_content(content: str) -> str | None:
    """Extrait le slug depuis le frontmatter `id:` d'une page."""
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("id:"):
            return line.split(":", 1)[1].strip().strip("'\"")
    return None

"""
retrieval — récupération hybride STM (context direct) + LTM (graphe/vecteurs).

Stratégie :
    1. STM : index.md + pages matchant tags/slugs de la requête
    2. LTM : sous-graphe Cypher + filtre vecteur sémantique
    3. Fusion dans un objet RetrievalResult passé à Claude
"""

from dataclasses import dataclass, field


@dataclass
class RetrievalResult:
    stm_pages: list[str] = field(default_factory=list)   # contenu markdown
    ltm_subgraph: dict = field(default_factory=dict)     # nodes + edges
    query: str = ""


class HybridRetriever:
    def __init__(self, stm, ltm) -> None:
        raise NotImplementedError

    def retrieve(
        self, query: str, k_stm: int = 10, k_ltm: int = 10
    ) -> RetrievalResult:
        raise NotImplementedError

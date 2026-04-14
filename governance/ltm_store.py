"""
ltm_store — interface CRUD au graphe LTM (RyuGraph).

Abstraction volontairement fine au-dessus de RyuGraph (fork Kuzu) :
Cypher + recherche vectorielle embarqués. Permet de basculer vers
Kuzu/LadybugDB sans toucher le reste du code.

API :
    LTMStore(db_path)
        upsert_node(label, props)                -> id
        upsert_edge(src_id, edge_type, dst_id,
                    props)                       -> None
        cypher(query, params)                    -> list[dict]
        vector_search(embedding, k)              -> list[dict]
        close()                                  -> None
"""

from pathlib import Path


class LTMStore:
    def __init__(self, db_path: Path) -> None:
        raise NotImplementedError

    def upsert_node(self, label: str, props: dict) -> str:
        raise NotImplementedError

    def upsert_edge(
        self, src: str, edge: str, dst: str, props: dict | None = None
    ) -> None:
        raise NotImplementedError

    def cypher(self, query: str, params: dict | None = None) -> list[dict]:
        raise NotImplementedError

    def vector_search(self, embedding: list[float], k: int = 10) -> list[dict]:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

"""
ltm_store — interface CRUD au graphe LTM (RyuGraph).

Choix d'implémentation :
- Table de nœuds unique `JasperNode` avec propriété `label`
  (Person | Place | Project | Concept | Tool | Event | Idea).
  Simplifie les relations qui peuvent alors être FROM JasperNode TO JasperNode.
- Vecteurs stockés en FLOAT[embedding_dim] (défaut 1024 pour bge-m3).
  En tests, passer embedding_dim=4 pour éviter numpy lourd.
- 7 types d'edge (voir ARCHITECTURE.md §2.3), tous FROM/TO JasperNode.

API publique :
    LTMStore(db_path, embedding_dim=1024)
        upsert_node(label, props)               -> id  (str)
        upsert_edge(src_id, edge_type, dst_id, props) -> None
        cypher(query, params)                   -> list[dict]
        vector_search(embedding, k, label)      -> list[dict]
        close()                                 -> None
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import ryugraph

# Labels de nœuds valides
NODE_LABELS = frozenset(
    {"Person", "Place", "Project", "Concept", "Tool", "Event", "Idea"}
)

# Types d'edge valides
EDGE_TYPES = frozenset(
    {
        "RELATED_TO",
        "PART_OF",
        "CREATED_BY",
        "HAPPENED_AT",
        "CONTRADICTS",
        "REINFORCES",
        "EVOLVED_FROM",
    }
)

# DDL du schéma (exécuté une seule fois à l'init si non présent)
_NODE_DDL = """
CREATE NODE TABLE IF NOT EXISTS JasperNode(
    id          STRING,
    label       STRING,
    name        STRING,
    definition  STRING,
    created_at  STRING,
    last_accessed STRING,
    confidence  DOUBLE,
    source      STRING,
    embedding   FLOAT[{dim}],
    PRIMARY KEY (id)
)
"""

_EDGE_DDLS = [
    "CREATE REL TABLE IF NOT EXISTS RELATED_TO"
    "(FROM JasperNode TO JasperNode, weight DOUBLE, source STRING, created_at STRING)",
    "CREATE REL TABLE IF NOT EXISTS PART_OF"
    "(FROM JasperNode TO JasperNode, weight DOUBLE, source STRING, created_at STRING)",
    "CREATE REL TABLE IF NOT EXISTS CREATED_BY"
    "(FROM JasperNode TO JasperNode, weight DOUBLE, source STRING, created_at STRING)",
    "CREATE REL TABLE IF NOT EXISTS HAPPENED_AT"
    "(FROM JasperNode TO JasperNode, weight DOUBLE, source STRING, created_at STRING)",
    "CREATE REL TABLE IF NOT EXISTS CONTRADICTS"
    "(FROM JasperNode TO JasperNode, weight DOUBLE, source STRING, created_at STRING)",
    "CREATE REL TABLE IF NOT EXISTS REINFORCES"
    "(FROM JasperNode TO JasperNode, weight DOUBLE, source STRING, created_at STRING)",
    "CREATE REL TABLE IF NOT EXISTS EVOLVED_FROM"
    "(FROM JasperNode TO JasperNode, weight DOUBLE, source STRING, created_at STRING)",
]


class LTMStore:
    """Accès au graphe LTM embarqué (RyuGraph)."""

    def __init__(self, db_path: Path, embedding_dim: int = 1024) -> None:
        self.db_path = db_path
        self.embedding_dim = embedding_dim
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = ryugraph.Database(str(db_path))
        self._conn = ryugraph.Connection(self._db)
        self._init_schema()

    # ----------------------------------------------------------------- schema

    def _init_schema(self) -> None:
        """Crée les tables si elles n'existent pas encore."""
        self._conn.execute(_NODE_DDL.format(dim=self.embedding_dim))
        for ddl in _EDGE_DDLS:
            self._conn.execute(ddl)

    # ----------------------------------------------------------------- nœuds

    def upsert_node(self, label: str, props: dict[str, Any]) -> str:
        """Crée ou met à jour un nœud. Retourne son `id`.

        `props` doit contenir au minimum `id` et `name`.
        `label` doit être dans NODE_LABELS.
        `embedding` est optionnel : si absent, un vecteur nul est utilisé.
        """
        if label not in NODE_LABELS:
            raise ValueError(
                f"Label inconnu : {label!r}. Valides : {sorted(NODE_LABELS)}"
            )
        node_id: str = str(props.get("id") or props.get("name", "")).strip()
        if not node_id:
            raise ValueError("props doit contenir 'id' ou 'name' non vide")

        embedding = list(
            props.get("embedding") or [0.0] * self.embedding_dim
        )
        if len(embedding) != self.embedding_dim:
            raise ValueError(
                f"embedding doit avoir {self.embedding_dim} dimensions, "
                f"reçu {len(embedding)}"
            )

        from datetime import datetime

        now = datetime.now().isoformat(timespec="seconds")

        # Vérification existence
        exists_res = self._conn.execute(
            "MATCH (n:JasperNode {id: $id}) RETURN n.id",
            {"id": node_id},
        )
        if exists_res.has_next():
            # Mise à jour des champs mutables
            self._conn.execute(
                """
                MATCH (n:JasperNode {id: $id})
                SET n.name = $name,
                    n.last_accessed = $now,
                    n.confidence = $confidence,
                    n.source = $source,
                    n.definition = $definition,
                    n.embedding = $embedding
                """,
                {
                    "id": node_id,
                    "name": str(props.get("name", node_id)),
                    "now": now,
                    "confidence": float(props.get("confidence", 1.0)),
                    "source": str(props.get("source", "")),
                    "definition": str(props.get("definition", "")),
                    "embedding": embedding,
                },
            )
        else:
            self._conn.execute(
                """
                CREATE (:JasperNode {
                    id:           $id,
                    label:        $label,
                    name:         $name,
                    definition:   $definition,
                    created_at:   $now,
                    last_accessed:$now,
                    confidence:   $confidence,
                    source:       $source,
                    embedding:    $embedding
                })
                """,
                {
                    "id": node_id,
                    "label": label,
                    "name": str(props.get("name", node_id)),
                    "definition": str(props.get("definition", "")),
                    "now": now,
                    "confidence": float(props.get("confidence", 1.0)),
                    "source": str(props.get("source", "")),
                    "embedding": embedding,
                },
            )
        return node_id

    # ----------------------------------------------------------------- edges

    def upsert_edge(
        self,
        src_id: str,
        edge_type: str,
        dst_id: str,
        props: dict[str, Any] | None = None,
    ) -> None:
        """Crée ou met à jour une relation entre deux nœuds existants.

        Lève ValueError si `edge_type` est inconnu ou si les nœuds
        src/dst n'existent pas.
        """
        if edge_type not in EDGE_TYPES:
            raise ValueError(
                f"Type d'edge inconnu : {edge_type!r}. "
                f"Valides : {sorted(EDGE_TYPES)}"
            )
        p = props or {}
        from datetime import datetime

        now = datetime.now().isoformat(timespec="seconds")

        # Vérification existence des deux nœuds
        for nid in (src_id, dst_id):
            r = self._conn.execute(
                "MATCH (n:JasperNode {id: $id}) RETURN n.id", {"id": nid}
            )
            if not r.has_next():
                raise ValueError(f"Nœud introuvable : {nid!r}")

        # MERGE sur (src)-[edge]->(dst) — RyuGraph ne supporte pas
        # MERGE sur les relations, on vérifie manuellement.
        exists = self._conn.execute(
            f"MATCH (a:JasperNode {{id: $src}})"
            f"-[e:{edge_type}]->"
            f"(b:JasperNode {{id: $dst}}) RETURN e.weight",
            {"src": src_id, "dst": dst_id},
        )
        if exists.has_next():
            self._conn.execute(
                f"MATCH (a:JasperNode {{id: $src}})"
                f"-[e:{edge_type}]->"
                f"(b:JasperNode {{id: $dst}}) "
                f"SET e.weight = $weight, e.source = $source, e.created_at = $now",
                {
                    "src": src_id,
                    "dst": dst_id,
                    "weight": float(p.get("weight", 1.0)),
                    "source": str(p.get("source", "")),
                    "now": now,
                },
            )
        else:
            self._conn.execute(
                f"MATCH (a:JasperNode {{id: $src}}), (b:JasperNode {{id: $dst}}) "
                f"CREATE (a)-[:{edge_type} {{weight: $weight, source: $source, "
                f"created_at: $now}}]->(b)",
                {
                    "src": src_id,
                    "dst": dst_id,
                    "weight": float(p.get("weight", 1.0)),
                    "source": str(p.get("source", "")),
                    "now": now,
                },
            )

    # --------------------------------------------------------------- requêtes

    def cypher(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict]:
        """Exécute une requête Cypher et retourne les lignes en dicts."""
        result = self._conn.execute(query, params or {})
        rows = []
        cols = result.get_column_names()
        while result.has_next():
            rows.append(dict(zip(cols, result.get_next())))
        return rows

    def vector_search(
        self,
        embedding: list[float],
        k: int = 10,
        label: str | None = None,
    ) -> list[dict]:
        """Recherche les k nœuds les plus proches par similarité cosinus.

        Implémentation : calcul en Python sur tous les nœuds du label
        (ou tous les nœuds si label=None). Adapté au volume d'un second
        brain personnel (~quelques centaines de nœuds).
        """
        if len(embedding) != self.embedding_dim:
            raise ValueError(
                f"embedding doit avoir {self.embedding_dim} dimensions"
            )

        if label is not None and label not in NODE_LABELS:
            raise ValueError(f"Label inconnu : {label!r}")

        label_filter = f"AND n.label = '{label}'" if label else ""
        rows = self.cypher(
            f"MATCH (n:JasperNode) WHERE n.id IS NOT NULL {label_filter} "
            f"RETURN n.id, n.label, n.name, n.embedding, n.definition, n.source"
        )

        scored: list[tuple[float, dict]] = []
        for row in rows:
            emb = row.get("n.embedding") or []
            sim = _cosine(embedding, emb)
            scored.append((sim, row))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {**r, "_score": s} for s, r in scored[:k] if s > 0
        ]

    # ------------------------------------------------------------------ close

    def close(self) -> None:
        self._conn.close()


# ------------------------------------------------------------------- helpers


def _cosine(a: list[float], b: list[float]) -> float:
    """Similarité cosinus entre deux vecteurs (listes de même taille)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)

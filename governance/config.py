"""
config — seuils et paramètres de gouvernance.

Valeurs calibrées par défaut (cf. ARCHITECTURE.md §2.4).
"""

from dataclasses import dataclass


@dataclass
class GovConfig:
    ttl_days: int = 30                 # expiration page STM sans accès
    max_stm_pages: int = 50            # déclencheur consolidation
    promote_threshold: int = 3         # pages citantes -> promotion LTM
    min_edge_confidence: float = 0.6   # edge auto créé si >= seuil
    embedding_dim: int = 1024          # bge-m3 local par défaut
    embedding_model: str = "bge-m3"

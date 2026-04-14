"""
governor — agent de gouvernance STM <-> LTM.

Passe complète (cf. ARCHITECTURE.md §2.4) :
    1. scan métadonnées STM
    2. détection entités fréquentes (seuil N)
    3. promotion LTM (nodes + edges)
    4. expiration TTL
    5. consolidation si wiki surdimensionné
    6. log
"""

from dataclasses import dataclass, field


@dataclass
class Promotion:
    slug: str
    label: str              # Person | Place | Project | ...
    citing_pages: list[str]
    confidence: float


@dataclass
class GovReport:
    promoted: list[Promotion] = field(default_factory=list)
    expired: list[str] = field(default_factory=list)
    consolidated: list[str] = field(default_factory=list)


class Governor:
    def __init__(self, stm, ltm, config) -> None:
        raise NotImplementedError

    def govern(self) -> GovReport:
        raise NotImplementedError

    def _detect_promotions(self) -> list[Promotion]:
        raise NotImplementedError

    def _promote(self, p: Promotion) -> None:
        raise NotImplementedError

    def _expire_stale(self) -> list[str]:
        raise NotImplementedError

    def _consolidate_if_oversized(self) -> list[str]:
        raise NotImplementedError

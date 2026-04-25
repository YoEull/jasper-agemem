"""
governor — agent de gouvernance STM <-> LTM.

Passe complète déclenchée par `jasper govern` :
    1. Scan des métadonnées STM
    2. Détection des entités fréquentes (seuil N pages citantes)
    3. Promotion vers LTM (upsert node + edges RELATED_TO co-occurrences)
    4. Expiration des pages TTL dépassé
    5. Consolidation si wiki surdimensionné (placeholder Phase 2)
    6. Log dans stm/wiki/log.md

Toutes les dépendances (STMManager, LTMStore, GovConfig) sont injectées
pour faciliter les tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from governance.config import GovConfig
from governance.ltm_store import LTMStore
from governance.stm_manager import PageMeta, STMManager


@dataclass
class Promotion:
    slug: str
    label: str               # label LTM déduit (heuristique simple)
    citing_pages: list[str]  # slugs des pages qui citent cette entité
    confidence: float = 1.0


@dataclass
class GovReport:
    promoted: list[Promotion] = field(default_factory=list)
    expired: list[str] = field(default_factory=list)
    consolidated: list[str] = field(default_factory=list)


class Governor:
    """Agent de gouvernance STM ↔ LTM."""

    def __init__(
        self,
        stm: STMManager,
        ltm: LTMStore,
        config: GovConfig | None = None,
    ) -> None:
        self.stm = stm
        self.ltm = ltm
        self.cfg = config or GovConfig()

    # ---------------------------------------------------------------- public

    def govern(self) -> GovReport:
        """Exécute la passe complète. Retourne un rapport."""
        report = GovReport()

        promotions = self._detect_promotions()
        for p in promotions:
            self._promote(p)
            report.promoted.append(p)

        report.expired = self._expire_stale()
        report.consolidated = self._consolidate_if_oversized()

        self._log_report(report)
        return report

    # ------------------------------------------------------------ détection

    def _detect_promotions(self) -> list[Promotion]:
        """Retourne les slugs cités dans ≥ N pages distinctes (seuil config)."""
        pages = self.stm.list_pages()

        # Index : slug_cible -> liste de slugs sources qui le citent
        citations: dict[str, list[str]] = {}
        for page in pages:
            for link in page.links:
                citations.setdefault(link, []).append(page.slug)

        promotions: list[Promotion] = []
        already_promoted = {p.slug for p in pages if p.promoted_to_ltm}

        for slug, citers in citations.items():
            if len(citers) < self.cfg.promote_threshold:
                continue
            if slug in already_promoted:
                continue
            promotions.append(
                Promotion(
                    slug=slug,
                    label=_infer_label(slug, pages),
                    citing_pages=list(set(citers)),
                    confidence=min(1.0, len(citers) / 5),
                )
            )
        return promotions

    # ------------------------------------------------------------ promotion

    def _promote(self, p: Promotion) -> None:
        """Upserte le nœud dans LTM, crée edges, marque la page STM."""
        # Trouver la page cible pour lire son contenu
        try:
            content = self.stm.read_page(p.slug)
        except FileNotFoundError:
            content = ""

        # Upsert nœud dans LTM
        self.ltm.upsert_node(
            p.label,
            {
                "id": p.slug,
                "name": _humanize(p.slug),
                "definition": _first_para(content),
                "confidence": p.confidence,
                "source": ",".join(p.citing_pages),
            },
        )

        # Edges RELATED_TO vers les co-citants déjà dans le LTM
        for citer_slug in p.citing_pages:
            rows = self.ltm.cypher(
                "MATCH (n:JasperNode {id: $id}) RETURN n.id",
                {"id": citer_slug},
            )
            if rows:
                try:
                    self.ltm.upsert_edge(
                        p.slug,
                        "RELATED_TO",
                        citer_slug,
                        {"weight": p.confidence, "source": "governor"},
                    )
                except ValueError:
                    pass  # nœud citer pas encore dans LTM, OK

        # Marquer promoted_to_ltm dans le frontmatter STM
        try:
            import frontmatter
            from governance.stm_manager import _dump, _meta_from_path

            page_path = None
            for meta in self.stm.list_pages():
                if meta.slug == p.slug:
                    page_path = meta.path
                    break
            if page_path and page_path.exists():
                post = frontmatter.load(page_path)
                post["promoted_to_ltm"] = True
                _dump(post, page_path)
        except Exception:
            pass  # non bloquant

    # ------------------------------------------------------------ expiration

    def _expire_stale(self) -> list[str]:
        """Expire les pages dont last_accessed > TTL jours."""
        expired: list[str] = []
        cutoff = date.today() - timedelta(days=self.cfg.ttl_days)
        for page in self.stm.list_pages():
            if page.last_accessed < cutoff:
                self.stm.expire(page.slug)
                expired.append(page.slug)
        return expired

    # ------------------------------------------------------- consolidation

    def _consolidate_if_oversized(self) -> list[str]:
        """Déclenche la consolidation si le wiki dépasse max_stm_pages.

        Phase 1 : placeholder — retourne la liste des slugs candidats
        sans effectuer de fusion (implémentation complète Phase 2).
        """
        pages = self.stm.list_pages()
        if len(pages) <= self.cfg.max_stm_pages:
            return []
        # Candidats : pages les moins accédées, non promues
        candidates = sorted(
            [p for p in pages if not p.promoted_to_ltm],
            key=lambda p: (p.access_count, p.last_accessed),
        )
        # Pour l'instant : juste signaler, pas fusionner
        return [p.slug for p in candidates[: len(pages) - self.cfg.max_stm_pages]]

    # ------------------------------------------------------------------- log

    def _log_report(self, report: GovReport) -> None:
        self.stm._log(
            f"govern: {len(report.promoted)} promu(s), "
            f"{len(report.expired)} expiré(s), "
            f"{len(report.consolidated)} consolidé(s)"
        )


# ------------------------------------------------------------------ helpers


def _infer_label(slug: str, pages: list[PageMeta]) -> str:
    """Heuristique simple : cherche le type de la page STM correspondante."""
    type_to_label = {
        "entity": "Concept",   # MVP : tout entity -> Concept
        "concept": "Concept",
        "source": "Idea",
        "synthesis": "Concept",
    }
    for page in pages:
        if page.slug == slug:
            return type_to_label.get(page.type, "Concept")
    return "Concept"


def _humanize(slug: str) -> str:
    """Convertit un slug en nom lisible : pergola-jardin -> Pergola jardin."""
    return slug.replace("-", " ").capitalize()


def _first_para(content: str) -> str:
    """Retourne le premier paragraphe non-frontmatter du contenu."""
    lines = content.splitlines()
    paras: list[str] = []
    in_fm = False
    for line in lines:
        if line.strip() == "---":
            in_fm = not in_fm
            continue
        if in_fm:
            continue
        if line.startswith("#"):
            continue
        if line.strip():
            paras.append(line.strip())
        elif paras:
            break
    return " ".join(paras)[:500]

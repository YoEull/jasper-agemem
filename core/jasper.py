"""
jasper — CLI point d'entrée du second brain.

Commandes :
    jasper capture "<texte>"     append à inbox/inbox.md
    jasper ingest                inbox -> STM (parse + wiki)
    jasper govern                passe de gouvernance STM<->LTM
    jasper ask "<question>"      retrieval hybride + affichage contexte
    jasper status                stats STM/LTM

Toutes les dépendances (chemins, config) sont résolubles depuis le
répertoire courant ou une variable d'env JASPER_ROOT.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(
    name="jasper",
    help="Second brain personnel (STM wiki + LTM graphe).",
    add_completion=False,
)


# ------------------------------------------------------------------ résolution racine


def _root() -> Path:
    """Répertoire racine du projet (JASPER_ROOT ou cwd)."""
    return Path(os.environ.get("JASPER_ROOT", ".")).resolve()


def _inbox_path(root: Path) -> Path:
    return root / "inbox" / "inbox.md"


def _wiki_dir(root: Path) -> Path:
    return root / "stm" / "wiki"


def _ltm_path(root: Path) -> Path:
    return root / "ltm" / "jasper.ryu"


def _make_stm(root: Path):
    from governance.stm_manager import STMManager
    return STMManager(_wiki_dir(root))


def _make_ltm(root: Path, embedding_dim: int = 1024):
    from governance.ltm_store import LTMStore
    return LTMStore(_ltm_path(root), embedding_dim=embedding_dim)


# ------------------------------------------------------------------ capture


@app.command()
def capture(
    texte: Annotated[str, typer.Argument(help="Texte à capturer dans l'inbox.")],
) -> None:
    """Ajoute une entrée horodatée dans inbox/inbox.md."""
    root = _root()
    inbox = _inbox_path(root)
    inbox.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().isoformat(timespec="seconds")
    with inbox.open("a", encoding="utf-8") as f:
        f.write(f"\n--- {ts} ---\n{texte.strip()}\n")
    typer.echo(f"✓ Capturé @ {ts}")


# ------------------------------------------------------------------ ingest


@app.command()
def ingest(
    extract: Annotated[bool, typer.Option(
        "--extract/--no-extract",
        help="Extraction d'entités via Claude API.",
    )] = False,
) -> None:
    """Parse l'inbox et met à jour le wiki STM."""
    root = _root()
    from core.inbox_parser import extract_entities, parse_inbox
    from governance.stm_manager import STMManager

    entries = parse_inbox(_inbox_path(root))
    if not entries:
        typer.echo("Inbox vide — rien à ingérer.")
        raise typer.Exit()

    stm = _make_stm(root)
    total_pages = 0
    for entry in entries:
        if extract:
            entry.entities = extract_entities(entry.text)
        paths = stm.ingest(entry)
        total_pages += len(paths)

    typer.echo(
        f"✓ {len(entries)} entrée(s) ingérée(s) → {total_pages} page(s) STM maj."
    )


# ------------------------------------------------------------------ govern


@app.command()
def govern() -> None:
    """Exécute la passe de gouvernance STM ↔ LTM."""
    root = _root()
    from governance.config import GovConfig
    from governance.governor import Governor

    stm = _make_stm(root)
    ltm = _make_ltm(root)
    try:
        gov = Governor(stm, ltm, GovConfig())
        report = gov.govern()
        typer.echo(
            f"✓ Gouvernance terminée :\n"
            f"   Promus   : {len(report.promoted)}\n"
            f"   Expirés  : {len(report.expired)}\n"
            f"   Consolidés: {len(report.consolidated)}"
        )
        for p in report.promoted:
            typer.echo(f"   ↑ {p.slug} ({p.label})")
    finally:
        ltm.close()


# ------------------------------------------------------------------ ask


@app.command()
def ask(
    question: Annotated[str, typer.Argument(help="Question en langage naturel.")],
    k_stm: Annotated[int, typer.Option(help="Nombre max de pages STM.")] = 5,
    k_ltm: Annotated[int, typer.Option(help="Nombre max de nœuds LTM.")] = 10,
) -> None:
    """Retrieval hybride STM+LTM et affichage du contexte retrouvé."""
    root = _root()
    from governance.retrieval import HybridRetriever

    stm = _make_stm(root)
    ltm = _make_ltm(root)
    try:
        retriever = HybridRetriever(stm, ltm)
        result = retriever.retrieve(question, k_stm=k_stm, k_ltm=k_ltm)

        typer.echo(f"\n── Contexte STM ({len(result.stm_pages)} page(s)) ──")
        for page in result.stm_pages:
            first_line = page.splitlines()[0] if page.splitlines() else "(vide)"
            typer.echo(f"  {first_line[:80]}")

        typer.echo(f"\n── Contexte LTM ({len(result.ltm_nodes)} nœud(s)) ──")
        for node in result.ltm_nodes:
            typer.echo(
                f"  [{node.get('n.label','')}] {node.get('n.name','')} "
                f"— {node.get('n.definition','')[:60]}"
            )
        if result.ltm_edges:
            typer.echo(f"\n── Relations ({len(result.ltm_edges)}) ──")
            for edge in result.ltm_edges:
                typer.echo(
                    f"  {edge.get('src')} -{edge.get('rel')}-> {edge.get('dst')}"
                )
    finally:
        ltm.close()


# ------------------------------------------------------------------ status


@app.command()
def status() -> None:
    """Affiche les stats du second brain (STM pages, LTM nœuds, edges)."""
    root = _root()
    stm = _make_stm(root)
    ltm = _make_ltm(root)
    try:
        pages = stm.list_pages()
        promoted = sum(1 for p in pages if p.promoted_to_ltm)

        nodes = ltm.cypher("MATCH (n:JasperNode) RETURN COUNT(n) AS cnt")
        node_count = nodes[0]["cnt"] if nodes else 0

        edge_count = 0
        from governance.ltm_store import EDGE_TYPES
        for etype in EDGE_TYPES:
            rows = ltm.cypher(
                f"MATCH ()-[e:{etype}]->() RETURN COUNT(e) AS cnt"
            )
            if rows:
                edge_count += rows[0]["cnt"]

        typer.echo(
            f"── Jasper Status ──────────────────\n"
            f"  STM pages       : {len(pages)}\n"
            f"  STM promus LTM  : {promoted}\n"
            f"  LTM nœuds       : {node_count}\n"
            f"  LTM relations   : {edge_count}\n"
            f"  Inbox           : {_inbox_path(root)}\n"
            f"  Wiki            : {_wiki_dir(root)}\n"
            f"  LTM DB          : {_ltm_path(root)}"
        )
    finally:
        ltm.close()


# ------------------------------------------------------------------ main


def main() -> None:
    app()


if __name__ == "__main__":
    main()

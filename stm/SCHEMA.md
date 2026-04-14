# Schéma du wiki STM

> Règles d'écriture du wiki Karpathy pour jasper-agemem.
> Le LLM est le libraire : ces règles sont appliquées par
> `governance/stm_manager.py`. L'humain ne modifie pas ces fichiers
> à la main — il capture dans `inbox/inbox.md` et lit le wiki.

## Types de pages

| Dossier            | Type       | Contenu                                    |
|--------------------|------------|--------------------------------------------|
| `wiki/sources/`    | source     | résumé d'une entrée inbox ou doc ingérée   |
| `wiki/entities/`   | entity     | personne, lieu, projet, outil              |
| `wiki/concepts/`   | concept    | idée, framework, thème                     |
| `wiki/synthesis/`  | synthesis  | comparaisons, patterns transversaux        |

Un fichier = un sujet canonique. Pas de pages fourre-tout.

## Frontmatter obligatoire

```yaml
---
id: pergola-jardin               # slug, = nom de fichier sans .md
type: concept                    # source | entity | concept | synthesis
created_at: 2026-04-14
last_accessed: 2026-04-14
access_count: 1
sources: [inbox-2026-04-14-2230] # ids des sources d'origine
links: [maison, cedre-rouge]     # backlinks sortants (slugs)
tags: [idée, maison]             # tags inbox propagés
promoted_to_ltm: false           # true après passe Governor
---
```

## Corps

- Titre H1 = nom lisible (le slug est dans le frontmatter).
- Liens internes en `[[slug]]` (parsés pour maintenir `links`).
- Paragraphes courts, bullet points autorisés.
- Pas de citations brutes longues : résumer, paraphraser.

## Fichiers spéciaux

- `wiki/index.md` : catalogue régénéré à chaque écriture (liste par
  type, nombre d'accès, derniers ajouts). **Toujours chargé dans le
  contexte** lors d'un retrieval.
- `wiki/log.md` : historique append-only des opérations (ingest,
  promote, expire, consolidate).
- `wiki/_expired/` : archivage des pages expirées (TTL dépassé).

## Invariants

1. Le slug est unique dans tout le wiki (pas de collision entre dossiers).
2. Chaque page référencée dans `links` doit exister.
3. `last_accessed >= created_at`.
4. Après promotion LTM, la page reste dans STM jusqu'à expiration TTL.

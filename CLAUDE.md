# CLAUDE.md — jasper-agemem

Config pour agents Claude Code opérant sur ce dépôt.

## Contexte projet

Second brain personnel, architecture AgeMem adaptée :
- **STM** : wiki markdown Karpathy (`stm/wiki/`), chargeable en
  context window. Le LLM est libraire, l'humain capture et lit.
- **LTM** : graphe RyuGraph embarqué (`ltm/`), Cypher + vecteurs.
- **Gouvernance** : `governance/governor.py` décide promotion /
  expiration / consolidation.
- **Inbox** : `inbox/inbox.md` append-only, point d'entrée unique.

Voir `ARCHITECTURE.md` pour le design complet.

## Conventions

- Python 3.12, **code et commentaires en français**.
- Tests pytest dans `tests/`, coverage minimum **70%** (bloquant review).
- Offline par défaut, seule dépendance cloud = Claude API.
- Pas de modification manuelle de `stm/wiki/**` — passer par
  `STMManager`. Seul `inbox/inbox.md` est édité directement (par
  l'humain ou la commande `jasper capture`).

## Pattern multi-agent (Cowork / Task tool)

Ordre d'implémentation Phase 1.3 :
1. `core/inbox_parser.py`
2. `governance/stm_manager.py`
3. `governance/ltm_store.py`
4. `governance/governor.py`
5. `governance/retrieval.py`
6. `core/jasper.py` (CLI)

Pour chaque module :
- **Coder agent** : implémentation selon API dans ARCHITECTURE.md §3.
- **Tester agent** : pytest, cible 70%+ coverage.
- **Reviewer agent** : bloque si coverage < 70%, flag design issues.

Les agents sont spawnés via la Task tool, séquentiels entre phases,
parallèles sur tâches indépendantes (ex: tests + review d'un module
déjà livré, pendant que le coder attaque le suivant).

## Commandes utiles (prévues Phase 1.3+)

```
jasper capture "<texte>"     # append inbox
jasper ingest                 # inbox -> STM wiki
jasper govern                 # passe STM <-> LTM
jasper ask "<question>"       # retrieval hybride
jasper status                 # stats
```

## Ce que les agents ne doivent PAS faire

- Créer des pages wiki directement (utiliser `STMManager`).
- Ajouter des dépendances cloud (hors Claude API).
- Écrire en anglais dans le code ou les commentaires.
- Passer en revue sans lancer pytest+coverage.
- Mocker la base LTM dans les tests d'intégration — utiliser un
  dossier `ltm/` temporaire (RyuGraph est embarqué, c'est peu coûteux).

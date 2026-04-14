# ARCHITECTURE — jasper-agemem

> Second brain personnel inspiré d'AgeMem (arXiv 2601.01885), avec
> STM style LLM-wiki Karpathy (avril 2026) et LTM graphe embarqué.
> Tout est local, offline, hors appels Claude API pour la gouvernance.

---

## 1. Vue d'ensemble

```
                        ┌──────────────┐
                        │   UTILISATEUR │
                        └──────┬───────┘
                               │ texte libre + tags
                               ▼
                   ┌───────────────────────┐
                   │   inbox/inbox.md      │  append-only, séparateurs ---
                   │   (point d'entrée)    │
                   └───────────┬───────────┘
                               │ jasper ingest
                               ▼
                ┌──────────────────────────────┐
                │   core/inbox_parser.py       │
                │   - découpage entrées        │
                │   - extraction tags          │
                │   - extraction entités (LLM) │
                └──────────────┬───────────────┘
                               │ Entry(tags, entities, text)
                               ▼
                ┌──────────────────────────────┐
                │   governance/stm_manager.py  │
                │   wiki Karpathy : écrit /    │
                │   met à jour pages markdown  │
                └───┬────────────────────┬─────┘
                    │                    │
            (lecture directe)     (promotion)
                    │                    │
                    ▼                    ▼
        ┌─────────────────┐   ┌────────────────────────┐
        │ stm/wiki/*.md   │   │ governance/governor.py │
        │ + index.md      │◄──┤ TTL / seuils / expiry  │
        │ + log.md        │   └──────────┬─────────────┘
        └────────┬────────┘              │ promotion STM→LTM
                 │                       ▼
                 │          ┌────────────────────────┐
                 │          │ governance/ltm_store.py│
                 │          │ RyuGraph (Cypher +     │
                 │          │ vecteurs embarqués)    │
                 │          └──────────┬─────────────┘
                 │                     │
                 │                     ▼
                 │          ┌────────────────────────┐
                 │          │ ltm/ryu.db (fichiers)  │
                 │          └──────────┬─────────────┘
                 │                     │
                 └──────────┬──────────┘
                            ▼
                ┌──────────────────────────────┐
                │ governance/retrieval.py      │
                │ hybride : STM (context direct)│
                │  + LTM (Cypher + vecteurs)    │
                └──────────────┬───────────────┘
                               ▼
                        ┌──────────────┐
                        │   RÉPONSE     │
                        └──────────────┘
```

---

## 2. Composants

### 2.1 Inbox
- **Fichier unique** `inbox/inbox.md`, append-only.
- Format d'une entrée :
  ```
  --- 2026-04-14T22:30:00 ---
  idée: maison: pergola jardin côté sud, cèdre rouge,
  mesurer largeur mur avant devis menuisier
  ```
- Les tags `mot:` en début d'entrée sont libres mais conventionnels :
  `idée:`, `maison:`, `tech:`, `trip:`, `perso:`, `todo:`.
- Traitement déclenché par `jasper ingest` (pas de watch automatique).

### 2.2 STM — wiki Karpathy
Pattern strict : **le LLM est le libraire**. L'humain n'édite jamais
manuellement le wiki ; il lit et capture dans l'inbox.

```
stm/
├── raw/                  sources brutes ingérées (archivage, pas lu)
├── wiki/
│   ├── sources/          une page résumée par source (inbox entry ou doc)
│   ├── entities/         personnes, lieux, projets, outils
│   ├── concepts/         idées, frameworks, thèmes
│   ├── synthesis/        comparaisons, patterns transversaux
│   ├── index.md          catalogue master, backlinks, toujours en contexte
│   └── log.md            historique des opérations (ingest, promote, expire)
└── SCHEMA.md             règles du wiki (types, frontmatter, backlinks)
```

**Frontmatter de chaque page** :
```yaml
---
id: pergola-jardin
type: concept            # source | entity | concept | synthesis
created_at: 2026-04-14
last_accessed: 2026-04-14
access_count: 1
sources: [inbox-2026-04-14-2230]
links: [maison, cedre-rouge, menuisier]
tags: [idée, maison]
promoted_to_ltm: false
---
```

**Règles d'écriture** (appliquées par `stm_manager.py` via Claude) :
- Titre H1 = nom canonique (slugifié = `id`).
- Une page = un concept/entité/source. Pas de pages fourre-tout.
- Les liens internes utilisent `[[slug]]` (backlinks auto).
- Mise à jour incrémentale : si la page existe, Claude réécrit en
  intégrant la nouvelle info ; sinon création.
- `index.md` regénéré à chaque écriture.

### 2.3 LTM — RyuGraph
Backend : **RyuGraph** (fork Kuzu maintenu), embarqué, Python bindings,
Cypher + vecteurs natifs, stockage fichiers dans `ltm/ryu.db/`.

**Nodes** (label + propriétés) :

| Label    | Propriétés clés                                            |
|----------|------------------------------------------------------------|
| Person   | id, name, aliases[], created_at, last_accessed, confidence |
| Place    | id, name, aliases[], lat?, lon?, created_at, last_accessed |
| Project  | id, name, status, created_at, last_accessed                |
| Concept  | id, name, definition, created_at, last_accessed            |
| Tool     | id, name, url?, created_at, last_accessed                  |
| Event    | id, name, date, created_at                                 |
| Idea     | id, name, content, created_at, last_accessed               |

Propriétés communes à tous : `source` (origine STM page id),
`confidence` (0..1), `embedding` (vecteur 1024 pour recherche sémantique).

**Edges** (type + propriétés) :

| Type          | Sémantique                                    |
|---------------|-----------------------------------------------|
| RELATED_TO    | lien générique, symétrique                    |
| PART_OF       | composition (Project PART_OF Project)         |
| CREATED_BY    | (Idea | Project) CREATED_BY Person            |
| HAPPENED_AT   | Event HAPPENED_AT Place                       |
| CONTRADICTS   | désaccord explicite entre deux nœuds          |
| REINFORCES    | accord / renforcement                         |
| EVOLVED_FROM  | filiation temporelle (Idea EVOLVED_FROM Idea) |

Propriétés d'edge : `created_at`, `weight` (0..1), `source` (page STM).

### 2.4 Gouvernance

Déclencheurs :
- **synchrone** (à l'ingest) : extraction entités légère + écriture STM
- **manuel** : `jasper govern` exécute la passe complète ci-dessous

**Règles (par défaut, modifiables dans `governance/config.py`)** :

| Règle                                   | Seuil défaut |
|-----------------------------------------|--------------|
| TTL page STM sans accès → candidat LTM  | 30 jours     |
| Taille max wiki                         | 50 pages     |
| Entité apparue dans N+ pages → promote  | 3            |
| Confidence minimale pour edge auto      | 0.6          |
| Compression forcée si > max_pages       | consolidation synthesis |

**Flux de `jasper govern`** :
```
1. SCAN stm/wiki/*.md → collecte métadonnées (last_accessed, links)
2. DÉTECTION entités fréquentes (seuil N) via comptage + alias resolver (LLM)
3. PROMOTION : pour chaque entité éligible
     3a. upsert node dans LTM
     3b. créer edges RELATED_TO vers co-occurrences
     3c. marquer promoted_to_ltm: true dans page STM
4. EXPIRATION : pages TTL dépassé
     4a. résumé 1-ligne intégré dans entité LTM si lien
     4b. archivage stm/wiki/_expired/
5. CONSOLIDATION (si |wiki| > max_pages)
     5a. clustering thématique via embeddings
     5b. création pages synthesis/, suppression originales
6. LOG ops dans stm/wiki/log.md
```

### 2.5 Retrieval hybride

Une requête utilisateur (`jasper ask "..."`) :
```
1. STM direct : charger index.md + pages matchant tags/slugs de la requête
2. LTM Cypher : extraire sous-graphe pertinent (Cypher + filtre vecteur)
3. Fusion : passer les deux blocs à Claude en contexte
4. Mise à jour last_accessed / access_count pour pages STM touchées
```

---

## 3. API Python par module

### 3.1 `core/inbox_parser.py`
```python
@dataclass
class InboxEntry:
    timestamp: datetime
    tags: list[str]
    text: str
    entities: list[str]   # extraites via LLM, optionnel

def parse_inbox(path: Path) -> list[InboxEntry]: ...
def parse_entry(raw: str) -> InboxEntry: ...
def extract_entities(text: str) -> list[str]: ...  # Claude
```

### 3.2 `governance/stm_manager.py`
```python
class STMManager:
    def __init__(self, wiki_dir: Path): ...
    def ingest(self, entry: InboxEntry) -> list[Path]:
        """Crée/met à jour pages wiki. Retourne pages touchées."""
    def read_page(self, slug: str) -> str: ...
    def touch(self, slug: str) -> None:
        """Incrémente access_count, maj last_accessed."""
    def rebuild_index(self) -> None: ...
    def list_pages(self) -> list[PageMeta]: ...
    def expire(self, slug: str) -> None: ...
```

### 3.3 `governance/ltm_store.py`
```python
class LTMStore:
    def __init__(self, db_path: Path): ...
    def upsert_node(self, label: str, props: dict) -> str: ...
    def upsert_edge(self, src: str, edge: str, dst: str,
                    props: dict) -> None: ...
    def cypher(self, query: str, params: dict = {}) -> list[dict]: ...
    def vector_search(self, embedding: list[float],
                      k: int = 10) -> list[dict]: ...
    def close(self) -> None: ...
```

### 3.4 `governance/governor.py`
```python
class Governor:
    def __init__(self, stm: STMManager, ltm: LTMStore,
                 config: GovConfig): ...
    def govern(self) -> GovReport:
        """Exécute la passe complète : promote, expire, consolider."""
    def _detect_promotions(self) -> list[Promotion]: ...
    def _promote(self, p: Promotion) -> None: ...
    def _expire_stale(self) -> list[str]: ...
    def _consolidate_if_oversized(self) -> None: ...
```

### 3.5 `governance/retrieval.py`
```python
class HybridRetriever:
    def __init__(self, stm: STMManager, ltm: LTMStore): ...
    def retrieve(self, query: str, k_stm: int = 10,
                 k_ltm: int = 10) -> RetrievalResult:
        """Retourne {stm_pages: [...], ltm_subgraph: {...}}."""
```

### 3.6 `core/jasper.py` (CLI)
```
jasper capture "idée: maison: pergola..."   # append inbox
jasper ingest                                 # inbox → STM
jasper govern                                 # passe gouvernance
jasper ask "que sais-je sur la pergola ?"    # retrieval + réponse
jasper status                                 # stats STM/LTM
```

---

## 4. Exemple bout-en-bout : "pergola jardin"

**T0 — capture** :
```
$ jasper capture "idée: maison: pergola jardin côté sud, cèdre rouge,
                   mesurer largeur mur avant devis menuisier"
```
→ append dans `inbox/inbox.md` :
```
--- 2026-04-14T22:30:00 ---
idée: maison: pergola jardin côté sud, cèdre rouge,
mesurer largeur mur avant devis menuisier
```

**T1 — ingest** :
```
$ jasper ingest
```
- `parse_inbox` → `InboxEntry(tags=[idée, maison],
                               text="pergola...",
                               entities=["pergola", "cèdre rouge",
                                         "menuisier", "jardin"])`
- `stm_manager.ingest(entry)` crée/maj :
  - `stm/wiki/sources/inbox-2026-04-14-2230.md` (résumé source)
  - `stm/wiki/concepts/pergola-jardin.md` (idée)
  - `stm/wiki/entities/cedre-rouge.md`
  - `stm/wiki/entities/menuisier.md`
  - `stm/wiki/entities/jardin-sud.md`
- Chaque page contient backlinks `[[...]]` et frontmatter.
- `index.md` regénéré.

**T2 — ajouts suivants** (jours plus tard, 2 autres entrées
mentionnent `pergola`) :
- Après 3+ pages citant `pergola`, `governor.govern()` détecte la
  promotion.

**T3 — govern** :
```
$ jasper govern
[promote] Concept "pergola-jardin" → LTM (3 pages citantes)
[promote] Entity "cèdre rouge" → LTM (co-occurrence 3x)
[edges ] pergola-jardin RELATED_TO cèdre-rouge (weight=0.8)
[edges ] pergola-jardin PART_OF projet-maison
```
Dans LTM (Cypher) :
```cypher
CREATE (:Concept {id:'pergola-jardin', name:'pergola jardin',
                  definition:'...', embedding:[...]})
CREATE (:Tool {id:'cedre-rouge', name:'cèdre rouge'})
MATCH (a {id:'pergola-jardin'}), (b {id:'cedre-rouge'})
CREATE (a)-[:RELATED_TO {weight:0.8, source:'concepts/pergola-jardin'}]->(b)
```

**T4 — ask** :
```
$ jasper ask "qu'est-ce que j'avais décidé pour la pergola ?"
```
- STM : `concepts/pergola-jardin.md` chargé
- LTM Cypher : sous-graphe `(pergola-jardin)-[*1..2]-()`
- Claude répond avec contexte fusionné.

---

## 5. Décisions ouvertes (à réviser)
- Modèle d'embeddings local vs API (défaut : `bge-m3` local via
  sentence-transformers pour rester offline).
- Seuil de promotion (3 pages) à calibrer après usage réel.
- Format de `log.md` : JSONL vs markdown humain (défaut : markdown).
- Gestion conflits (CONTRADICTS) : détection auto vs balisage manuel
  dans l'inbox (`contre:` tag).

---

## 6. Contraintes respectées
- Offline sauf Claude API (extraction entités, gouvernance, résumés).
- Zéro serveur : RyuGraph embarqué, markdown fichiers plats.
- Chaque module testable isolément (mocks Claude API dans tests).
- Code et commentaires en français.

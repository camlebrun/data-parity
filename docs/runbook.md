# Runbook : prouver qu'une table est identique sur deux moteurs avec data-parity

Objectif : prouver que la table T du moteur A (ex. BigQuery) et celle du moteur B (ex. DuckDB, Trino) sont
identiques, **sans déplacer de données** : chaque moteur n'affiche que des comptes et des sommes de hash par ligne,
et on compare ces petites sorties. L'outil `data-parity` (Python, bibliothèque standard seulement) appelle dbt des
deux côtés, compare, descend là où ça diffère et écrit un récapitulatif Markdown (`parity.md`) et un JSON
(`parity.json`). Il fonctionne pareil sous Windows (PowerShell), macOS et Linux ; les commandes ci-dessous sont
identiques dans PowerShell, bash et zsh sauf mention contraire.

Contraintes : aucune donnée ne sort d'un moteur (seulement comptes et sommes de hash) ; aucune clé n'est affichée
par défaut (seulement valeurs de partition, numéros de bucket, noms de colonnes en écart) ; dbt d'abord ; tables de
plusieurs To (premier passage = un seul scan groupé, bornable) ; aucun catalogue système du moteur n'est supposé.

Pourquoi pas `audit_helper` seul : il compare deux relations visibles par UNE connexion dbt (même entrepôt). Il ne
sait pas comparer BigQuery et DuckDB, ni Trino et BigQuery, sans déplacer les données, et ses comparaisons ligne à
ligne passent par des `EXCEPT` / jointures sur les lignes.

## 0. Pré-requis

- Python 3.9 ou plus récent (aucune dépendance Python à installer).
- dbt installé pour CHAQUE moteur (dbt Fusion ou dbt-core pour DuckDB et BigQuery ; **dbt-core seulement pour Trino**,
  Fusion ne supporte pas Trino). Les deux côtés peuvent utiliser des binaires dbt différents (`--dbt-a`, `--dbt-b`).
- Des droits de lecture sur les tables, rien d'autre : le mode normal n'écrit rien (le cache est optionnel et
  désactivé par défaut).
- Les deux côtés ont fini leur run, sinon de faux écarts de fraîcheur (voir triage, hypothèse 1).

## 1. Installer

Le package dbt, dans le `packages.yml` du projet dbt, puis `dbt deps` :

```yaml
packages:
  - git: "https://github.com/camlebrun/data-parity"
    subdirectory: dbt_package
    # revision: <tag ou commit>     # à figer dès qu'on s'appuie dessus
```

L'outil en ligne de commande, depuis un clone (pas encore de publication PyPI, donc pas de `pipx install data-parity`) :

```text
git clone https://github.com/camlebrun/data-parity
cd data-parity
pip install .
data-parity help
```

Sans rien installer : `python -m data_parity help` avec `src` dans le chemin Python.

```text
# bash / zsh
PYTHONPATH=/chemin/vers/data-parity/src python -m data_parity help
# PowerShell
$env:PYTHONPATH = "C:\chemin\vers\data-parity\src"; python -m data_parity help
```

## 2. Déclarer ce qui est comparé

Par modèle (yml). **Avec dbt Fusion, `tags` et `meta` vont sous `config:`**, sinon ils sont ignorés :

```yaml
models:
  - name: orders
    config:
      tags: [data_parity]
      event_time: order_date            # sert de colonne de partition si partition_column est absent
      meta:
        data_parity:
          primary_key: [order_id]       # sinon déduite d'un test unique / unique_combination_of_columns
          partition_column: order_date
          exclude_columns: [raw_payload]  # chaîne ou liste ; obligatoire pour array, struct, json, bytes...
          exclude_prefixes: [meta_]       # défaut : meta_ ; chaîne ou liste, préfixe vide refusé
          enabled: true
```

- Désactiver un modèle : `meta: {data_parity: false}` ou `enabled: false`.
- Sans clé primaire : les buckets viennent du hash de la ligne entière et les colonnes sont comparées sur toute la
  partition (une ligne modifiée apparaît dans deux buckets).
- Sans colonne de partition : le passage 1 n'affiche que la ligne `table` (comparaison au niveau de la table).
- Un type non supporté (array, struct, map, json, bytes, interval, geography) arrête le run avec une erreur de
  compilation : l'exclure via `exclude_columns`.
- Les mêmes colonnes, les mêmes noms et les mêmes types logiques doivent exister des deux côtés ; sinon les lignes
  `cols|` diffèrent et le rapport signale une différence de structure.

## 3. Étapes

Se placer dans le dossier du projet dbt (les chemins DuckDB des profils y sont relatifs), ou donner `--project DIR`.
Les cibles `a` et `b` ci-dessous sont les cibles dbt de chaque moteur. On peut les mettre une fois pour toutes dans
`./.data-parity.env` (clés `A=`, `B=`, `PROJECT_B=` seulement ; le fichier est lu, jamais exécuté) :

```text
A=dev
B=local
```

### 3.1 Conformance sur les deux moteurs (toujours en premier sur un nouveau couple de moteurs)

```text
data-parity conformance --a dev --b local
```

79 lignes synthétiques en ligne : aucune table lue ni créée. Résultat attendu :
`IDENTICAL: the hash is portable between dev and local (79 rows)`, code de sortie 0. Si la sortie est
`DIFFERENT: the hash is NOT identical ...`, ne pas aller plus loin : le hash n'est pas comparable sur ce couple de
moteurs (voir `docs/canonical-spec.md`).

### 3.2 Comparer

Un modèle d'abord, pour valider la config :

```text
data-parity run orders --a dev --b local
```

Puis tout le projet (tous les modèles taggés `data_parity`) :

```text
data-parity run --a dev --b local
```

Options utiles : `--dbt-a` / `--dbt-b` (binaire dbt de chaque côté, ex. Fusion d'un côté, dbt-core de l'autre),
`--project` / `--project-b`, `--profiles-dir-a` / `--profiles-dir-b`, `--buckets` (256), `--as-of`, `--last N`,
`--drill-limit` (200), `--detail N` (15 au plus en ligne ; la liste complète reste dans `parity.json`), `--out DIR`, `--report FILE`, `--json FILE`. `data-parity report RUN_DIR [--out FILE]` régénère `parity.md` depuis un dossier de run (`parity.json`, `p1_A.txt`, `p1_B.txt` ; code 2 s'ils manquent) sans appeler dbt. Un run fait par une ancienne version contient un échantillon des N premiers jours : la phrase « spread evenly » régénérée y est inexacte, seul un run neuf donne un vrai échantillon. Au-delà de `--drill-limit`, l'échantillon est réparti régulièrement du premier au dernier jour en écart (premier et dernier inclus) et le rapport dit « column attribution is based on a sample of N of K differing days, spread evenly from F to L » ; les parts de colonnes sont relatives aux jours échantillonnés. Exemple Trino contre DuckDB :

```text
data-parity run --a duck --b trino --dbt-a dbt-fusion --dbt-b dbt-core --project-b ./projet_trino --profiles-dir-b ./projet_trino
```

**Codes de sortie** : 0 = identique, 1 = différences, 2 = erreur (échec dbt d'un côté, cible ou modèle inconnu,
mauvais usage). Une erreur ne donne jamais 0 ni 1. Un périmètre partiel (`--last`, `--as-of`, partitions non
descendues) donne 0 avec le verdict « ISO on the checked scope » et `scope_limited: true` dans le JSON : ce n'est
jamais « ISO » tout court.

Le résultat est écrit dans un sous-dossier neuf `run-<horodatage>` de `--out` (défaut : dossier temporaire du système,
`data-parity/<projet>`) : `parity.md`, `parity.json`, et les sorties brutes `p1_A.txt`, `p1_B.txt`... Les journaux bruts
dbt (`raw_*.log`) ne sont écrits que pour un appel dbt en échec. Le chemin du rapport est affiché à la fin.

Les trois passages (lancés par l'outil, un par un, seulement là où c'est nécessaire) :

| passage | requête sur chaque moteur | périmètre |
|---|---|---|
| 1 | UN scan groupé : par partition `rows`, `sum(h1)`, `sum(h2)` ; la ligne `table` est la somme des partitions | toute la table |
| 2 | par (partition, bucket), bucket = hash de la clé primaire modulo 256 | partitions en écart |
| 3 | par colonne : somme du hash de [clé + colonne], par bucket | buckets de même nombre de lignes mais hash différent |

Un modèle dont le passage 1 est égal n'est plus jamais scanné.

**Tables de plusieurs To** : le passage 1 lit toutes les colonnes hachées de toutes les lignes (sur BigQuery, les
octets de toutes ces colonnes). Le borner avec `--last N` et fixer `--as-of` pour écarter un décalage de fraîcheur.
Le rapport indique alors ce qui n'a PAS été vérifié (section « Not checked »).

#### Sans l'outil : le macro à la main

Le macro `data_parity_reconcile` accepte (tous optionnels) : `only` (liste de modèles), `level` (1, 2 ou 3 : passage le
plus profond), `drill` (`{modèle: [partition, ...]}`), `drill_buckets` (`{modèle: [[partition, bucket], ...]}`),
`buckets` (défaut 256, **identique des deux côtés**), `as_of` (exclut les partitions `>=` `as_of` et les partitions
NULL), `partitions` (`{last: N}` ou liste explicite).

**Piège du faux « identique »** : si dbt échoue, les deux fichiers sont vides et un `diff` simple dit « identiques ».
Vérifier le code de sortie de dbt ET que la sortie n'est pas vide.

bash / zsh :

```bash
for t in a b; do
  dbt run-operation data_parity_reconcile --target "$t" --args '{only: [orders], level: 1}' > "$t.log" 2>&1 \
    || { echo "dbt failed on $t"; exit 1; }
  sed -n 's/.*PARITY|//p' "$t.log" > "$t.txt"
  [ -s "$t.txt" ] || { echo "no PARITY output on $t"; exit 1; }
done
diff a.txt b.txt && echo IDENTIQUE
```

PowerShell (les accolades YAML de `--args` se passent entre apostrophes ; sur Windows PowerShell 5.1, si dbt reçoit
mal les guillemets, utiliser l'outil, qui ne passe par aucun shell) :

```powershell
foreach ($t in 'a', 'b') {
  dbt run-operation data_parity_reconcile --target $t --args '{only: [orders], level: 1}' *> "$t.log"
  if ($LASTEXITCODE -ne 0) { throw "dbt failed on $t" }
  Select-String -Path "$t.log" -Pattern 'PARITY\|(.*)$' |
    ForEach-Object { $_.Matches[0].Groups[1].Value } | Set-Content "$t.txt"
  if (-not (Test-Path "$t.txt") -or (Get-Item "$t.txt").Length -eq 0) { throw "no PARITY output on $t" }
}
if (Compare-Object (Get-Content a.txt) (Get-Content b.txt)) { "DIFFERENT" } else { "IDENTIQUE" }
```

### 3.3 Lire `parity.md`

- Bandeau de verdict en tête : « Identical », « Identical on a limited scope », « Differences found » ou « No verdict »
  (erreur, rien n'est prouvé).
- Tableau d'en-tête : date, moteurs et cibles, ce qui est comparé, périmètre, modèles.
- **Not checked** : ce qui n'a pas été vérifié (partitions plus anciennes avec `--last`, partitions exclues par
  `--as-of`, partitions non descendues à cause de `--drill-limit`, colonnes exclues par configuration, modèles non taggés).
- **Overview** : une ligne par modèle (identique, différent, structure), lignes de chaque côté, delta, partitions en écart.
- Pour chaque modèle en écart : un diagnostic en une phrase, les colonnes en écart classées, la nature des écarts
  (« changed values » : même nombre de lignes, hash différents ; « missing or extra rows »), la chronologie, le
  tableau des partitions en écart avec buckets et colonnes (15 au plus en ligne, choisis parmi les jours échantillonnés ; « not sampled » seulement s'il y en a moins de 15).
- Un bucket qui contient à la fois une ligne manquante et une valeur modifiée n'a pas de colonne attribuée
  (le passage 3 ne tourne que sur les buckets de même nombre de lignes).
- **How to reproduce** : la commande exacte, et ce que la vérification prouve et ne prouve pas.

Le rapport ne contient que des comptes, des sommes de hash, des valeurs de partition, des numéros de bucket et des
noms de colonnes. Pour un script, lire `parity.json` plutôt que le Markdown : `verdict`
(`identical` | `identical_scoped` | `differences` | `error`), `exit_code`, `verdict_text`, `scope_limited`, et par modèle
`partitions_differing`, `differing_partitions[].{partition, kind, rows_a, rows_b, differing_columns}`, `columns`.

### 3.4 Triage d'un écart

Avant de conclure « les données sont fausses », trois hypothèses, de la moins chère à la plus chère :

1. **Fraîcheur / coupure** : les deux côtés ont-ils les mêmes données sources et le même instant de run ?
   Vérification la moins chère : relancer avec `--as-of <date>` sur les deux côtés ; si l'écart tombe sur les dernières
   partitions et disparaît, c'était de la fraîcheur, pas du contenu.
2. **Écart connu entre moteurs** : flottants au-delà de 2^53 après mise à l'échelle, timestamps sous la microseconde
   (Trino arrondit, DuckDB tronque), jour d'une partition `timestamptz` qui dépend du fuseau de session sur DuckDB et
   Trino (UTC sur BigQuery), normalisation unicode, espaces en fin de chaîne, `CHAR(n)`, médiane approchée contre exacte,
   déduplication non déterministe. Vérification la moins chère : relancer `data-parity conformance` (3.1) et lire
   `docs/canonical-spec.md` (« Known cross-engine non-equalities ») ; si la conformance est identique, le hash n'est pas
   en cause et l'écart est dans les données ou dans le fuseau de session.
3. **Vrai bug de transformation** : comparer le SQL compilé des deux côtés (`dbt compile` sur chaque cible) et isoler
   la première couche où ça diverge (staging, intermédiaire, mart) en relançant `data-parity run <modèle>` sur ces
   modèles. La colonne indiquée au passage 3 et la partition donnent le point de départ.

### 3.5 Cache des partitions fermées (optionnel, hors outil)

Le passage 1 relit tout à chaque fois. Une partition **fermée** (antérieure à `reference_date - lag_days`, 3 jours
par défaut) ne change plus ; son empreinte peut être calculée une fois et conservée. Détails : `docs/cache.md`.
L'outil `data-parity` n'utilise pas le cache ; ces macros se lancent à la main avec `dbt run-operation`.

```bash
# 1. À blanc : liste ce qui serait fait, n'écrit RIEN (par défaut)
dbt run-operation data_parity_cache_refresh --args '{schema: dp_cache}'
# 2. Écriture : seulement si on le veut explicitement (crée la table data_parity_cache dans ce schéma)
dbt run-operation data_parity_cache_refresh --args '{schema: dp_cache, write: true}'
# 3. Passage 1 avec cache : ne scanne que les partitions ouvertes ; mêmes lignes PARITY| qu'un passage 1 direct
dbt run-operation data_parity_cached_pass1 --args '{schema: dp_cache}'
# 4. Contrôle par échantillon : recalcule N partitions en cache et affiche VERIFY|OK ou VERIFY|STALE
dbt run-operation data_parity_cache_verify --args '{schema: dp_cache, sample: 10}'
```

Pour extraire les lignes `PARITY|` et éviter le faux « identique », voir le piège en 3.2 (même vérification du code de
sortie et du fichier non vide).

- Arguments : `only`, `lag_days` (3), `reference_date` (date de référence pour calculer la coupure, défaut = aujourd'hui),
  `schema` (ou var `data_parity_cache_schema` ; sans schéma, `write: true` et `cached_pass1` refusent de tourner),
  `write` (false). **`reference_date` n'est pas le `as_of` de `data_parity_reconcile`** : il ne filtre aucune ligne, il
  sert seulement à décider quelles partitions sont fermées.
- Sur BigQuery le dataset du cache n'est jamais créé par la macro : il doit exister. **Les écritures du cache sur
  BigQuery n'ont jamais été exécutées : d'abord à blanc, puis sur un dataset de test.**
- Invalidation : (1) changement de schéma (colonne ajoutée, retirée, renommée, retypée) : tout le modèle est recalculé ;
  (2) `--vars '{data_parity_cache_version: "2"}'` invalide tout (après un backfill connu ou un changement de hash) ;
  (3) réécriture silencieuse d'une partition fermée : **invisible** pour le passage 1 avec cache, seul `cache_verify`
  (échantillon, pas une preuve) la détecte ; remède : supprimer les lignes périmées du cache puis `refresh`, ou monter la
  version ; (4) choisir `lag_days` au moins égal au plus long retard d'arrivée normal des données.
- Le cache est en ajout seul par défaut (la ligne la plus récente gagne) ; `data_parity_cache_prune: true` supprime les
  anciennes lignes si le connecteur gère `DELETE`.
- Les partitions doivent être de type DATE ou TIMESTAMP (castable en date).

## 4. Matrice des moteurs

| Moteur | Statut |
|---|---|
| DuckDB | testé (dbt Fusion 2.0.9 et dbt-core 1.12 avec dbt-duckdb) : conformance, reconcile, cache |
| Trino | testé sur Trino 484 dans Docker (catalogue memory), dbt-core seulement (Fusion ne supporte pas Trino) : conformance identique à DuckDB et BigQuery, reconcile DuckDB contre Trino, cache. **Non testé : un vrai cluster Trino** |
| BigQuery | conformance en ligne (identique à DuckDB et Trino) et un vrai run manuel de `data-parity run` avec une table BigQuery en côté A contre une table DuckDB (lecture seule). Aucun test automatisé du dépôt ne couvre BigQuery. Le code du cache (`bigquery__`) n'a jamais tourné ; écritures du cache NON testées |
| Windows | non testé (l'outil n'utilise aucun shell, mais n'a jamais tourné sur Windows) |
| Python 3.9 | version minimale déclarée, non exécutée |

## 5. Windows : particularités

- `dbt.exe` ou `dbt.cmd` : l'outil résout le binaire avec le `PATH` et `PATHEXT` (`.exe`, `.cmd`, `.bat`). Si dbt est
  installé dans un environnement virtuel, donner le chemin complet : `--dbt-a C:\chemin\venv\Scripts\dbt.exe`. En cas
  de `dbt binary not found: dbt`, activer l'environnement ou passer ce chemin.
- Aucun shell n'est utilisé : le JSON passé à `--args` arrive en un seul argument, sans règle de guillemets à connaître.
- Encodage de la console : le résumé affiché dans le terminal est en ASCII pur (les caractères hors ASCII sont remplacés
  par des séquences `\uXXXX`), donc pas de caractères illisibles en cp1252/cp850. Les fichiers `parity.md` et `parity.json`
  sont en UTF-8 (émojis, barres) : les ouvrir avec un éditeur UTF-8 ; dans Windows PowerShell 5.1, `Get-Content -Encoding UTF8 parity.md`.
- Dans le flux manuel PowerShell, `*>` écrit en UTF-16 sous Windows PowerShell 5.1 ; `Select-String` le relit sans
  problème, mais un autre outil (`sed`, `grep` de Git Bash) ne le lira pas : rester dans PowerShell pour tout le flux.
- Les commandes d'exemple de ce runbook ne contiennent aucune syntaxe propre à un shell, sauf les blocs étiquetés bash
  ou PowerShell.

## 6. Dépannage

Les messages sont ceux de l'outil et des macros. Tout échec de l'outil sort avec le code 2.

| Symptôme (message réel) | Cause / remède |
|---|---|
| `data-parity run: error: give --a and --b (dbt targets), or put A= and B= in ./.data-parity.env` | cibles non données ; passer `--a` et `--b` |
| `data-parity: error: no dbt_project.yml in <dossier> (run from your dbt project or use --project)` | mauvais dossier ; se placer dans le projet dbt ou donner `--project` (et `--project-b`) |
| `dbt failed on side B (target X, pass 1): dbt exited with code 1` suivi de `--- last log lines, side B ---` | dbt a échoué : lire les dernières lignes affichées et `raw_p1_B.log` dans le dossier `run-...` ; lancer la même commande `dbt run-operation data_parity_reconcile --target X` à la main dans le bon dossier |
| `target 'X' not found in profile '...'` (dans les dernières lignes dbt) | cible absente de `profiles.yml` ; vérifier `--a`/`--b` et `--profiles-dir-a/-b` |
| `dbt binary not found: dbt` (code 127) | dbt introuvable pour ce côté : activer l'environnement, ou `--dbt-a/--dbt-b` avec le chemin complet |
| `dbt exited with code 127` / `cannot run <chemin>: ...` | le binaire a été trouvé mais n'a pas pu être lancé : vérifier le chemin et les droits |
| `dbt produced no PARITY output (is the data_parity package installed? run `dbt deps`)` | package absent : `dbt deps` ; ou aucun modèle sélectionné |
| `data_parity: 'only' names no tagged model: X (tagged: ...)` | nom de modèle faux (faute de frappe) ou modèle non taggé `data_parity` |
| `data_parity_reconcile: no model tagged data_parity` / `no model in the outputs: tag your models with data_parity` | aucun modèle taggé `data_parity` ni `meta.data_parity` ; avec Fusion, `tags` et `meta` sous `config:` |
| `model(s) not found in the dbt output: X (missing on side A). Check the spelling...` | modèle absent d'un côté (désactivé, non taggé sur ce côté) |
| `data_parity: unsupported column type '...'` | array, struct, json, bytes... : ajouter la colonne à `meta.data_parity.exclude_columns` |
| `... partition column X not found (or excluded)` / `primary key column X not found (or excluded)` | nom de colonne faux dans le meta, ou colonne exclue par `exclude_columns` / `exclude_prefixes` |
| `partitions selected 0 of N partitions (check the partition values)` | liste `partitions:` qui ne correspond à aucune valeur |
| `data_parity: exclude_columns must be a string or a list of strings...` / `...an empty prefix would exclude everything` | config `meta.data_parity` mal formée |
| `a partition value contains a quote or a backslash; refused` | valeur de partition inattendue : choisir une autre colonne de partition |
| `as_of needs a date, timestamp or integer partition column` | `--as-of` demandé sur une partition de type chaîne |
| `partition column X has type class '...'; supported: date, timestamp, integer, string` | type de partition non géré |
| `malformed PARITY line (...)` | sortie dbt altérée ; vérifier la version de l'outil et du package, relancer, garder le dossier `run-...` |
| `data-parity: .data-parity.env: key X ignored (only A, B and PROJECT_B are read)` | avertissement : seules ces trois clés sont lues (jamais un binaire à exécuter) |
| Les lignes `cols\|` diffèrent / « structure » dans le rapport | colonnes ou types logiques différents entre A et B ; les passages suivants sont sautés (hash non comparables) |
| `DIFFERENT: the hash is NOT identical between A and B` (conformance) | ne pas continuer ; lire `docs/canonical-spec.md` ; vérifier les versions des moteurs |
| `incomplete conformance output: N rows on side A, M on side B` / `conformance run failed: ...` | un côté n'a pas rendu les 79 lignes : voir les dernières lignes dbt affichées |
| DuckDB : fichier introuvable ou verrouillé | les chemins DuckDB sont relatifs au dossier courant : `--project` ; fermer les autres connexions à la base |
| `data_parity cache: no cache schema. Pass schema: <name> or set var data_parity_cache_schema` | voulu : aucun schéma par défaut |
| `VERIFY\|STALE\|...` | une partition fermée a été réécrite : supprimer ses lignes du cache et `refresh`, ou monter `data_parity_cache_version` |
| Une macro du projet racine n'arrive pas à appeler une macro du package (constaté dans le banc de test) | ne pas en dépendre : lancer les macros du package par `run-operation`, ou copier la macro dans le package |
| Coût BigQuery élevé | modèle précis, `--last N` / `--as-of`, partition DATE (pour l'élagage), puis cache |

## 7. Limites connues

Trois problèmes ouverts :

1. **Fuseau des partitions `timestamptz`** : le jour d'une partition `timestamptz` dépend du fuseau de session sur DuckDB
   et Trino (UTC sur BigQuery). Mettre la session en UTC des deux côtés, ou partitionner sur une DATE.
2. **Le cache fait confiance aux partitions fermées** : la réécriture silencieuse n'est détectée que par `cache_verify`
   (un échantillon) ou par un changement de version.
3. **« Last N » sur partitions texte trie le texte** : `--last N` ordonne les partitions de type chaîne comme du texte,
   ce qui n'est chronologique que pour des valeurs de type ISO (`2025-01-31`, `202501`).

Un périmètre partiel (`--last`, `--as-of`) donne « ISO on the checked scope », jamais « ISO ». Autres limites :

- Flottants : `round(x * 10^échelle)` en double IEEE ; au-delà de 2^53 après mise à l'échelle l'entier n'est plus exact et
  Trino diffère de DuckDB / BigQuery (exclu du jeu de conformance).
- Timestamps comparés à la microseconde ; en dessous, Trino arrondit et DuckDB tronque.
- Pas de normalisation unicode, pas de trim, pas de collation : `'x '` != `'x'`, NFC != NFD.
- Une somme peut cacher des erreurs qui s'annulent exactement (environ 2^-24 par somme pour une seule ligne modifiée).
- Le passage 1 est un scan complet des colonnes hachées : à borner (`--last`, `--as-of`).
- Les valeurs de partition sont affichées : choisir une partition grossière si elles sont sensibles.
- Types non supportés à exclure ; deux colonnes ne différant que par la casse ne sont pas supportées.
- Fusion : `meta` / `tags` sous `config:`.

## 8. Tests

Deux niveaux qui coexistent :

- `python -m pytest -q` (dossier `tests/`) : tests unitaires et de la CLI avec un faux dbt, toujours exécutés ; avec
  `DBT_FUSION=dbt` (PowerShell : `$env:DBT_FUSION = "dbt"`) `tests/test_integration.py` tourne aussi contre les vraies
  données de `integration_tests/`. À lancer après toute modification de `src/`.
- `sh integration_tests/test_reconcile.sh`, `bash integration_tests/test_cache.sh`,
  `bash integration_tests/test_config_guards.sh` : la matrice complète (dbt-core, Trino avec `TRINO=1`, cache, garde-fous
  de config). À lancer avant une publication ou après toute modification de `dbt_package/`. `TRINO=1` exige le conteneur
  Docker sur `localhost:18080`. Rien ne touche BigQuery.

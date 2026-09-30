# DataStudio

> [🇬🇧 English](README.md) | [🇷🇺 Русский](README.ru.md) | 🇫🇷 Français | [🇩🇪 Deutsch](README.de.md)

[![CI](https://github.com/DogNellaf/data-studio/actions/workflows/ci.yml/badge.svg)](https://github.com/DogNellaf/data-studio/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%E2%80%933.12-3776AB)
![Django](https://img.shields.io/badge/django-5.2-092E20)
![PostgreSQL](https://img.shields.io/badge/postgresql-16%20%7C%2017-4169E1)
![License](https://img.shields.io/badge/license-MIT-green)

Une application web de sauvegarde de bases PostgreSQL : sauvegardes complètes
avec `pg_dump`, puis incrémentales et différentielles par-dessus, lignes
supprimées comprises. Les sauvegardes sont réalisées par un worker en
arrière-plan et stockées sur disque local ou dans un stockage objet compatible
S3. Chaque utilisateur ne voit que les siennes. L’interface est en anglais par
défaut et existe aussi en russe, en français et en allemand.

![Liste des sauvegardes](docs/screenshots/fr/backups.png)

## Démarrage rapide

```bash
docker compose up --build
```

Ouvrez <http://localhost:8000> et connectez-vous avec **demo / demo12345**. La
stack compose lance l’application, le worker de sauvegarde, une base de
démonstration d’une boutique et SeaweedFS comme stockage S3. Dans le
formulaire « Nouvelle sauvegarde », indiquez l’hôte `demo-db`, le port `5432`,
la base `shop`, l’utilisateur et le mot de passe `shop`, puis choisissez le
disque local ou S3. Les fichiers stockés dans S3 sont visibles dans le
navigateur de fichiers de SeaweedFS <http://localhost:8888/buckets/>.

Pour voir les deltas à l’œuvre, modifiez des données puis lancez une
sauvegarde incrémentale :

```bash
psql postgres://shop:shop@localhost:5433/shop \
  -c "UPDATE products SET price = price * 1.1 WHERE id <= 20" \
  -c "DELETE FROM order_items WHERE order_id = 1"
```

## Étude de cas

### Problème

Une équipe doit sauvegarder plusieurs bases PostgreSQL sans accès SSH au
serveur et sans lancer `pg_dump` à la main. Un dump complet d’une grosse base
coûte cher : entre deux dumps, elle veut des sauvegardes légères ne contenant
que les changements, et un dump ne doit jamais bloquer l’application web.

### Solution

| Type | Contenu | Nécessaire pour restaurer |
|---|---|---|
| **Complète** | Schéma et toutes les données (`pg_dump`) | Cette sauvegarde seule |
| **Incrémentale** | Changements depuis la dernière sauvegarde, quel que soit son type | La sauvegarde complète et toutes les incrémentales suivantes |
| **Différentielle** | Changements depuis la dernière sauvegarde complète | La sauvegarde complète et la dernière différentielle |

Chaque sauvegarde enregistre aussi un **fichier d’état** : l’empreinte de
chaque ligne de chaque table, indexée par clé primaire. Un delta compare la
base actuelle à l’état de sa sauvegarde de base ; il n’a donc besoin d’aucune
colonne `updated_at` et voit tout : les lignes nouvelles ou modifiées
deviennent des upserts, les lignes disparues des `DELETE`. Le résultat est un
fichier SQL en une seule transaction, appliqué avec un simple `psql -f` sur la
base restaurée :

```sql
BEGIN;
ALTER TABLE "public"."products" DISABLE TRIGGER USER;
INSERT INTO "public"."products" ("id", "title", "price", "stock", "updated_at")
VALUES ('1', 'Товар №1', '5847.18', '103', '2026-09-30 20:33:18.241939+00')
ON CONFLICT ("id") DO UPDATE SET "title" = EXCLUDED."title", ...;
DELETE FROM "public"."order_items" WHERE ("order_id", "product_id") IN (('1', '2'), ('1', '15'));
ALTER TABLE "public"."products" ENABLE TRIGGER USER;
SELECT pg_catalog.setval('public.customers_id_seq', 501, true);
COMMIT;
```

### Points techniques

- **Les restaurations sont testées de bout en bout.** Les tests d’intégration
  tournent sur un vrai PostgreSQL : sauvegarde complète, mises à jour,
  insertions, suppressions en cascade, table sans clé primaire, autre schéma,
  séquences, puis restauration dans une base vide et comparaison avec la
  source. Si l’on retire du moteur les suppressions, les séquences ou les
  schémas autres que `public`, les tests échouent.
- **Tous les changements sont captés.** Des empreintes de lignes plutôt que
  des horodatages : lignes supprimées, tables sans `updated_at` et tous les
  schémas sont pris en compte. Les tables sans clé primaire sont comparées
  comme des multiensembles d’empreintes : un delta insère et supprime
  exactement les lignes modifiées, doublons compris.
- **Mémoire constante.** Les fichiers d’état sont des flux triés, qu’un delta
  fusionne avec une lecture triée de la table, comme une jointure par fusion.
  Une seule série de clés est gardée en mémoire, quelle que soit la taille de
  la table ; les suppressions attendent dans un fichier temporaire.
- **Un seul instantané cohérent.** La sauvegarde complète transmet
  l’instantané de sa transaction à `pg_dump --snapshot` : le dump et le
  fichier d’état décrivent le même instant, même si la base est modifiée
  pendant ce temps. Les deltas sont lus dans une seule transaction
  `REPEATABLE READ READ ONLY`.
- **Un ordre correct.** Les upserts vont des parents aux enfants, les
  suppressions des enfants aux parents (tri topologique de `pg_constraint`).
  Les séquences sont remises à leur valeur courante pour que les nouvelles
  lignes après restauration n’entrent pas en collision avec les identifiants
  restaurés.
- **Les valeurs sont restaurées telles quelles.** PostgreSQL se charge de
  l’échappement (`quote_nullable()`), et les triggers utilisateur comme
  « mettre à jour `updated_at` » sont désactivés pendant l’écriture.
- **Un changement de schéma ne bloque jamais une sauvegarde.** Si une table ou
  une colonne est apparue ou a disparu depuis la sauvegarde de base, ou si
  celle-ci n’existe plus, le worker réalise une sauvegarde complète et la liste
  indique pourquoi, au lieu de produire un delta impossible à restaurer.
- **Des tâches en arrière-plan sans infrastructure supplémentaire.** La file
  est une table de la base de l’application ; `backup_worker` prend les tâches
  avec `SELECT … FOR UPDATE SKIP LOCKED`, plusieurs workers peuvent donc
  tourner en parallèle. Les tâches d’un worker tombé sont marquées comme
  interrompues, et un worker qu’on arrête termine d’abord son dump en cours.
- **Stockage interchangeable.** Les fichiers passent par l’API de stockage de
  Django : disque local ou tout service compatible S3 (AWS S3, MinIO…). Une
  chaîne de sauvegardes ne sort jamais d’un même stockage, chacun se restaure
  donc seul.

### Sécurité

- Les mots de passe des bases sources **ne sont jamais stockés en clair**.
  Pendant l’attente dans la file, le mot de passe est chiffré (Fernet, clé
  dérivée de `BACKUP_CREDENTIALS_KEY`) et il est effacé dès la fin de la tâche.
  Il parvient à `pg_dump` par l’environnement, pas par la ligne de commande.
- La connexion est vérifiée avant la mise en file : un mauvais mot de passe
  s’affiche dans le formulaire plutôt que sous forme de tâche échouée.
- Chacun ne peut voir, télécharger et supprimer que ses propres sauvegardes ;
  tout le reste renvoie une 404. Les sauvegardes de base des deltas sont, elles
  aussi, cherchées uniquement parmi celles de l’utilisateur.
- Tous les secrets viennent de l’environnement. En production, l’application
  refuse de démarrer sans `DJANGO_SECRET_KEY`, et `check --deploy` passe sans
  avertissement (HSTS, redirection HTTPS, cookies sécurisés).
- La déconnexion se fait uniquement en POST, l’inscription vérifie la
  robustesse du mot de passe et la redirection après connexion est protégée
  contre les redirections ouvertes.

### Localisation

- Toute l’interface est traduite avec le gettext de Django : chaînes source en
  anglais, catalogues russe, français et allemand dans `locale/`, pluriels
  compris (« 2 bases de données », « 5 баз данных »).
- L’anglais est la langue par défaut pour tous. L’en-tête `Accept-Language` du
  navigateur est volontairement ignoré ; le sélecteur EN / RU / FR / DE
  enregistre le choix dans un cookie, et la langue reste limitée à la requête
  pour ne pas déborder sur la suivante.
- Les erreurs des tâches sont stockées sous forme de codes et traduites à
  l’affichage : le worker ne sait pas dans quelle langue lit l’utilisateur.
- La CI vérifie que les catalogues compilés `.mo` correspondent aux sources `.po`.

### Architecture

```mermaid
flowchart LR
    U[Navigateur] -->|HTTP| V[Vues Django]
    V -->|vérification de connexion,<br/>mise en file| Q[(Base de l’application<br/>file de tâches)]
    W[backup_worker] -->|SKIP LOCKED| Q
    W --> B[backuper<br/>pg_dump + empreintes]
    B -->|REPEATABLE READ| SRC[(PostgreSQL<br/>source)]
    W -->|API de stockage Django| ST[/Disque local ou S3/]
    V -->|téléchargement| ST
```

| Module | Rôle |
|---|---|
| `core/views.py` | Vues légères : HTTP, formulaires, messages |
| `core/services.py` | File et tâches : mise en file, prise en charge, exécution, choix de la base, effacement du mot de passe |
| `core/management/commands/backup_worker.py` | Le processus worker |
| `backuper/utils.py` | Sauvegardes complètes via `pg_dump --snapshot`, fichiers d’état, export des deltas |
| `core/backends.py`, `core/crypto.py` | Backends de stockage et chiffrement des mots de passe en file |
| `custom_auth/` | Connexion, inscription et profil sur les formulaires d’authentification de Django |

### Ce que la refonte a changé

Le projet est né comme un prototype d’études. Le rendre opérationnel a
demandé de :

- refaire les deltas : ils ignoraient les lignes supprimées, les tables sans
  `updated_at` et les schémas autres que `public`, et leurs fichiers ne se
  restauraient pas par-dessus une sauvegarde complète ;
- sortir les sauvegardes de la requête web pour les confier à un worker, et
  les fichiers d’un répertoire unique vers un stockage interchangeable avec
  prise en charge de S3 ;
- cesser de stocker en clair les mots de passe des bases tierces ;
- découpler l’algorithme des libellés traduisibles, avec des migrations de
  données pour les lignes existantes ;
- remplacer l’analyse manuelle de `request.POST` par des formulaires Django et
  une couche de services ;
- refaire l’interface : CSS écrit à la main au lieu de Tailwind via CDN, thème
  sombre, mise en page responsive, statut des tâches, états vides et erreurs
  explicites ;
- traduire l’interface en russe, en français et en allemand ;
- ajouter Docker, une CI (lint, tests unitaires et d’intégration, build de
  l’image, exécution de bout en bout de toute la stack) et des données de
  démonstration.

## Captures d’écran

| Nouvelle sauvegarde | Erreur de connexion |
|---|---|
| ![Formulaire](docs/screenshots/fr/create.png) | ![Erreur](docs/screenshots/fr/create-error.png) |

| Stockages | Suppression d’une sauvegarde de base |
|---|---|
| ![Stockages](docs/screenshots/fr/storages.png) | ![Suppression](docs/screenshots/fr/remove.png) |

| Thème sombre | Mobile |
|---|---|
| ![Thème sombre](docs/screenshots/fr/backups-dark.png) | ![Mobile](docs/screenshots/fr/backups-mobile.png) |

| Connexion | Types de sauvegarde |
|---|---|
| ![Connexion](docs/screenshots/fr/login.png) | ![Types de sauvegarde](docs/screenshots/fr/backup-types.png) |

## Lancement sans Docker

Il faut Python 3.10+, PostgreSQL et un `pg_dump` au moins aussi récent que les
serveurs sauvegardés.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # renseignez DATABASE_URL
python manage.py migrate        # crée les types de sauvegarde et un stockage par défaut
python manage.py createsuperuser
python manage.py runserver      # l’application web
python manage.py backup_worker  # dans un second terminal : réalise les sauvegardes en file
```

Pour un essai rapide sans worker, définissez `BACKUP_RUN_INLINE=True` : les
sauvegardes sont alors réalisées dans la requête.

## Configuration

Les réglages viennent des variables d’environnement ou d’un fichier `.env`.
La liste complète et commentée se trouve dans [`.env.example`](.env.example).

| Variable | Rôle | Par défaut |
|---|---|---|
| `DJANGO_SECRET_KEY` | Clé secrète ; obligatoire si `DEBUG=False` | en debug, une clé locale dans `.dev-secret-key` |
| `DJANGO_DEBUG` | Mode debug | `False` |
| `DJANGO_ALLOWED_HOSTS` | Hôtes autorisés, séparés par des virgules | `localhost` en debug |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Origines de confiance pour le CSRF | — |
| `DJANGO_TIME_ZONE` | Fuseau horaire des dates affichées | `UTC` |
| `DATABASE_URL` | Base de l’application (contient aussi la file de tâches) | `postgres://…/DataStudio` |
| `BACKUP_DIR` | Répertoire du stockage local | `./backups` |
| `BACKUP_S3_BUCKET` | Active le stockage S3 | — |
| `BACKUP_S3_PREFIX`, `BACKUP_S3_ENDPOINT_URL`, `BACKUP_S3_REGION`, `BACKUP_S3_ACCESS_KEY`, `BACKUP_S3_SECRET_KEY`, `BACKUP_S3_ADDRESSING_STYLE` | Emplacement et accès S3 ; l’endpoint et le style `path` servent à MinIO et aux autres services compatibles S3 | préfixe `datastudio`, réglages AWS |
| `BACKUP_CREDENTIALS_KEY` | Clé des mots de passe en file ; identique pour l’application et les workers | dérivée de `DJANGO_SECRET_KEY` |
| `BACKUP_WORKER_POLL_INTERVAL` | Fréquence de consultation de la file par un worker inactif, s | `2` |
| `BACKUP_RUN_INLINE` | Réaliser les sauvegardes dans la requête, sans file | `False` |
| `PG_DUMP_PATH` | Exécutable `pg_dump` | trouvé dans `PATH` |
| `PG_DUMP_TIMEOUT` | Durée maximale d’un `pg_dump`, s | `600` |
| `DB_CONNECT_TIMEOUT` | Délai de connexion à la base source, s | `5` |
| `DEMO_USERNAME` / `DEMO_PASSWORD` | Compte de démonstration affiché sur la page de connexion | — |
| `DJANGO_SECURE_SSL_REDIRECT`, `DJANGO_SECURE_HSTS_SECONDS`, `DJANGO_SECURE_COOKIES`, `DJANGO_USE_X_FORWARDED_PROTO` | Durcissement HTTPS en production | activés |

`python manage.py seed_demo` crée l’utilisateur de démonstration et, si S3 est
configuré, enregistre le stockage S3 et crée le bucket s’il n’existe pas.

## Tests

```bash
pip install -r requirements-dev.txt
ruff check .
python manage.py test --settings=datastudio.settings_test

# avec les tests d’intégration sur un vrai PostgreSQL :
INTEGRATION_DATABASE_URL=postgres://postgres:postgres@localhost:5432/postgres \
  coverage run manage.py test --settings=datastudio.settings_test && coverage report
```

131 tests, couverture de 97 % ; S3 est testé avec moto. La CI démarre
PostgreSQL 16 pour les tests d’intégration puis, après le build de l’image,
lance toute la stack compose et réalise des sauvegardes complètes et
incrémentales via le worker, sur disque local comme dans S3 (SeaweedFS), y compris un
changement de schéma qui transforme un delta en sauvegarde complète
([`docker/smoke_test.py`](docker/smoke_test.py)).

## Structure du projet

```
├── backuper/            # Sauvegardes : pg_dump, fichiers d’état, deltas
├── core/                # Sauvegardes : modèles, tâches, stockage, vues, templates, CSS
├── custom_auth/         # Connexion, inscription, profil
├── datastudio/          # Réglages et URLconf racine
├── locale/              # Traductions russe, française et allemande (gettext)
├── docker/              # Entrypoint, données de démonstration, test de la stack
├── docs/screenshots/
├── Dockerfile
├── docker-compose.yml
└── .github/workflows/ci.yml
```

## Licence

[MIT](LICENSE)

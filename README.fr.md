# DataStudio

> [🇬🇧 English](README.md) | [🇷🇺 Русский](README.ru.md) | 🇫🇷 Français | [🇩🇪 Deutsch](README.de.md)

[![CI](https://github.com/DogNellaf/data-studio/actions/workflows/ci.yml/badge.svg)](https://github.com/DogNellaf/data-studio/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%E2%80%933.12-3776AB)
![Django](https://img.shields.io/badge/django-5.2-092E20)
![PostgreSQL](https://img.shields.io/badge/postgresql-16%20%7C%2017-4169E1)
![License](https://img.shields.io/badge/license-MIT-green)

Une application web de sauvegarde de bases PostgreSQL. Elle réalise des
sauvegardes complètes avec `pg_dump`, puis des sauvegardes incrémentales et
différentielles par-dessus. Les sauvegardes se téléchargent et se suppriment,
et chaque utilisateur ne voit que les siennes. L’interface est en anglais par
défaut et existe aussi en russe, en français et en allemand grâce au
sélecteur de langue de l’en-tête.

![Liste des sauvegardes](docs/screenshots/fr/backups.png)

## Démarrage rapide

```bash
docker compose up --build
```

Ouvrez <http://localhost:8000> et connectez-vous avec **demo / demo12345**. La
stack compose contient une base de démonstration d’une boutique. Dans le
formulaire « Nouvelle sauvegarde », indiquez l’hôte `demo-db`, le port `5432`,
la base `shop`, l’utilisateur et le mot de passe `shop`.

Pour voir les deltas à l’œuvre, modifiez des données puis lancez une
sauvegarde incrémentale :

```bash
psql postgres://shop:shop@localhost:5433/shop \
  -c "UPDATE products SET price = price * 1.1 WHERE id <= 20"
```

## Étude de cas

### Problème

Une équipe doit sauvegarder plusieurs bases PostgreSQL sans accès SSH au
serveur et sans lancer `pg_dump` à la main. Un dump complet d’une grosse base
coûte cher ; entre deux dumps, elle veut donc des sauvegardes légères qui ne
contiennent que les changements.

### Solution

| Type | Contenu | Nécessaire pour restaurer |
|---|---|---|
| **Complète** | Schéma et toutes les données (`pg_dump`) | Cette sauvegarde seule |
| **Incrémentale** | Lignes modifiées depuis la dernière sauvegarde, quel que soit son type | La sauvegarde complète et toutes les incrémentales suivantes |
| **Différentielle** | Lignes modifiées depuis la dernière sauvegarde complète | La sauvegarde complète et la dernière différentielle |

Une sauvegarde delta est un fichier SQL d’upserts exécuté en une seule
transaction. On l’applique avec un simple `psql -f` sur une base restaurée à
partir de la sauvegarde complète :

```sql
BEGIN;
ALTER TABLE "products" DISABLE TRIGGER USER;
INSERT INTO "products" ("id", "title", "price", "stock", "updated_at")
VALUES ('1', 'Товар №1', '8672.42', '92', '2026-09-30 11:37:21.910003+00')
ON CONFLICT ("id") DO UPDATE SET "title" = EXCLUDED."title", ...;
ALTER TABLE "products" ENABLE TRIGGER USER;
COMMIT;
```

### Points techniques

- **Les restaurations sont testées de bout en bout.** Les tests d’intégration
  tournent sur un vrai PostgreSQL : sauvegarde complète, modification des
  données, deltas, restauration dans une base vide, puis comparaison avec la
  source.
- **Upserts par clé primaire.** Une ligne modifiée existe déjà après la
  restauration complète : elle est mise à jour au lieu de provoquer une
  violation d’unicité.
- **Ordre respectant les clés étrangères.** Les tables sont triées
  topologiquement à partir de `pg_constraint` : une nouvelle commande est
  insérée après son nouveau client.
- **PostgreSQL se charge de l’échappement.** `quote_nullable()` s’exécute côté
  serveur : JSON, tableaux, `bytea`, apostrophes et NULL passent sans formatage
  des valeurs en Python.
- **Un seul instantané cohérent.** Les deltas sont lus dans une transaction
  `REPEATABLE READ READ ONLY`.
- **Les valeurs sont restaurées telles quelles.** Les triggers utilisateur,
  comme « mettre à jour `updated_at` », sont désactivés pendant l’insertion et
  ne peuvent pas écraser les valeurs sauvegardées.

### Sécurité

- Les mots de passe des bases sources **ne sont jamais conservés**. Ils
  n’existent que le temps de la sauvegarde et parviennent à `pg_dump` par
  l’environnement, pas par la ligne de commande.
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
- Les noms et descriptions des types de sauvegarde viennent de chaînes
  traduisibles associées à un code stable, pas de lignes en base : ils suivent
  la langue choisie.
- La CI vérifie que le catalogue compilé `.mo` correspond à la source `.po`.

### Architecture

```mermaid
flowchart LR
    U[Navigateur] -->|HTTP| V[Vues Django<br/>core, custom_auth]
    V --> F[Formulaires<br/>validation]
    V --> S[core.services<br/>choix de la base,<br/>transaction]
    S --> B[backuper.utils]
    B -->|pg_dump| SRC[(PostgreSQL<br/>source)]
    B -->|psycopg2, REPEATABLE READ| SRC
    B --> FS[/BACKUP_DIR/*.sql/]
    S --> DB[(Base de l’application<br/>PostgreSQL)]
```

| Module | Rôle |
|---|---|
| `core/views.py` | Vues légères : HTTP, formulaires, messages |
| `core/services.py` | Logique métier : quelle sauvegarde réaliser, par rapport à quelle base, annulation en cas d’échec |
| `backuper/utils.py` | Sauvegardes complètes via `pg_dump`, export des deltas en SQL d’upserts |
| `custom_auth/` | Connexion, inscription et profil sur les formulaires d’authentification de Django |
| `core/migrations/0007_*` | Migration de données : codes de type stables au lieu des libellés, suppression des mots de passe stockés |

### Ce que la refonte a changé

Le projet est né comme un prototype d’études. Le rendre opérationnel a
demandé de :

- corriger les deltas : la recherche des colonnes ignorait le schéma, et les
  fichiers de simples `INSERT` ne se restauraient pas par-dessus une sauvegarde
  complète ;
- cesser de stocker en clair les mots de passe des bases tierces ;
- découpler l’algorithme des libellés traduisibles (renommer un type dans
  l’administration cassait les sauvegardes), avec une migration de données pour
  les lignes existantes ;
- remplacer l’analyse manuelle de `request.POST` par des formulaires Django et
  une couche de services ;
- refaire l’interface : CSS écrit à la main au lieu de Tailwind via CDN, thème
  sombre, mise en page responsive, états vides et erreurs explicites ;
- traduire l’interface : anglais par défaut, plus le russe, le français et
  l’allemand ;
- ajouter Docker, une CI (lint, tests unitaires et d’intégration, build de
  l’image) et des données de démonstration.

## Captures d’écran

| Nouvelle sauvegarde | Erreur de connexion |
|---|---|
| ![Formulaire](docs/screenshots/fr/create.png) | ![Erreur](docs/screenshots/fr/create-error.png) |

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
python manage.py runserver
```

## Configuration

Les réglages viennent des variables d’environnement ou d’un fichier `.env`.
La liste complète et commentée se trouve dans [`.env.example`](.env.example).

| Variable | Rôle | Par défaut |
|---|---|---|
| `DJANGO_SECRET_KEY` | Clé secrète ; obligatoire si `DEBUG=False` | — |
| `DJANGO_DEBUG` | Mode debug | `False` |
| `DJANGO_ALLOWED_HOSTS` | Hôtes autorisés, séparés par des virgules | `localhost` en debug |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Origines de confiance pour le CSRF | — |
| `DJANGO_TIME_ZONE` | Fuseau horaire des dates affichées | `UTC` |
| `DATABASE_URL` | Base de l’application | `postgres://…/DataStudio` |
| `BACKUP_DIR` | Répertoire des fichiers de sauvegarde | `./backups` |
| `PG_DUMP_PATH` | Exécutable `pg_dump` | trouvé dans `PATH` |
| `PG_DUMP_TIMEOUT` | Durée maximale d’un `pg_dump`, s | `600` |
| `DB_CONNECT_TIMEOUT` | Délai de connexion à la base source, s | `5` |
| `DEMO_USERNAME` / `DEMO_PASSWORD` | Compte de démonstration affiché sur la page de connexion | — |
| `DJANGO_SECURE_SSL_REDIRECT`, `DJANGO_SECURE_HSTS_SECONDS`, `DJANGO_SECURE_COOKIES`, `DJANGO_USE_X_FORWARDED_PROTO` | Durcissement HTTPS en production | activés |

## Tests

```bash
pip install -r requirements-dev.txt
ruff check .
python manage.py test --settings=datastudio.settings_test

# avec les tests d’intégration sur un vrai PostgreSQL :
INTEGRATION_DATABASE_URL=postgres://postgres:postgres@localhost:5432/postgres \
  coverage run manage.py test --settings=datastudio.settings_test && coverage report
```

98 tests, couverture de 98 %. La CI démarre PostgreSQL 16 et exécute les vrais
`pg_dump` et `psql`.

## Limites

Les limites connues de l’implémentation actuelle :

- Les deltas ne capturent pas les lignes **supprimées** ; il faudrait la
  réplication logique ou un journal des suppressions.
- Seules les tables du schéma `public` ayant une colonne `updated_at` ou
  `created_at` entrent dans les deltas ; les autres ne figurent que dans les
  sauvegardes complètes.
- Les sauvegardes s’exécutent de façon synchrone dans la requête. Pour des bases
  de plusieurs dizaines de gigaoctets, il faudrait une file de tâches
  (Celery/RQ) et un stockage S3.
- Le seul stockage est le répertoire local `BACKUP_DIR`. Le modèle du catalogue
  de stockages laisse la place à d’autres backends.

## Structure du projet

```
├── backuper/            # Sauvegardes : pg_dump et export des deltas
├── core/                # Sauvegardes : modèles, formulaires, services, vues, templates, CSS
├── custom_auth/         # Connexion, inscription, profil
├── datastudio/          # Réglages et URLconf racine
├── locale/              # Traductions russe, française et allemande (gettext)
├── docker/              # Entrypoint et données de la base de démonstration
├── docs/screenshots/
├── Dockerfile
├── docker-compose.yml
└── .github/workflows/ci.yml
```

## Licence

[MIT](LICENSE)

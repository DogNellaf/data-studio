# DataStudio

> [🇬🇧 English](README.md) | [🇷🇺 Русский](README.ru.md) | [🇫🇷 Français](README.fr.md) | 🇩🇪 Deutsch

[![CI](https://github.com/DogNellaf/data-studio/actions/workflows/ci.yml/badge.svg)](https://github.com/DogNellaf/data-studio/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%E2%80%933.12-3776AB)
![Django](https://img.shields.io/badge/django-5.2-092E20)
![PostgreSQL](https://img.shields.io/badge/postgresql-16%20%7C%2017-4169E1)
![License](https://img.shields.io/badge/license-MIT-green)

Eine Webanwendung zur Sicherung von PostgreSQL-Datenbanken. Sie erstellt
Vollsicherungen mit `pg_dump` und darauf aufbauend inkrementelle und
differenzielle Sicherungen. Sicherungen lassen sich herunterladen und löschen,
und jeder Benutzer sieht nur seine eigenen. Die Oberfläche ist standardmäßig
auf Englisch und über den Sprachumschalter in der Kopfzeile auch auf Russisch,
Französisch und Deutsch verfügbar.

![Liste der Sicherungen](docs/screenshots/de/backups.png)

## Schnellstart

```bash
docker compose up --build
```

Öffnen Sie <http://localhost:8000> und melden Sie sich mit **demo / demo12345**
an. Der Compose-Stack enthält eine Demo-Datenbank eines Onlineshops. Geben Sie
im Formular „Neue Sicherung“ den Host `demo-db`, Port `5432`, die Datenbank
`shop` sowie Benutzer und Passwort `shop` ein.

Um Delta-Sicherungen in Aktion zu sehen, ändern Sie Daten und erstellen dann
eine inkrementelle Sicherung:

```bash
psql postgres://shop:shop@localhost:5433/shop \
  -c "UPDATE products SET price = price * 1.1 WHERE id <= 20"
```

## Fallstudie

### Problem

Ein Team muss mehrere PostgreSQL-Datenbanken sichern, ohne SSH-Zugang zum
Server und ohne `pg_dump` von Hand aufzurufen. Vollständige Dumps einer großen
Datenbank sind teuer, deshalb braucht es dazwischen kleine Sicherungen, die nur
die Änderungen enthalten.

### Lösung

| Typ | Inhalt | Zur Wiederherstellung nötig |
|---|---|---|
| **Vollständig** | Schema und alle Daten (`pg_dump`) | Nur diese Sicherung |
| **Inkrementell** | Zeilen, die seit der letzten Sicherung beliebigen Typs geändert wurden | Die Vollsicherung und alle inkrementellen seitdem |
| **Differenziell** | Zeilen, die seit der letzten Vollsicherung geändert wurden | Die Vollsicherung und die neueste differenzielle |

Eine Delta-Sicherung ist eine SQL-Datei mit Upserts in einer einzigen
Transaktion. Sie wird mit einem einfachen `psql -f` auf eine aus der
Vollsicherung wiederhergestellte Datenbank angewendet:

```sql
BEGIN;
ALTER TABLE "products" DISABLE TRIGGER USER;
INSERT INTO "products" ("id", "title", "price", "stock", "updated_at")
VALUES ('1', 'Товар №1', '8672.42', '92', '2026-09-30 11:37:21.910003+00')
ON CONFLICT ("id") DO UPDATE SET "title" = EXCLUDED."title", ...;
ALTER TABLE "products" ENABLE TRIGGER USER;
COMMIT;
```

### Technische Highlights

- **Wiederherstellungen werden durchgängig getestet.** Integrationstests laufen
  gegen ein echtes PostgreSQL: Vollsicherung, Datenänderungen, Deltas,
  Wiederherstellung in eine leere Datenbank und Vergleich mit der Quelle.
- **Upserts per Primärschlüssel.** Eine geänderte Zeile existiert nach der
  Vollwiederherstellung bereits; sie wird aktualisiert, statt an einer
  Eindeutigkeitsverletzung zu scheitern.
- **Reihenfolge nach Fremdschlüsseln.** Die Tabellen werden anhand von
  `pg_constraint` topologisch sortiert, sodass eine neue Bestellung nach ihrem
  neuen Kunden eingefügt wird.
- **PostgreSQL übernimmt das Escaping.** `quote_nullable()` läuft serverseitig,
  sodass JSON, Arrays, `bytea`, Anführungszeichen und NULL ohne Formatierung in
  Python korrekt übertragen werden.
- **Ein konsistenter Snapshot.** Deltas werden in einer Transaktion
  `REPEATABLE READ READ ONLY` gelesen.
- **Werte werden unverändert wiederhergestellt.** Benutzer-Trigger wie
  „`updated_at` aktualisieren“ sind beim Einfügen deaktiviert und können die
  gesicherten Werte nicht überschreiben.

### Sicherheit

- Passwörter der Quelldatenbanken werden **nie gespeichert**. Sie existieren
  nur während der Sicherung und gelangen über die Umgebung zu `pg_dump`, nicht
  über die Kommandozeile.
- Jeder kann nur seine eigenen Sicherungen sehen, herunterladen und löschen;
  alles andere ergibt 404. Auch die Basissicherung für Deltas wird nur unter den
  eigenen gesucht.
- Alle Geheimnisse kommen aus der Umgebung. In Produktion startet die Anwendung
  ohne `DJANGO_SECRET_KEY` nicht, und `check --deploy` läuft ohne Warnungen
  durch (HSTS, HTTPS-Weiterleitung, sichere Cookies).
- Abmelden geht nur per POST, die Registrierung prüft die Passwortstärke, und
  die Weiterleitung nach der Anmeldung ist gegen offene Weiterleitungen
  geschützt.

### Lokalisierung

- Die gesamte Oberfläche ist mit dem gettext von Django übersetzt: englische
  Quelltexte, russische, französische und deutsche Kataloge in `locale/`,
  einschließlich Pluralformen („2 Datenbanken“, „5 баз данных“).
- Englisch ist für alle der Standard. Der `Accept-Language`-Header des Browsers
  wird bewusst ignoriert; der Umschalter EN / RU / FR / DE speichert die Wahl in
  einem Cookie, und die Sprache gilt nur für die jeweilige Anfrage, damit sie
  nicht in die nächste durchsickert.
- Namen und Beschreibungen der Sicherungstypen stammen aus übersetzbaren Texten
  zu einem stabilen Code, nicht aus Datenbankzeilen, und folgen daher der
  gewählten Sprache.
- Die CI prüft, dass der kompilierte `.mo`-Katalog zur `.po`-Quelle passt.

### Architektur

```mermaid
flowchart LR
    U[Browser] -->|HTTP| V[Django-Views<br/>core, custom_auth]
    V --> F[Formulare<br/>Validierung]
    V --> S[core.services<br/>Wahl der Basissicherung,<br/>Transaktion]
    S --> B[backuper.utils]
    B -->|pg_dump| SRC[(Quell-<br/>PostgreSQL)]
    B -->|psycopg2, REPEATABLE READ| SRC
    B --> FS[/BACKUP_DIR/*.sql/]
    S --> DB[(App-Datenbank<br/>PostgreSQL)]
```

| Modul | Aufgabe |
|---|---|
| `core/views.py` | Schlanke Views: HTTP, Formulare, Meldungen |
| `core/services.py` | Geschäftslogik: welche Sicherung, gegen welche Basis, Rollback bei Fehlern |
| `backuper/utils.py` | Vollsicherungen per `pg_dump`, Delta-Export als Upsert-SQL |
| `custom_auth/` | Anmeldung, Registrierung und Profil auf Basis der Django-Auth-Formulare |
| `core/migrations/0007_*` | Datenmigration: stabile Typcodes statt Anzeigenamen, gespeicherte Passwörter entfernt |

### Was die Überarbeitung geändert hat

Das Projekt begann als Prototyp aus einer Studienarbeit. Um es einsatzfähig zu
machen, war Folgendes nötig:

- Fehler in den Delta-Sicherungen beheben: Die Spaltensuche ignorierte das
  Schema, und Dateien aus einfachen `INSERT`s ließen sich nicht auf eine
  Vollsicherung anwenden;
- Passwörter fremder Datenbanken nicht länger im Klartext speichern;
- den Sicherungsalgorithmus von übersetzbaren Typnamen entkoppeln (das
  Umbenennen eines Typs im Adminbereich brach die Sicherungen), mit einer
  Datenmigration für bestehende Zeilen;
- das manuelle Parsen von `request.POST` durch Django-Formulare und eine
  Service-Schicht ersetzen;
- die Oberfläche neu bauen: handgeschriebenes CSS statt Tailwind per CDN, mit
  dunklem Modus, responsivem Layout, Leerzuständen und klaren Fehlermeldungen;
- die Oberfläche übersetzen: Englisch als Standard, dazu Russisch, Französisch
  und Deutsch;
- Docker, CI (Lint, Unit- und Integrationstests, Image-Build) und Demodaten
  ergänzen.

## Screenshots

| Neue Sicherung | Verbindungsfehler |
|---|---|
| ![Formular](docs/screenshots/de/create.png) | ![Fehler](docs/screenshots/de/create-error.png) |

| Dunkler Modus | Mobil |
|---|---|
| ![Dunkler Modus](docs/screenshots/de/backups-dark.png) | ![Mobil](docs/screenshots/de/backups-mobile.png) |

| Anmeldung | Sicherungstypen |
|---|---|
| ![Anmeldung](docs/screenshots/de/login.png) | ![Sicherungstypen](docs/screenshots/de/backup-types.png) |

## Ohne Docker starten

Benötigt werden Python 3.10+, PostgreSQL und ein `pg_dump`, das mindestens so
neu ist wie die gesicherten Server.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # DATABASE_URL eintragen
python manage.py migrate        # legt Sicherungstypen und einen Standard-Speicherort an
python manage.py createsuperuser
python manage.py runserver
```

## Konfiguration

Einstellungen kommen aus Umgebungsvariablen oder einer `.env`-Datei. Die
vollständige, kommentierte Liste steht in [`.env.example`](.env.example).

| Variable | Zweck | Standard |
|---|---|---|
| `DJANGO_SECRET_KEY` | Geheimer Schlüssel; Pflicht bei `DEBUG=False` | — |
| `DJANGO_DEBUG` | Debug-Modus | `False` |
| `DJANGO_ALLOWED_HOSTS` | Erlaubte Hosts, kommagetrennt | `localhost` im Debug-Modus |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Vertrauenswürdige CSRF-Origins | — |
| `DJANGO_TIME_ZONE` | Zeitzone für angezeigte Daten | `UTC` |
| `DATABASE_URL` | Datenbank der Anwendung | `postgres://…/DataStudio` |
| `BACKUP_DIR` | Verzeichnis für Sicherungsdateien | `./backups` |
| `PG_DUMP_PATH` | `pg_dump`-Programm | aus `PATH` |
| `PG_DUMP_TIMEOUT` | Höchstdauer eines `pg_dump`-Laufs, s | `600` |
| `DB_CONNECT_TIMEOUT` | Verbindungs-Timeout zur Quelldatenbank, s | `5` |
| `DEMO_USERNAME` / `DEMO_PASSWORD` | Demo-Konto, auf der Anmeldeseite angezeigt | — |
| `DJANGO_SECURE_SSL_REDIRECT`, `DJANGO_SECURE_HSTS_SECONDS`, `DJANGO_SECURE_COOKIES`, `DJANGO_USE_X_FORWARDED_PROTO` | HTTPS-Härtung für die Produktion | aktiviert |

## Tests

```bash
pip install -r requirements-dev.txt
ruff check .
python manage.py test --settings=datastudio.settings_test

# einschließlich Integrationstests gegen ein echtes PostgreSQL:
INTEGRATION_DATABASE_URL=postgres://postgres:postgres@localhost:5432/postgres \
  coverage run manage.py test --settings=datastudio.settings_test && coverage report
```

98 Tests, 98 % Abdeckung. Die CI startet PostgreSQL 16 und führt echte
`pg_dump`- und `psql`-Aufrufe aus.

## Einschränkungen

Bekannte Grenzen der aktuellen Implementierung:

- Deltas erfassen keine **gelöschten** Zeilen; dafür wären logische Replikation
  oder ein Löschprotokoll nötig.
- In Deltas landen nur Tabellen des Schemas `public` mit einer Spalte
  `updated_at` oder `created_at`; andere Tabellen stehen nur in
  Vollsicherungen.
- Sicherungen laufen synchron in der Anfrage. Für Datenbanken mit mehreren
  zehn Gigabyte bräuchte es eine Task-Queue (Celery/RQ) und S3-Speicher.
- Der einzige Speicherort ist das lokale `BACKUP_DIR`. Das Modell des
  Speicherkatalogs lässt Platz für weitere Backends.

## Projektstruktur

```
├── backuper/            # Sicherungen: pg_dump und Delta-Export
├── core/                # Sicherungen: Modelle, Formulare, Services, Views, Templates, CSS
├── custom_auth/         # Anmeldung, Registrierung, Profil
├── datastudio/          # Einstellungen und Root-URLconf
├── locale/              # Russische, französische und deutsche Übersetzungen (gettext)
├── docker/              # Entrypoint und Daten der Demo-Datenbank
├── docs/screenshots/
├── Dockerfile
├── docker-compose.yml
└── .github/workflows/ci.yml
```

## Lizenz

[MIT](LICENSE)

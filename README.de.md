# DataStudio

> [🇬🇧 English](README.md) | [🇷🇺 Русский](README.ru.md) | [🇫🇷 Français](README.fr.md) | 🇩🇪 Deutsch

[![CI](https://github.com/DogNellaf/data-studio/actions/workflows/ci.yml/badge.svg)](https://github.com/DogNellaf/data-studio/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%E2%80%933.12-3776AB)
![Django](https://img.shields.io/badge/django-5.2-092E20)
![PostgreSQL](https://img.shields.io/badge/postgresql-16%20%7C%2017-4169E1)
![License](https://img.shields.io/badge/license-PolyForm%20Noncommercial-orange)

Eine Webanwendung zur Sicherung von PostgreSQL-Datenbanken: Vollsicherungen mit
`pg_dump` und darauf aufbauend inkrementelle und differenzielle Sicherungen,
gelöschte Zeilen eingeschlossen. Die Sicherungen erstellt ein Hintergrund-Worker,
die Dateien liegen auf der lokalen Festplatte oder in einem S3-kompatiblen
Objektspeicher. Jeder Benutzer sieht nur seine eigenen Sicherungen. Die
Oberfläche ist standardmäßig auf Englisch und außerdem auf Russisch,
Französisch und Deutsch verfügbar.

![Liste der Sicherungen](docs/screenshots/de/backups.png)

## Schnellstart

```bash
docker compose up --build
```

Öffnen Sie <http://localhost:8000> und melden Sie sich mit **demo / demo12345**
an. Der Compose-Stack startet die Anwendung, den Sicherungs-Worker, eine
Demo-Datenbank eines Onlineshops und SeaweedFS als S3-Speicher. Geben Sie im
Formular „Neue Sicherung“ den Host `demo-db`, Port `5432`, die Datenbank
`shop` sowie Benutzer und Passwort `shop` ein und wählen Sie die lokale
Festplatte oder S3. Die Dateien in S3 sind im Dateibrowser von SeaweedFS unter
<http://localhost:8888/buckets/> zu sehen.

Um Delta-Sicherungen in Aktion zu sehen, ändern Sie Daten und erstellen dann
eine inkrementelle Sicherung:

```bash
psql postgres://shop:shop@localhost:5433/shop \
  -c "UPDATE products SET price = price * 1.1 WHERE id <= 20" \
  -c "DELETE FROM order_items WHERE order_id = 1"
```

## Fallstudie

### Problem

Ein Team muss mehrere PostgreSQL-Datenbanken sichern, ohne SSH-Zugang zum
Server und ohne `pg_dump` von Hand aufzurufen. Vollständige Dumps einer großen
Datenbank sind teuer, deshalb braucht es dazwischen kleine Sicherungen, die nur
die Änderungen enthalten, und ein Dump darf die Webanwendung nie blockieren.

### Lösung

| Typ | Inhalt | Zur Wiederherstellung nötig |
|---|---|---|
| **Vollständig** | Schema und alle Daten (`pg_dump`) | Nur diese Sicherung |
| **Inkrementell** | Änderungen seit der letzten Sicherung beliebigen Typs | Die Vollsicherung und alle inkrementellen seitdem |
| **Differenziell** | Änderungen seit der letzten Vollsicherung | Die Vollsicherung und die neueste differenzielle |

Jede Sicherung speichert zusätzlich eine **Zustandsdatei**: einen Hash jeder
Zeile jeder Tabelle, geordnet nach Primärschlüssel. Ein Delta vergleicht die
aktuelle Datenbank mit dem Zustand seiner Basissicherung. Es braucht daher
keine `updated_at`-Spalten und erfasst alles: neue und geänderte Zeilen werden
zu Upserts, verschwundene zu `DELETE`s. Das Ergebnis ist eine SQL-Datei in
einer einzigen Transaktion, die mit einem einfachen `psql -f` auf die
wiederhergestellte Basis angewendet wird:

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

### Technische Highlights

- **Wiederherstellungen werden durchgängig getestet.** Integrationstests laufen
  gegen ein echtes PostgreSQL: Vollsicherung, Updates, Inserts, kaskadierende
  Löschungen, eine Tabelle ohne Primärschlüssel, ein weiteres Schema,
  Sequenzänderungen, dann Wiederherstellung in eine leere Datenbank und
  Vergleich mit der Quelle. Entfernt man aus der Engine die Löschungen, die
  Sequenzen oder alle Schemas außer `public`, schlagen die Tests fehl.
- **Jede Änderung wird erfasst.** Zeilen-Hashes statt Zeitstempeln: gelöschte
  Zeilen, Tabellen ohne `updated_at` und alle Schemas sind dabei. Tabellen ohne
  Primärschlüssel werden als Multimengen von Zeilen-Hashes verglichen, sodass
  ein Delta genau die geänderten Zeilen einfügt und löscht, Duplikate
  eingeschlossen.
- **Konstanter Speicherbedarf.** Zustandsdateien sind sortierte Datenströme, die
  ein Delta mit einem sortierten Lesen der Tabelle zusammenführt, wie ein Merge
  Join. Im Speicher liegt immer nur ein Stapel von Schlüsseln, unabhängig von der
  Tabellengröße; anstehende Löschungen warten in einer temporären Datei.
- **Ein konsistenter Snapshot.** Die Vollsicherung übergibt den Snapshot ihrer
  Transaktion an `pg_dump --snapshot`, sodass Dump und Zustandsdatei denselben
  Moment beschreiben, auch wenn währenddessen geschrieben wird. Deltas werden in
  einer einzigen Transaktion `REPEATABLE READ READ ONLY` gelesen.
- **Richtige Reihenfolge.** Upserts laufen von Eltern zu Kindern, Löschungen
  von Kindern zu Eltern (topologische Sortierung von `pg_constraint`). Sequenzen
  werden auf ihren aktuellen Wert gesetzt, damit neue Zeilen nach der
  Wiederherstellung nicht mit wiederhergestellten IDs kollidieren.
- **Werte werden unverändert wiederhergestellt.** PostgreSQL übernimmt das
  Escaping (`quote_nullable()`), und Benutzer-Trigger wie „`updated_at`
  aktualisieren“ sind beim Schreiben deaktiviert.
- **Schemaänderungen blockieren keine Sicherung.** Ist seit der Basissicherung
  eine Tabelle oder Spalte hinzugekommen oder verschwunden oder gibt es die
  Basissicherung nicht mehr, erstellt der Worker stattdessen eine Vollsicherung,
  und die Liste zeigt den Grund an, statt ein Delta zu erzeugen, das sich nicht
  wiederherstellen ließe.
- **Hintergrundaufgaben ohne zusätzliche Infrastruktur.** Die Warteschlange ist
  eine Tabelle in der Anwendungsdatenbank; `backup_worker` holt Aufgaben mit
  `SELECT … FOR UPDATE SKIP LOCKED`, sodass mehrere Worker parallel laufen
  können. Aufgaben eines abgestürzten Workers werden als unterbrochen markiert,
  und ein Worker, der gestoppt wird, beendet zuerst seinen laufenden Dump.
- **Austauschbarer Speicher.** Dateien laufen über die Storage-API von Django:
  lokale Festplatte oder jeder S3-kompatible Dienst (AWS S3, MinIO …). Eine
  Sicherungskette verlässt nie einen Speicherort, daher lässt sich jeder für
  sich wiederherstellen.

### Sicherheit

- Passwörter der Quelldatenbanken werden **nie im Klartext gespeichert**.
  Während eine Aufgabe wartet, ist das Passwort verschlüsselt (Fernet, Schlüssel
  abgeleitet aus `BACKUP_CREDENTIALS_KEY`), und es wird gelöscht, sobald die
  Aufgabe endet. Zu `pg_dump` gelangt es über die Umgebung, nicht über die
  Kommandozeile.
- Die Verbindung wird vor dem Einreihen geprüft, sodass ein falsches Passwort
  direkt im Formular erscheint statt später als fehlgeschlagene Aufgabe.
- Jeder kann nur seine eigenen Sicherungen sehen, herunterladen und löschen;
  alles andere ergibt einen 404-Fehler. Auch die Basissicherung für Deltas wird nur unter den
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
- Fehler von Aufgaben werden als Codes gespeichert und erst bei der Anzeige
  übersetzt, denn der Worker weiß nicht, in welcher Sprache der Benutzer liest.
- Die CI prüft, dass die kompilierten `.mo`-Kataloge zu den `.po`-Quellen passen.

### Architektur

```mermaid
flowchart LR
    U[Browser] -->|HTTP| V[Django-Views]
    V -->|Verbindung prüfen,<br/>einreihen| Q[(App-Datenbank<br/>Warteschlange)]
    W[backup_worker] -->|SKIP LOCKED| Q
    W --> B[backuper<br/>pg_dump + Zeilen-Hashes]
    B -->|REPEATABLE READ| SRC[(Quell-<br/>PostgreSQL)]
    W -->|Django-Storage-API| ST[/Lokale Festplatte oder S3/]
    V -->|Download| ST
```

| Modul | Aufgabe |
|---|---|
| `core/views.py` | Schlanke Views: HTTP, Formulare, Meldungen |
| `core/services.py` | Warteschlange und Aufgaben: einreihen, holen, ausführen, Basissicherung wählen, Passwort löschen |
| `core/management/commands/backup_worker.py` | Der Worker-Prozess |
| `backuper/utils.py` | Vollsicherungen per `pg_dump --snapshot`, Zustandsdateien, Delta-Export |
| `core/backends.py`, `core/crypto.py` | Speicher-Backends und Verschlüsselung wartender Passwörter |
| `custom_auth/` | Anmeldung, Registrierung und Profil auf Basis der Django-Auth-Formulare |

### Was die Überarbeitung geändert hat

Das Projekt begann als Prototyp aus einer Studienarbeit. Um es einsatzfähig zu
machen, war Folgendes nötig:

- die Deltas neu bauen: Sie übersahen gelöschte Zeilen, Tabellen ohne
  `updated_at` und alle Schemas außer `public`, und ihre Dateien ließen sich
  nicht auf eine Vollsicherung anwenden;
- Sicherungen aus der Webanfrage in einen Worker verlagern und Dateien aus
  einem einzigen Verzeichnis in austauschbare Speicher mit S3-Unterstützung;
- Passwörter fremder Datenbanken nicht länger im Klartext speichern;
- den Algorithmus von übersetzbaren Typnamen entkoppeln, mit Datenmigrationen
  für bestehende Zeilen;
- das manuelle Parsen von `request.POST` durch Django-Formulare und eine
  Service-Schicht ersetzen;
- die Oberfläche neu bauen: handgeschriebenes CSS statt Tailwind per CDN, mit
  dunklem Modus, responsivem Layout, Aufgabenstatus, Leerzuständen und klaren
  Fehlermeldungen;
- die Oberfläche ins Russische, Französische und Deutsche übersetzen;
- Docker, CI (Lint, Unit- und Integrationstests, Image-Build, ein Durchlauf
  des gesamten Stacks) und Demodaten ergänzen.

## Screenshots

| Neue Sicherung | Verbindungsfehler |
|---|---|
| ![Formular](docs/screenshots/de/create.png) | ![Fehler](docs/screenshots/de/create-error.png) |

| Speicherorte | Löschen einer Basissicherung |
|---|---|
| ![Speicherorte](docs/screenshots/de/storages.png) | ![Löschen](docs/screenshots/de/remove.png) |

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
python manage.py runserver      # die Webanwendung
python manage.py backup_worker  # in einem zweiten Terminal: erstellt eingereihte Sicherungen
```

Für einen schnellen Test ohne Worker setzen Sie `BACKUP_RUN_INLINE=True`; dann
werden Sicherungen direkt in der Anfrage erstellt.

## Konfiguration

Einstellungen kommen aus Umgebungsvariablen oder einer `.env`-Datei. Die
vollständige, kommentierte Liste steht in [`.env.example`](.env.example).

| Variable | Zweck | Standard |
|---|---|---|
| `DJANGO_SECRET_KEY` | Geheimer Schlüssel; Pflicht bei `DEBUG=False` | im Debug-Modus ein lokaler Schlüssel in `.dev-secret-key` |
| `DJANGO_DEBUG` | Debug-Modus | `False` |
| `DJANGO_ALLOWED_HOSTS` | Erlaubte Hosts, kommagetrennt | `localhost` im Debug-Modus |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Vertrauenswürdige CSRF-Origins | — |
| `DJANGO_TIME_ZONE` | Zeitzone für angezeigte Daten | `UTC` |
| `DATABASE_URL` | Datenbank der Anwendung (enthält auch die Warteschlange) | `postgres://…/DataStudio` |
| `BACKUP_DIR` | Verzeichnis des lokalen Speichers | `./backups` |
| `BACKUP_S3_BUCKET` | Aktiviert den S3-Speicher | — |
| `BACKUP_S3_PREFIX`, `BACKUP_S3_ENDPOINT_URL`, `BACKUP_S3_REGION`, `BACKUP_S3_ACCESS_KEY`, `BACKUP_S3_SECRET_KEY`, `BACKUP_S3_ADDRESSING_STYLE` | Ort und Zugang zu S3; Endpoint und `path`-Stil sind für MinIO und andere S3-kompatible Dienste | Präfix `datastudio`, AWS-Standards |
| `BACKUP_CREDENTIALS_KEY` | Schlüssel für wartende Passwörter; muss für Anwendung und Worker gleich sein | abgeleitet aus `DJANGO_SECRET_KEY` |
| `BACKUP_WORKER_POLL_INTERVAL` | Wie oft ein untätiger Worker die Warteschlange prüft, s | `2` |
| `BACKUP_RUN_INLINE` | Sicherungen in der Anfrage erstellen statt einreihen | `False` |
| `PG_DUMP_PATH` | `pg_dump`-Programm | aus `PATH` |
| `PG_DUMP_TIMEOUT` | Höchstdauer eines `pg_dump`-Laufs, s | `600` |
| `DB_CONNECT_TIMEOUT` | Verbindungs-Timeout zur Quelldatenbank, s | `5` |
| `DEMO_USERNAME` / `DEMO_PASSWORD` | Demo-Konto, auf der Anmeldeseite angezeigt | — |
| `DJANGO_SECURE_SSL_REDIRECT`, `DJANGO_SECURE_HSTS_SECONDS`, `DJANGO_SECURE_COOKIES`, `DJANGO_USE_X_FORWARDED_PROTO` | HTTPS-Härtung für die Produktion | aktiviert |

`python manage.py seed_demo` legt den Demo-Benutzer an und registriert bei
konfiguriertem S3 den S3-Speicher; fehlt der Bucket, wird er erstellt.

## Tests

```bash
pip install -r requirements-dev.txt
ruff check .
python manage.py test --settings=datastudio.settings_test

# einschließlich Integrationstests gegen ein echtes PostgreSQL:
INTEGRATION_DATABASE_URL=postgres://postgres:postgres@localhost:5432/postgres \
  coverage run manage.py test --settings=datastudio.settings_test && coverage report
```

133 Tests, 97 % Abdeckung; S3 wird gegen moto getestet. Die CI startet
PostgreSQL 16 für die Integrationstests und fährt nach dem Image-Build den
gesamten Compose-Stack hoch, um über den Worker Voll- und inkrementelle
Sicherungen sowohl auf die lokale Festplatte als auch nach S3 (SeaweedFS) zu
erstellen, einschließlich einer Schemaänderung, nach der statt eines Deltas
eine Vollsicherung entsteht ([`docker/smoke_test.py`](docker/smoke_test.py)).

## Projektstruktur

```
├── backuper/            # Sicherungen: pg_dump, Zustandsdateien, Deltas
├── core/                # Sicherungen: Modelle, Aufgaben, Speicher, Views, Templates, CSS
├── custom_auth/         # Anmeldung, Registrierung, Profil
├── datastudio/          # Einstellungen und Root-URLconf
├── locale/              # Russische, französische und deutsche Übersetzungen (gettext)
├── docker/              # Entrypoint, Demodaten, Stack-Smoke-Test
├── docs/screenshots/
├── Dockerfile
├── docker-compose.yml
└── .github/workflows/ci.yml
```

## Lizenz

[PolyForm Noncommercial 1.0.0](LICENSE). Nutzung, Änderung und
Weitergabe sind für alle nicht kommerziellen Zwecke erlaubt, sofern der Hinweis
`Copyright (c) 2026 DogNellaf` erhalten bleibt. Für die kommerzielle Nutzung ist
eine gesonderte Lizenz erforderlich; Kontakt:
[DogNellaf](https://github.com/DogNellaf).

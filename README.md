# aptly-gui

A lightweight web UI for [aptly](https://www.aptly.info/), focused on **upstream mirror
lifecycle**: see what is published, run sync → snapshot → publish as one controlled
operation, and roll back when an update goes wrong.

It talks to aptly over its REST API only and never touches aptly's database.

> **Status: v0.2.** Existing mirrors can be adopted into sets, synced and snapshotted, and
> published or rolled back from the UI. There is **no authentication yet**, so keep it bound
> to loopback.

## Why

aptly handles Debian and Ubuntu mirrors well, but only from the command line. In practice
that means scripts and cron jobs that one person understands, and questions like "which
snapshot is `noble-security` serving, and how old is it?" get answered over SSH. aptly's
REST API has no authentication, so it cannot simply be handed to colleagues.

## Documents

| File | Contents |
|---|---|
| [`KONCEPT.md`](KONCEPT.md) | Design document (Czech) — concepts, architecture, workflow, roadmap |
| [`POC-NALEZY.md`](POC-NALEZY.md) | Proof-of-concept findings (Czech) — what aptly's API actually does |
| [`demo/`](demo/) | Throwaway aptly instance for development |

## Try it

`demo/` brings up a throwaway aptly (built from `debian:trixie-slim`, since no official
image exists) with its own signing key, plus the UI:

```sh
cd demo
docker compose up -d --build
```

- UI on <http://127.0.0.1:8078>
- aptly API on <http://127.0.0.1:8079>

See [`demo/README.md`](demo/README.md) for creating a small filtered mirror to look at,
and for what to do if the UI cannot reach aptly.

## How it works

aptly stores mirrors, snapshots and publications individually; it has no concept of "these
sixteen mirrors are one Ubuntu release". That grouping is what makes a controlled update
possible, so aptly-gui keeps it in its own database as a **mirror set** and replays the full
definition on every run — including the keyrings, which aptly forgets between updates.

Adoption records that grouping for mirrors that already exist. It creates, changes and
deletes nothing in aptly, which also makes it the safest possible first write operation.

An update syncs and snapshots, then stops. Nothing reaches clients until you publish that
snapshot set explicitly — and publishing an older set is exactly what a rollback is.

Only one write job runs at a time, because aptly has a single database.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `APTLY_API_URL` | `http://127.0.0.1:8079` | Where aptly's REST API lives |
| `APTLY_API_SOCKET` | — | Unix socket path, preferred over TCP |
| `APTLY_GUI_DATABASE_URL` | `sqlite+aiosqlite:///./aptly-gui.db` | Where the GUI keeps its own state |
| `APTLY_GUI_REFRESH_SECONDS` | `15` | How long a read of aptly is reused |
| `APTLY_GUI_LANGUAGE` | `en` | Language before one is chosen in Settings |

## Translations

English and Czech ship in the box; the language is chosen under Settings. Message ids are
English, so an untranslated string degrades to English rather than breaking.

```sh
pybabel extract -F babel.cfg -o src/aptly_gui/i18n/locales/messages.pot .
pybabel update -i src/aptly_gui/i18n/locales/messages.pot -d src/aptly_gui/i18n/locales -l cs
pybabel compile -d src/aptly_gui/i18n/locales
```

Compiled catalogues are committed, and a test fails if one drifts from its source.

## Tests

```sh
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest                      # integration tests skip if no aptly is running
```

Integration tests look for `APTLY_API_URL`, defaulting to the demo instance above. They
create their own mirrors under a random `test-*` name and clean up after themselves, but
they do download one real package from `deb.debian.org`.

Since aptly publishes no OpenAPI spec, these tests are the only thing standing between a
new aptly release and a silently broken client. Run them against every aptly version you
intend to support.

## License

Not chosen yet — MIT or Apache-2.0.

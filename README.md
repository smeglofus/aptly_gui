# aptly-gui

A lightweight web UI for [aptly](https://www.aptly.info/), focused on **upstream mirror
lifecycle**: see what is published, run sync → snapshot → publish as one controlled
operation, and roll back when an update goes wrong.

It talks to aptly over its REST API only and never touches aptly's database.

> **Status: v0.1 in progress.** A read-only web UI shows what is published, which snapshots
> back it, and how old the mirrors are. There is **no authentication yet**, so bind it to
> loopback. Controlled updates and rollback are v0.2.

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

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `APTLY_API_URL` | `http://127.0.0.1:8079` | Where aptly's REST API lives |
| `APTLY_API_SOCKET` | — | Unix socket path, preferred over TCP |
| `APTLY_GUI_REFRESH_SECONDS` | `15` | How long a read of aptly is reused |

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

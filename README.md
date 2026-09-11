# aptly-gui

A lightweight web UI for [aptly](https://www.aptly.info/), focused on **upstream mirror
lifecycle**: see what is published, run sync → snapshot → publish as one controlled
operation, and roll back when an update goes wrong.

It talks to aptly over its REST API only and never touches aptly's database.

> **Status: early.** The aptly API client is written and covered by integration tests that
> drive a real mirror through sync, snapshot, publish, switch and rollback against aptly
> 1.6.1. There is no web UI yet — that is v0.1.

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

## Development environment

No official aptly Docker image exists, so `demo/` builds one from `debian:trixie-slim`,
generates a throwaway signing key and exposes the API on loopback:

```sh
cd demo
docker compose up -d --build
curl -s http://127.0.0.1:8079/api/version
```

See [`demo/README.md`](demo/README.md) for creating a small filtered mirror to test against.

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

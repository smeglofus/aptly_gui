# aptly-gui

A lightweight web UI for [aptly](https://www.aptly.info/), focused on **upstream mirror
lifecycle**: see what is published, run sync → snapshot → publish as one controlled
operation, and roll back when an update goes wrong.

It talks to aptly over its REST API only and never touches aptly's database.

> **Status: design phase.** The proof of concept is done — the full mirror lifecycle has
> been validated against a live aptly 1.6.1 — but no application code exists yet.

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

## License

Not chosen yet — MIT or Apache-2.0.

# demo

Testovací aptly pro vývoj aptly-gui. Staví vlastní image z `debian:trixie-slim`
(oficiální aptly image neexistuje), generuje jednorázový podpisový klíč bez hesla
a pouští `aptly api serve -no-lock`.

```sh
docker compose up -d --build
curl -s http://127.0.0.1:8079/api/version
```

Port je `127.0.0.1:8079` (8080 bývá obsazený), přebít jde přes `APTLY_API_PORT`.
API je bez autentizace, proto pouze na loopbacku.

Založení malého filtrovaného mirroru:

```sh
curl -X POST -H 'Content-Type: application/json' \
  http://127.0.0.1:8079/api/mirrors -d '{
    "Name": "demo-trixie-main",
    "ArchiveURL": "http://deb.debian.org/debian",
    "Distribution": "trixie",
    "Components": ["main"],
    "Architectures": ["amd64"],
    "Filter": "nginx",
    "Keyrings": ["/usr/share/keyrings/debian-archive-keyring.gpg"]
  }'
```

Keyring musí být kombinovaný `debian-archive-keyring.gpg`; per-release keyringy
Release soubor trixie neověří. `Keyrings` je nutné posílat znovu i při každém
`PUT` (update), jinak update spadne — viz `../POC-NALEZY.md`.

Úklid včetně stažených dat:

```sh
docker compose down -v
```

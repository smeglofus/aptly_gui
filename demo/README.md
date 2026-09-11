# demo

Testovací aptly plus GUI. Aptly se staví z `debian:trixie-slim` (oficiální image
neexistuje), generuje jednorázový podpisový klíč bez hesla a pouští
`aptly api serve -no-lock`.

```sh
docker compose up -d --build
```

- GUI: <http://127.0.0.1:8078>
- aptly API: <http://127.0.0.1:8079>

Oba porty jsou jen na loopbacku, protože aptly API nemá autentizaci a GUI ji zatím
taky ne. Přebít jdou přes `APTLY_GUI_PORT` a `APTLY_API_PORT`.

## Když GUI nevidí aptly

Na hostech, kde běží vedle Dockeru i k3s, umí kube-router filtrovat provoz mezi
kontejnery na docker bridge a GUI pak hlásí `ConnectTimeout`, i když DNS jméno
`aptly` resolvuje. Pozná se to tak, že `iptables -L FORWARD` má politiku DROP a
`KUBE-ROUTER-FORWARD` je v řetězci první.

Pro vývoj je nejjednodušší pustit GUI mimo kontejner proti publikovanému portu:

```sh
cd .. && .venv/bin/uvicorn aptly_gui.web.app:app --port 8078
```

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

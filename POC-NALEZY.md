# v0.0 PoC — ověřené nálezy

Ověřeno proti **aptly 1.6.1+ds1-3** (Debian trixie, arm64) přes `aptly api serve -no-lock`.
Demo prostředí je v `demo/`.

## Odpovědi na otevřené otázky z konceptu (kap. 11)

### Async tasky v API
Funguje. `_async=1` vrací `{"Name":…,"ID":n,"State":0}`, stav se čte z `GET /api/tasks/{id}`,
výstup z `GET /api/tasks/{id}/output`.

Stavy tasku: `0` = zařazen, `1` = běží, `2` = úspěch, `3` = chyba.

### Průběh stahování

**Oprava dřívějšího závěru.** `GET /api/tasks/{id}/detail` není nepoužitelný — prázdný objekt
vrací jen u tasků, které nic nestahují (třeba `db cleanup`). U aktualizace mirroru vrací:

```json
{"TotalDownloadSize":1437811898,"RemainingDownloadSize":1069152444,
 "TotalNumberOfPackages":303,"RemainingNumberOfPackages":150}
```

Z toho jde spočítat procenta, stažené bajty, hotové balíčky, rychlost i odhad zbývajícího
času. První vzorky po spuštění jsou ještě prázdné, protože aptly nejdřív plánuje stahování;
UI to musí snést.

### Obsazenost disku
`GET /api/storage` vrací `{"Total":59360,"Free":23383,"PercentFull":60.6}` (v MB).
Read-only mount rootDir do kontejneru GUI **není potřeba**.

### db cleanup přes API
`POST /api/db/cleanup` existuje a podporuje `_async=1`. Retence tedy jde udělat
čistě přes REST a ne-cíl „jediné rozhraní je REST API" může zůstat v přísné podobě.

### Přežití tasků přes restart
**Nepřežijí.** Po `docker compose restart` vrací `GET /api/tasks` prázdné pole
a `GET /api/tasks/{id}` odpovídá `404 Could not find task with id 3`.

Důsledek: recovery po `interrupted` se nesmí opírat o uložená task ID. Stav se musí
odvodit z reality — existuje očekávaný snapshot, ukazuje publikace na očekávané snapshoty.

### Prometheus metriky
`GET /api/metrics` je v aptly nativně (`"enableMetricsEndpoint": true` v configu),
včetně `aptly_api_http_request_duration_seconds` po endpointech.

### Healthchecky
`GET /api/ready` a `GET /api/healthy` existují. Použito v `demo/docker-compose.yml`.

### Swagger spec
Na `/api/swagger.json`, `/swagger.json`, `/api/docs`, `/docs` ani `/api/swagger/doc.json`
není (404), přestože na něj oficiální dokumentace odkazuje. Klient se musí psát proti
chování, ne proti specu.

## Nálezy, které koncept nepředpokládal

### PUT mirroru neuchová Keyrings
`PUT /api/mirrors/{name}` s tělem `{}` selže na
`verification of detached signature failed: exit status 2`, i když mirror byl založen
s platným keyringem. Se stejným `Keyrings` v těle PUT projde.

Keyring se tedy při update neuchovává a **musí se posílat znovu při každé aktualizaci**.
Potvrzuje to, že definice mirror setu musí žít v DB GUI jako zdroj pravdy a při každém
běhu se přehrát.

### Volba keyringu je past
Pro `deb.debian.org/debian trixie` **nestačí** `debian-archive-trixie-stable.gpg` ani
`debian-archive-trixie-automatic.gpg` — Release je podepsaný klíči 12/bookworm i 13/trixie
a projde jedině kombinovaný `debian-archive-keyring.gpg`.

Chyba z API je přitom jen `exit status 2`. GUI ji musí přeložit do srozumitelné věty
a nabídnout keyring z výběru, ne jako textové pole.

### Aptly si sám hlídá závislosti
`DELETE /api/snapshots/{name}` publikovaného snapshotu vrací
`{"error":"unable to drop: snapshot is published"}`. Retence se na to může spolehnout
a nemusí kontrolu duplikovat.

### Verze má na konci newline
`GET /api/version` vrací `{"Version":"1.6.1+ds1-3\n"}`. Při porovnávání verzí trimovat.

### Oficiální Docker image aptly neexistuje
Zkoušeno `aptly/aptly`, `ghcr.io/aptly-dev/aptly`, `aptly/server`, `instantlinux/aptly`
— žádný. Demo si staví vlastní z `debian:trixie-slim`, což zároveň dává pinning verze
pro testovací matici.

## Ověřený řetězec

Celý cyklus z kapitoly 7 proběhl proti živému aptly:

1. `POST /api/mirrors` — filtrovaný mirror (`trixie`, `main`, `amd64`, filtr `nginx`) → 1 balíček
2. `PUT /api/mirrors/{name}?_async=1` — stažení, task skončil ve stavu 2
3. `POST /api/mirrors/{name}/snapshots` — snapshot
4. `POST /api/publish/demo?_async=1` — publikace s podpisem, vznikly `InRelease`, `Release`, `Release.gpg`
5. `PUT /api/publish/demo/trixie?_async=1` — přepnutí na druhý snapshot
6. totéž zpět — rollback, okamžitý

Podepisování stačí zadat jako `{"Signing":{"Batch":true,"GpgKey":"…"}}`, klíč bez hesla
na straně aptly. Passphrase v requestu není potřeba.

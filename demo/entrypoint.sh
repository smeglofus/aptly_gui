#!/bin/sh
set -eu

: "${APTLY_API_LISTEN:=:8080}"
: "${APTLY_SIGN_NAME:=aptly-gui demo}"
: "${APTLY_SIGN_EMAIL:=demo@example.invalid}"

mkdir -p "$GNUPGHOME"
chmod 700 "$GNUPGHOME"

# Throwaway signing key so the demo works without the operator creating one.
# Never reuse this outside the demo: it has no passphrase.
if ! gpg --list-secret-keys --with-colons 2>/dev/null | grep -q '^sec'; then
  echo "[entrypoint] generating throwaway signing key"
  gpg --batch --gen-key <<EOF
%no-protection
Key-Type: RSA
Key-Length: 3072
Name-Real: ${APTLY_SIGN_NAME}
Name-Email: ${APTLY_SIGN_EMAIL}
Expire-Date: 0
%commit
EOF
  mkdir -p /var/lib/aptly/public
  gpg --armor --export "${APTLY_SIGN_EMAIL}" > /var/lib/aptly/public/demo-signing-key.asc
fi

gpg --list-secret-keys --keyid-format LONG || true

# -no-lock keeps the database openable by CLI/cron alongside the API.
exec aptly api serve -listen="${APTLY_API_LISTEN}" -no-lock

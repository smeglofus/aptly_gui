FROM debian:trixie-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      aptly gnupg ca-certificates bzip2 xz-utils curl \
      ubuntu-keyring \
 && rm -rf /var/lib/apt/lists/*

COPY aptly.conf /etc/aptly.conf
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENV GNUPGHOME=/var/lib/aptly/.gnupg
VOLUME /var/lib/aptly
EXPOSE 8080

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

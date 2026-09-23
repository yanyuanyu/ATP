# Local fallback when the upstream ISC image cannot be downloaded.
# Debian trixie's BIND9 supports the SVCB records used by this demo.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends bind9 \
    && rm -rf /var/lib/apt/lists/*
EXPOSE 53/tcp 53/udp
CMD ["/usr/sbin/named", "-g", "-u", "bind", "-c", "/etc/bind/named.conf"]

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl dnsutils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir -e ".[dev]"

CMD ["atp", "--help"]

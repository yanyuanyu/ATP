# ATP Hackathon Demo Controller and Pi Agent runtime image.

FROM node:22.20-bookworm-slim AS pi-deps

WORKDIR /opt/atp-pi
COPY agents/pi/package.json agents/pi/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

FROM atp-hackthon:latest

ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir docker

# Keep Pi isolated from the web frontend dependency tree.  The final image only
# needs the Node runtime, pinned production modules and our ATP-specific harness.
COPY --from=pi-deps /usr/local/bin/node /usr/local/bin/node
COPY --from=pi-deps /opt/atp-pi/node_modules /opt/atp-pi/node_modules
COPY agents/pi/runtime.mjs /opt/atp-pi/runtime.mjs

WORKDIR /app
COPY demo/ /app/demo/

EXPOSE 8080

CMD ["uvicorn", "demo.controller:app", "--host", "0.0.0.0", "--port", "8080"]

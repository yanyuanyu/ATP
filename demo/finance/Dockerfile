FROM atp-hackthon:latest AS core
WORKDIR /app
COPY src/ /app/src/
ENV PYTHONPATH=/app/src

FROM atp-hackthon-demo:latest AS controller
WORKDIR /app
COPY src/ /app/src/
COPY demo/docker/demo/ /app/demo/
ENV PYTHONPATH=/app/src

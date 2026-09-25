FROM golang:1.24-bookworm AS build

WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY main.go ./
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/atp-tlcp-gateway .

FROM scratch
COPY --from=build /out/atp-tlcp-gateway /usr/local/bin/atp-tlcp-gateway
USER 65534:65534
ENTRYPOINT ["/usr/local/bin/atp-tlcp-gateway"]

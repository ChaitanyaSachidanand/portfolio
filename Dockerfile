# --- stage 1: generate the static site + build the server binary ---
FROM golang:1.23-alpine AS builder
WORKDIR /src

COPY go.mod ./
COPY . .

# Render content/content.go + web/templates into ./dist
RUN go run ./cmd/generate

# Give cmd/server its own copy of dist so `go:embed all:dist` picks it up.
RUN rm -rf cmd/server/dist && cp -r dist cmd/server/dist

RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/server ./cmd/server

# --- stage 2: minimal runtime image ---
FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=builder /out/server /server
EXPOSE 8080
ENTRYPOINT ["/server"]

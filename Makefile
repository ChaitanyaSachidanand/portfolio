.PHONY: generate build run docker-build docker-run fly-launch fly-deploy clean

generate:
	go run ./cmd/generate

build: generate
	rm -rf cmd/server/dist
	cp -r dist cmd/server/dist
	go build -o bin/server ./cmd/server

run: build
	PORT=8080 ./bin/server

docker-build:
	docker build -t portfolio .

docker-run: docker-build
	docker run --rm -p 8080:8080 -e PORT=8080 portfolio

# One-time setup: creates the Fly app and reserves the <name>.fly.dev hostname.
fly-launch:
	flyctl launch --no-deploy

fly-deploy:
	flyctl deploy

clean:
	rm -rf dist bin cmd/server/dist

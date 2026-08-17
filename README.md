# portfolio

A personal portfolio site, written in Go: a small program (`cmd/generate`)
renders `html/template` files against plain Go structs
([content/content.go](content/content.go)) into a static site, and a second,
tiny program (`cmd/server`) embeds that static site into a single binary and
serves it over HTTP. Ships as a ~15MB distroless Docker image, deployed to
Fly.io at a free `*.fly.dev` hostname.

## Project layout

```
content/content.go      ← ALL editable text lives here (name, projects, links, ...)
web/templates/*.tmpl    ← HTML structure (rarely needs touching)
web/static/style.css    ← styling
cmd/generate/           ← renders templates+content → ./dist (static files)
cmd/server/              ← embeds a copy of ./dist and serves it over HTTP
Dockerfile               ← multi-stage build: generate site → build server → distroless runtime
fly.toml                 ← Fly.io app config (hostname, region, resources)
.github/workflows/deploy.yml ← auto-deploy to Fly on push to main
```

## Editing content

Open [content/content.go](content/content.go) and search for `EDIT ME` —
every placeholder (project repo URLs, GitHub/LinkedIn handles, extra bio
paragraphs) is marked. That file is the only thing you should need to touch
for day-to-day updates; templates and CSS don't need to change.

## Run locally

Requires Go 1.23+.

```bash
make run
```

This generates the site, builds the server binary, and starts it on
`http://localhost:8080`. Re-run `make run` after editing `content.go`.

To just regenerate the static files without building/running the server:

```bash
make generate   # writes ./dist/index.html and ./dist/static/style.css
```

## Run with Docker

```bash
make docker-run
```

Builds the same image that ships to production and runs it on
`http://localhost:8080`.

## Deploy to Fly.io (free `*.fly.dev` hostname)

This gets you `https://chaitanyasachidanand.fly.dev` at no cost beyond what's
covered by Fly's free monthly allowance (3 shared-cpu-1x-256mb VMs / ~160GB
outbound transfer). **Important:** Fly requires a credit card on file to
activate an account, even though a small static site like this should stay
within the free allowance — there is no card-free tier. If you'd rather avoid
that entirely, see the "Card-free alternative" note at the bottom.

1. Install `flyctl` and sign up / log in:

   ```bash
   curl -L https://fly.io/install.sh | sh
   flyctl auth signup   # or `flyctl auth login` if you already have an account
   ```

2. From the repo root, launch the app (this reads `fly.toml`, and will
   interactively offer a different name if `chaitanyasachidanand` is taken):

   ```bash
   make fly-launch
   ```

3. Deploy:

   ```bash
   make fly-deploy
   ```

   Your site is now live at `https://<app-name>.fly.dev`.

### Auto-deploy on push (optional)

[.github/workflows/deploy.yml](.github/workflows/deploy.yml) redeploys on
every push to `main`. To enable it:

```bash
flyctl tokens create deploy   # copy the printed token
```

Add it as a repo secret named `FLY_API_TOKEN` (GitHub repo → Settings →
Secrets and variables → Actions → New repository secret).

### Card-free alternative

If you'd rather not put a card on file anywhere, Render's free web-service
tier needs no card and can run this same Dockerfile — but its free hostname
is `<name>.onrender.com`, which doesn't contain `.dev`. That trade-off (card
vs. `.dev` hostname) is the one to make; there's currently no free host that
gives a Go binary a genuine `.dev` subdomain without a card on file.

## Why a `.dev`-looking hostname without buying a domain

`fly.dev` is Fly.io's own domain — apps get `<app-name>.fly.dev` for free by
default, which is a real, publicly resolvable hostname containing `.dev`.
Registering your own `.dev` domain (e.g. `chaitanyasachidanand.dev`) costs
roughly $12–15/year at any registrar and requires HTTPS (enforced via HSTS
preload for the whole TLD) — `force_https = true` in `fly.toml` already
covers that if you later map a purchased domain to this app with
`flyctl certs add`.

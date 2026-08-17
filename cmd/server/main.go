// Command server serves the statically-generated site embedded in ./dist
// over HTTP. It is the binary that actually runs in production (Fly.io).
//
// ./dist here is NOT the repo-root dist/ that `make generate` writes to —
// it's a copy placed alongside this file by the build (see ../../Makefile
// and ../../Dockerfile) so it can be embedded via go:embed. Do not edit
// files under cmd/server/dist by hand; they're overwritten on every build.
package main

import (
	"embed"
	"io/fs"
	"log"
	"net/http"
	"os"
	"time"
)

//go:embed all:dist
var distFS embed.FS

func main() {
	root, err := fs.Sub(distFS, "dist")
	if err != nil {
		log.Fatal(err)
	}

	mux := http.NewServeMux()
	mux.Handle("/", withHeaders(http.FileServer(http.FS(root))))

	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}

	srv := &http.Server{
		Addr:              ":" + port,
		Handler:           logRequests(mux),
		ReadHeaderTimeout: 5 * time.Second,
	}

	log.Printf("serving on :%s", port)
	log.Fatal(srv.ListenAndServe())
}

func withHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Referrer-Policy", "strict-origin-when-cross-origin")
		next.ServeHTTP(w, r)
	})
}

func logRequests(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		next.ServeHTTP(w, r)
		log.Printf("%s %s %s", r.Method, r.URL.Path, time.Since(start))
	})
}

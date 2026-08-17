// Command generate renders the portfolio's html/template files against
// content.Data and writes the result to ./dist as static files:
//
//	dist/index.html
//	dist/static/style.css
//
// Run it with `go run ./cmd/generate` (or `make generate`). Run it again
// any time content/content.go changes.
package main

import (
	"html/template"
	"io/fs"
	"log"
	"os"
	"path/filepath"

	"github.com/chaitanyasachidanand/portfolio/content"
	"github.com/chaitanyasachidanand/portfolio/web"
)

const outDir = "dist"

func main() {
	if err := run(); err != nil {
		log.Fatal(err)
	}
	log.Printf("wrote site to ./%s", outDir)
}

func run() error {
	if err := os.RemoveAll(outDir); err != nil {
		return err
	}
	if err := os.MkdirAll(outDir, 0o755); err != nil {
		return err
	}

	if err := renderIndex(); err != nil {
		return err
	}
	if err := copyStatic(); err != nil {
		return err
	}
	return nil
}

func renderIndex() error {
	tmpl, err := template.ParseFS(web.TemplatesFS, "templates/index.html.tmpl")
	if err != nil {
		return err
	}

	f, err := os.Create(filepath.Join(outDir, "index.html"))
	if err != nil {
		return err
	}
	defer f.Close()

	return tmpl.Execute(f, content.Data)
}

// copyStatic copies every file under web/static (embedded as "static/...")
// into dist/static, preserving the directory structure.
func copyStatic() error {
	return fs.WalkDir(web.StaticFS, "static", func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		dest := filepath.Join(outDir, path)
		if d.IsDir() {
			return os.MkdirAll(dest, 0o755)
		}
		data, err := fs.ReadFile(web.StaticFS, path)
		if err != nil {
			return err
		}
		return os.WriteFile(dest, data, 0o644)
	})
}

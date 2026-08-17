// Package web embeds the site's templates and static assets so both the
// generator (cmd/generate) and, indirectly, the server can reach them
// without relying on a filesystem path at runtime.
package web

import "embed"

//go:embed templates
var TemplatesFS embed.FS

//go:embed static
var StaticFS embed.FS

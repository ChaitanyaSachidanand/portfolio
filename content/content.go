// Package content holds all the text that appears on the site.
//
// This is the ONLY file you should need to touch to make the portfolio
// yours. Every field marked "EDIT ME" below is a placeholder — search for
// that string and replace it. Nothing else in the repo needs to change for
// day-to-day content updates.
package content

// Link is a labeled outbound link (email, GitHub, LinkedIn, project repo, ...).
type Link struct {
	Label string // shown text, e.g. "GitHub"
	URL   string
	Icon  string // one of: "mail", "github", "linkedin", "x", "web", "arrow"
}

// SkillGroup is a named cluster of skills/technologies shown as tags.
type SkillGroup struct {
	Category string
	Items    []string
}

// Project is a single portfolio project card.
type Project struct {
	Name        string
	Tagline     string // one-line summary
	Description string // 1-3 sentences
	Stack       []string
	Status      string // e.g. "Active", "M0 shipped", "Archived"
	Links       []Link
}

// Site is the full set of content rendered into templates/index.html.tmpl.
type Site struct {
	Name    string
	Role    string
	Tagline string

	About []string // one or more paragraphs

	Skills   []SkillGroup
	Projects []Project
	Contact  []Link

	Year int
}

// Data is the single source of truth for site content.
var Data = Site{
	Name:    "Chaitanya Sachidanand",
	Role:    "Software Engineer — Go, Distributed Systems, AI Agents",
	Tagline: "I build systems that make autonomous agents and trading strategies behave predictably, in Go.",

	About: []string{
		"I'm a software engineer who works mostly in Go, building backend systems, autonomous agents, and the " +
			"infrastructure that keeps them reliable when nobody's watching.",
		"Lately that means deterministic simulation testing for LLM agent systems, and an autonomous trading agent " +
			"for Indian equities — two very different domains that both come down to the same problem: making " +
			"non-deterministic systems behave predictably enough to trust.",
		// EDIT ME: add a third paragraph about your background, interests, or what you're looking for.
	},

	Skills: []SkillGroup{
		{
			Category: "Languages",
			// EDIT ME: adjust to your real stack.
			Items: []string{"Go", "TypeScript", "Python", "SQL"},
		},
		{
			Category: "Systems & Infra",
			Items: []string{"Docker", "gRPC", "PostgreSQL", "Redis", "CI/CD"},
		},
		{
			Category: "AI / Agents",
			Items: []string{"LLM agent architectures", "Deterministic simulation testing", "LangGraph", "Prompt engineering"},
		},
	},

	Projects: []Project{
		{
			Name:        "schism",
			Tagline:     "Deterministic simulation testing for LLM agent systems",
			Description: "A Go framework that replays and fuzzes multi-agent LLM systems deterministically, " +
				"so flaky agent behavior becomes a reproducible test case instead of a support ticket. M0 shipped; " +
				"targeting LangGraph next.",
			Stack:  []string{"Go"},
			Status: "In development — M0 shipped",
			Links: []Link{
				// EDIT ME: replace with your real repo URL once it's public.
				{Label: "GitHub", URL: "https://github.com/REPLACE_ME/schism", Icon: "github"},
			},
		},
		{
			Name:        "Autonomous Trading Agent",
			Tagline:     "AI-driven trading bot for Indian equities",
			Description: "An autonomous trading agent built on the automaton framework, making and executing " +
				"trading decisions for Indian stock markets with a policy engine and tool-based architecture.",
			Stack:  []string{"Go", "automaton framework"},
			Status: "Active",
			Links: []Link{
				// EDIT ME: replace with your real repo URL, or remove this link if the repo is private.
				{Label: "GitHub", URL: "https://github.com/REPLACE_ME/autonomous-trading-agent", Icon: "github"},
			},
		},
		// EDIT ME: add more Project{} entries here as you ship things.
	},

	Contact: []Link{
		{Label: "Email", URL: "mailto:chaitanyasachidanand@gmail.com", Icon: "mail"},
		// EDIT ME: replace with your real profile URLs.
		{Label: "GitHub", URL: "https://github.com/REPLACE_ME", Icon: "github"},
		{Label: "LinkedIn", URL: "https://linkedin.com/in/REPLACE_ME", Icon: "linkedin"},
	},

	Year: 2026,
}

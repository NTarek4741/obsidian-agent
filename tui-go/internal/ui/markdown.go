package ui

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/glamour"
	"github.com/charmbracelet/glamour/ansi"
)

// Glamour renderers are width-bound; cache one per width plus the rendered
// output per (width, source) — mirrors the cache in MarkdownText.tsx.
var (
	mdRenderers = map[int]*glamour.TermRenderer{}
	mdCache     = map[string]string{}
)

func sp(v string) *string { return &v }
func bp(v bool) *bool     { return &v }
func up(v uint) *uint     { return &v }

// mdStyleConfig mirrors the marked-terminal chalk theme in MarkdownText.tsx:
// headings/strong bold in text color, code on mdCodeBg, links in accent
// underline, blockquotes dim italic, accent list bullets, zero margins.
func mdStyleConfig() ansi.StyleConfig {
	const (
		text   = "#dcddde"
		dim    = "#9e9e9e"
		accent = "#a78bfa"
		codeBg = "#1f1f1f"
		border = "#3a3a3a"
	)
	heading := ansi.StyleBlock{StylePrimitive: ansi.StylePrimitive{Color: sp(text), Bold: bp(true)}}
	return ansi.StyleConfig{
		Document: ansi.StyleBlock{
			StylePrimitive: ansi.StylePrimitive{Color: sp(text)},
			Margin:         up(0),
		},
		BlockQuote: ansi.StyleBlock{
			StylePrimitive: ansi.StylePrimitive{Color: sp(dim), Italic: bp(true)},
			Indent:         up(1),
			IndentToken:    sp("│ "),
		},
		Paragraph: ansi.StyleBlock{StylePrimitive: ansi.StylePrimitive{Color: sp(text)}},
		List: ansi.StyleList{
			LevelIndent: 2,
			StyleBlock:  ansi.StyleBlock{StylePrimitive: ansi.StylePrimitive{Color: sp(text)}},
		},
		Heading:        heading,
		H1:             heading,
		H2:             heading,
		H3:             heading,
		H4:             heading,
		H5:             heading,
		H6:             heading,
		Text:           ansi.StylePrimitive{Color: sp(text)},
		Strikethrough:  ansi.StylePrimitive{Color: sp(dim), CrossedOut: bp(true)},
		Emph:           ansi.StylePrimitive{Color: sp(text), Italic: bp(true)},
		Strong:         ansi.StylePrimitive{Color: sp(text), Bold: bp(true)},
		HorizontalRule: ansi.StylePrimitive{Color: sp(border), Format: "\n--------\n"},
		Item:           ansi.StylePrimitive{BlockPrefix: "• "},
		Enumeration:    ansi.StylePrimitive{BlockPrefix: ". ", Color: sp(text)},
		Link:           ansi.StylePrimitive{Color: sp(accent), Underline: bp(true)},
		LinkText:       ansi.StylePrimitive{Color: sp(accent), Underline: bp(true)},
		Code: ansi.StyleBlock{
			StylePrimitive: ansi.StylePrimitive{Color: sp(text), BackgroundColor: sp(codeBg)},
		},
		CodeBlock: ansi.StyleCodeBlock{
			StyleBlock: ansi.StyleBlock{
				StylePrimitive: ansi.StylePrimitive{Color: sp(text), BackgroundColor: sp(codeBg)},
				Margin:         up(0),
			},
		},
		Table: ansi.StyleTable{
			StyleBlock: ansi.StyleBlock{StylePrimitive: ansi.StylePrimitive{Color: sp(text)}},
		},
	}
}

func renderMarkdown(source string, width int) string {
	key := fmt.Sprintf("%d\x1f%s", width, source)
	if hit, ok := mdCache[key]; ok {
		return hit
	}

	r, ok := mdRenderers[width]
	if !ok {
		var err error
		r, err = glamour.NewTermRenderer(
			glamour.WithStyles(mdStyleConfig()),
			glamour.WithWordWrap(width),
			glamour.WithEmoji(),
		)
		if err != nil {
			return source
		}
		mdRenderers[width] = r
	}

	out := source
	if rendered, err := r.Render(source); err == nil {
		out = rendered
	}
	out = strings.Trim(out, "\n")
	mdCache[key] = out
	return out
}

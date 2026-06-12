package ui

import (
	"strings"

	"github.com/charmbracelet/lipgloss"

	"obsidian-agent/tui-go/internal/theme"
)

const slashCmdCol = 20
const slashMenuLimit = 7

var slashMenuBoxStyle = lipgloss.NewStyle().
	Border(lipgloss.RoundedBorder()).
	BorderForeground(theme.BoxBorder).
	BorderLeftForeground(theme.Accent)

var slashSelectedStyle = lipgloss.NewStyle().
	Bold(true).
	Foreground(theme.Text).
	Background(theme.AccentDeep)

// renderSlashMenu draws the autocomplete dropdown: up to 7 prefix hits, the
// selected row highlighted in deep purple edge to edge. Port of SlashMenu.tsx.
func renderSlashMenu(hits []SlashCmd, selected, width int) string {
	inner := width - 4 // matches the TS pad math (border 2 + 2)
	if inner < 4 {
		inner = 4
	}
	show := hits
	if len(show) > slashMenuLimit {
		show = show[:slashMenuLimit]
	}

	rows := make([]string, 0, len(show))
	for i, sc := range show {
		cmdText := padEnd(sc.Cmd, slashCmdCol)
		if i == selected {
			line := "❯ " + cmdText + "  " + sc.Meta
			if pad := inner - len([]rune(line)); pad > 0 {
				line += strings.Repeat(" ", pad)
			}
			rows = append(rows, truncLine(slashSelectedStyle.Render(line), inner))
		} else {
			rows = append(rows, truncLine(
				dimmerStyle.Render("  ")+
					accentStyle.Render(cmdText)+
					dimStyle.Render("  "+sc.Meta),
				inner,
			))
		}
	}
	return slashMenuBoxStyle.Width(width - 2).Render(strings.Join(rows, "\n"))
}

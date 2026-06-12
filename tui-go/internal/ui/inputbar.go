package ui

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/lipgloss"

	"obsidian-agent/tui-go/internal/theme"
)

var inputBoxStyle = lipgloss.NewStyle().
	Border(lipgloss.RoundedBorder()).
	BorderForeground(theme.BoxBorder).
	BorderLeftForeground(theme.Accent).
	Padding(0, 2)

// renderInputBar draws the bordered input row plus the status row beneath it.
// Port of InputBar.tsx.
func (m *Model) renderInputBar(width int) string {
	innerW := width - 6 // border (2) + paddingX (4)
	if innerW < 4 {
		innerW = 4
	}

	var content string
	if m.isRecording {
		content = truncLine(
			lipgloss.NewStyle().Foreground(theme.StatusRed).Bold(true).Render("● REC ")+
				dimStyle.Render("recording… press Enter to stop"),
			innerW,
		)
	} else {
		content = truncLine(m.input.View(), innerW)
	}
	box := inputBoxStyle.Width(width - 2).Render(content)

	vaultName := "no vault"
	if m.vault != "" {
		parts := strings.FieldsFunc(m.vault, func(r rune) bool { return r == '/' })
		if len(parts) > 0 {
			vaultName = parts[len(parts)-1]
		}
	}
	jobsWord := "jobs"
	if len(m.jobs) == 1 {
		jobsWord = "job"
	}
	statusLeft := fmt.Sprintf("%d %s · %s", len(m.jobs), jobsWord, vaultName)
	const hint = "ctrl+p commands"
	innerStatusW := width - 4
	if innerStatusW < 0 {
		innerStatusW = 0
	}
	padLen := innerStatusW - len([]rune(statusLeft)) - len(hint) - 2
	if padLen < 0 {
		padLen = 0
	}

	hintColor := theme.StatusRed
	if m.connected {
		hintColor = theme.Accent
	}
	status := "  " + truncLine(
		dimStyle.Render(statusLeft)+
			dimStyle.Render(strings.Repeat(" ", padLen)+"  ")+
			lipgloss.NewStyle().Foreground(hintColor).Render("ctrl+p")+
			dimStyle.Render(" commands"),
		innerStatusW+2,
	)

	return box + "\n" + status
}

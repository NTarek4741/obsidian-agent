package ui

import (
	"fmt"

	"github.com/charmbracelet/lipgloss"
)

var contentPadStyle = lipgloss.NewStyle().Padding(0, 2)

// layout computes the frame geometry (port of App.tsx width/height math).
func (m *Model) layout() (sidebarW, mainW, innerW, slashH, viewportH int) {
	sidebarW = sidebarWidthFor(m.width)
	mainW = m.width - sidebarW
	if mainW < 10 {
		mainW = 10
	}
	innerW = mainW - 4
	if innerW < 10 {
		innerW = 10
	}
	if m.menuVisible {
		n := len(getSlashHits(m.input.Value()))
		if n > slashMenuLimit {
			n = slashMenuLimit
		}
		if n > 0 {
			slashH = n + 2
		}
	}
	viewportH = m.height - bottomRows - slashH
	if viewportH < 1 {
		viewportH = 1
	}
	return
}

func (m *Model) View() string {
	if m.showConfig {
		return m.renderConfigWizard()
	}
	if !m.connected && m.checking {
		return dimStyle.Render("  connecting to backend…")
	}

	sidebarW, mainW, _, _, viewportH := m.layout()

	// Top padding row — replaced by the scroll banner when scrolled up.
	topRow := ""
	if !m.vp.AtBottom() {
		scrolled := m.vp.TotalLineCount() - m.vp.YOffset - m.vp.Height
		if scrolled > 0 {
			topRow = truncLine(
				"  "+dimStyle.Render(fmt.Sprintf("  ↑ scrolled %d rows  ·  PgDn to return", scrolled)),
				mainW,
			)
		}
	}

	viewportView := lipgloss.NewStyle().
		Width(mainW).
		Height(viewportH).
		MaxHeight(viewportH).
		Render(topRow + "\n" + m.vp.View())

	parts := []string{viewportView}
	if m.menuVisible {
		parts = append(parts, renderSlashMenu(getSlashHits(m.input.Value()), m.menuSelected, mainW))
	}
	parts = append(parts, m.renderInputBar(mainW))
	left := lipgloss.JoinVertical(lipgloss.Left, parts...)

	running := 0
	for _, j := range m.jobs {
		if j.Status == "running" || j.Status == "pending" {
			running++
		}
	}
	sidebar := renderSidebar(sidebarData{
		connected:   m.connected,
		vault:       m.vault,
		totalJobs:   len(m.jobs),
		runningJobs: running,
		machines:    m.machines,
		sessionISO:  m.sessionStart.UTC().Format("2006-01-02 15:04:05Z"),
		cwdLabel:    m.cwdLabel,
	}, sidebarW, m.height)

	return lipgloss.JoinHorizontal(lipgloss.Top, left, sidebar)
}

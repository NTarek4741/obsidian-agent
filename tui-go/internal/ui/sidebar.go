package ui

import (
	"fmt"
	"path/filepath"
	"strings"

	"github.com/charmbracelet/lipgloss"

	"obsidian-agent/tui-go/internal/api"
	"obsidian-agent/tui-go/internal/theme"
)

const appVersion = "0.3.0"

// seg is one colored span within a sidebar row. Port of Sidebar.tsx Seg.
type seg struct {
	text  string
	color lipgloss.Color
	bold  bool
}

const sidebarPadL = 2

// paintRow renders one sidebar row to exactly `width` cells with the lifted
// background painted edge to edge (trailing cells included, so the terminal
// wallpaper never bleeds through).
func paintRow(width int, segs ...seg) string {
	var b strings.Builder
	used := 0
	b.WriteString(lipgloss.NewStyle().Background(theme.BgLifted).Render(strings.Repeat(" ", sidebarPadL)))
	used += sidebarPadL
	for _, s := range segs {
		remaining := width - used
		if remaining <= 0 {
			break
		}
		txt := s.text
		if len([]rune(txt)) > remaining {
			txt = string([]rune(txt)[:remaining])
		}
		st := lipgloss.NewStyle().Background(theme.BgLifted)
		if s.color != "" {
			st = st.Foreground(s.color)
		}
		if s.bold {
			st = st.Bold(true)
		}
		b.WriteString(st.Render(txt))
		used += len([]rune(txt))
	}
	if used < width {
		b.WriteString(lipgloss.NewStyle().Background(theme.BgLifted).Render(strings.Repeat(" ", width-used)))
	}
	return b.String()
}

type sidebarData struct {
	connected   bool
	vault       string
	totalJobs   int
	runningJobs int
	machines    []api.MachineInfo
	sessionISO  string
	cwdLabel    string
}

// renderSidebar draws the full-height right panel. Port of Sidebar.tsx.
func renderSidebar(d sidebarData, width, height int) string {
	inner := width - sidebarPadL
	if inner < 2 {
		inner = 2
	}

	vaultName := "no vault"
	if d.vault != "" {
		vaultName = filepath.Base(strings.TrimRight(d.vault, "/"))
	}

	var top []string
	top = append(top, paintRow(width, seg{"New session", theme.Text, true}))
	top = append(top, paintRow(width, seg{d.sessionISO, theme.Dim, false}))
	top = append(top, paintRow(width))

	top = append(top, paintRow(width, seg{"Context", theme.Text, true}))
	jobsWord := "jobs"
	if d.totalJobs == 1 {
		jobsWord = "job"
	}
	top = append(top, paintRow(width, seg{fmt.Sprintf("%d %s", d.totalJobs, jobsWord), theme.Dim, false}))
	top = append(top, paintRow(width, seg{fmt.Sprintf("%d active", d.runningJobs), theme.Dim, false}))
	top = append(top, paintRow(width))

	top = append(top, paintRow(width, seg{"Vault", theme.Text, true}))
	top = append(top, paintRow(width, seg{sliceRunes(vaultName, inner-1), theme.Dim, false}))
	top = append(top, paintRow(width))

	if len(d.machines) > 0 {
		top = append(top, paintRow(width, seg{"Machines", theme.Text, true}))
		for _, m := range d.machines {
			top = append(top, paintRow(width,
				seg{"● ", theme.PhaseColor(m.Phase), false},
				seg{padEnd(m.Name, 11), theme.Text, false},
				seg{m.Phase, theme.Dim, false},
			))
			sub := ""
			if m.LastEvent != nil && *m.LastEvent != "" {
				sub = *m.LastEvent
			} else if m.Lifecycle == "ephemeral" {
				sub = "created per job"
			}
			if sub != "" {
				top = append(top, paintRow(width,
					seg{"  " + sliceRunes(sub, inner-3), theme.Dimmer, false},
				))
			}
		}
		top = append(top, paintRow(width))
	}

	dotColor, statusText, statusColor := theme.StatusRed, "offline", theme.Dim
	if d.connected {
		dotColor, statusText, statusColor = theme.StatusGreen, "online", theme.Text
	}
	top = append(top, paintRow(width, seg{"● ", dotColor, false}, seg{statusText, statusColor, false}))

	bottom := []string{
		paintRow(width, seg{sliceRunes(d.cwdLabel, inner-1), theme.Dim, false}),
		paintRow(width,
			seg{"● ", theme.StatusGreen, false},
			seg{"obsidian-agent", theme.Text, true},
			seg{" v" + appVersion, theme.Dim, false},
		),
	}

	filler := height - len(top) - len(bottom)
	rows := make([]string, 0, height)
	rows = append(rows, top...)
	for i := 0; i < filler; i++ {
		rows = append(rows, paintRow(width))
	}
	rows = append(rows, bottom...)
	if len(rows) > height {
		rows = rows[:height]
	}
	return strings.Join(rows, "\n")
}

func sliceRunes(s string, n int) string {
	if n < 0 {
		n = 0
	}
	r := []rune(s)
	if len(r) > n {
		return string(r[:n])
	}
	return s
}

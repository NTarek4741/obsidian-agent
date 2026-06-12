package ui

import (
	"fmt"
	"strings"
	"time"

	"github.com/charmbracelet/lipgloss"

	"obsidian-agent/tui-go/internal/theme"
)

// ContentItem is the viewport's content union. Port of types/index.ts.
type ContentItem struct {
	Kind   string // user_message | assistant_message | thinking | thinking_anim | tool_footer | job
	Text   string
	JobID  string
	Footer *FooterModel
}

// FooterModel is one-line job/tool status. Port of ToolFooterModel.
type FooterModel struct {
	Agent   string
	Model   string
	Status  string // running | done | failed
	Detail  string
	Started time.Time
	Elapsed time.Duration // frozen at the final value when done/failed
}

var (
	spinnerFrames = []string{"■", "□"}

	userBoxStyle = lipgloss.NewStyle().
			Border(lipgloss.RoundedBorder()).
			BorderForeground(theme.BoxBorder).
			BorderLeftForeground(theme.Accent).
			Padding(0, 2)

	textStyle   = lipgloss.NewStyle().Foreground(theme.Text)
	dimStyle    = lipgloss.NewStyle().Foreground(theme.Dim)
	dimmerStyle = lipgloss.NewStyle().Foreground(theme.Dimmer)
	accentStyle = lipgloss.NewStyle().Foreground(theme.Accent)
	redStyle    = lipgloss.NewStyle().Foreground(theme.StatusRed)
	amberItalic = lipgloss.NewStyle().Foreground(theme.ThinkingAmber).Italic(true)
)

func formatElapsed(d time.Duration) string {
	total := int(d.Seconds())
	if total < 0 {
		total = 0
	}
	if total < 60 {
		return fmt.Sprintf("%ds", total)
	}
	return fmt.Sprintf("%d:%02d", total/60, total%60)
}

// renderToolFooter draws the one-line status row: glyph, agent, optional
// model, elapsed, detail. Port of ToolFooter.tsx (spinner derives from the
// wall clock at 500ms per frame).
func renderToolFooter(f *FooterModel, width int) string {
	var glyph string
	switch f.Status {
	case "running":
		frame := spinnerFrames[time.Now().UnixMilli()/500%int64(len(spinnerFrames))]
		glyph = accentStyle.Render(frame)
	case "failed":
		glyph = redStyle.Render("✗")
	default: // done
		glyph = dimStyle.Render("□")
	}

	elapsed := f.Elapsed
	if f.Status == "running" && !f.Started.IsZero() {
		elapsed = time.Since(f.Started)
	}

	var b strings.Builder
	b.WriteString(glyph)
	b.WriteString(accentStyle.Render(" " + f.Agent))
	if f.Model != "" {
		b.WriteString(dimmerStyle.Render(" · "))
		b.WriteString(dimStyle.Render(f.Model))
	}
	b.WriteString(dimmerStyle.Render(" · "))
	b.WriteString(dimStyle.Render(formatElapsed(elapsed)))
	detail := f.Detail
	if detail == "" && f.Status == "running" {
		detail = "running…"
	}
	if detail != "" {
		b.WriteString(dimStyle.Render(" " + detail))
	}
	return truncLine(b.String(), width)
}

// renderJob maps a JobPanel onto a ToolFooter row. Port of JobBox.tsx.
func renderJob(job *JobPanel, width int) string {
	status := job.Status
	if status == "pending" {
		status = "running"
	}
	return renderToolFooter(&FooterModel{
		Agent:   jobFooterAgent(job),
		Status:  status,
		Detail:  jobFooterDetail(job),
		Started: job.Start,
		Elapsed: time.Since(job.Start),
	}, width)
}

// renderItem renders one ContentItem at the given inner width. Visuals are
// 1:1 ports of the Ink components named in each case.
func renderItem(item ContentItem, jobs map[string]*JobPanel, width int) (string, bool) {
	switch item.Kind {
	case "user_message": // MessageBox: rounded dim border, lavender left stripe
		return userBoxStyle.Width(width).Render(textStyle.Render(item.Text)), true
	case "assistant_message":
		return renderMarkdown(item.Text, width), true
	case "thinking": // ThinkingBlock: italic amber prefix + dim body
		return lipgloss.NewStyle().Width(width).Render(
			amberItalic.Render("Thinking: ") + dimStyle.Render(item.Text),
		), true
	case "thinking_anim": // ThinkingDots: 1-3 dots cycling on the wall clock
		dots := strings.Repeat(".", int(time.Now().UnixMilli()/400)%3+1)
		return truncLine(amberItalic.Render("Thinking")+dimStyle.Render(dots), width), true
	case "tool_footer":
		return renderToolFooter(item.Footer, width), true
	case "job":
		if job, ok := jobs[item.JobID]; ok {
			return renderJob(job, width), true
		}
	}
	return "", false
}

// truncLine clips a styled single line to width cells, appending nothing
// (matches Ink's wrap="truncate-end").
func truncLine(s string, width int) string {
	return lipgloss.NewStyle().MaxWidth(width).Render(s)
}

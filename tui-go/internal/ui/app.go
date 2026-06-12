// Package ui is the Bubble Tea application: one root Model owning layout,
// content, jobs, machines, input, and the config wizard. Port of the Ink App.
package ui

import (
	"os"
	"os/exec"
	"strings"
	"time"

	"github.com/charmbracelet/bubbles/cursor"
	"github.com/charmbracelet/bubbles/textinput"
	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"obsidian-agent/tui-go/internal/api"
	"obsidian-agent/tui-go/internal/history"
	"obsidian-agent/tui-go/internal/recorder"
	"obsidian-agent/tui-go/internal/theme"
)

// InputBar owns: 1 top-border + 1 input row + 1 bottom-border + 1 status row.
const bottomRows = 4

// sidebarWidthFor: ~22% of the terminal, clamped to 32..40 so the panel feels
// right on both narrow and ultrawide terminals (port of App.tsx).
func sidebarWidthFor(termW int) int {
	w := termW * 22 / 100
	if w < 32 {
		w = 32
	}
	if w > 40 {
		w = 40
	}
	return w
}

type Model struct {
	client *api.Client
	hist   *history.History

	width, height int

	// Backend state (port of useBackend).
	connected       bool
	configured      bool
	vault           string
	checking        bool // true until the first health response lands
	healthTickArmed bool // exactly one 3s health chain

	// Content & jobs.
	items       []ContentItem
	jobs        map[string]*JobPanel
	activeJobID string
	resolved    map[string]bool
	polling     map[string]bool

	// Machines.
	machines          []api.MachineInfo
	machinesLoop      bool // 5s machine chain started
	machinesTickArmed bool

	// Input & viewport.
	input textinput.Model
	vp    viewport.Model

	// Slash menu.
	menuVisible  bool
	menuSelected int

	// Config wizard.
	showConfig   bool
	wizStep      int
	wizAPIKey    string
	wizVaultPath string

	// Live recording.
	isRecording  bool
	rec          *recorder.Handle
	recStart     time.Time
	recFooterIdx int

	// One animation heartbeat; frames derive from the wall clock.
	animTicking bool

	sessionStart time.Time
	cwdLabel     string
}

func New(backendURL string) *Model {
	input := textinput.New()
	input.Prompt = "❯ "
	input.PromptStyle = lipgloss.NewStyle().Foreground(theme.Dim)
	input.TextStyle = lipgloss.NewStyle().Foreground(theme.Text)
	input.Cursor.Style = lipgloss.NewStyle().Foreground(theme.Text).Background(theme.AccentDeep)
	input.Cursor.SetMode(cursor.CursorStatic)
	input.Focus()

	m := &Model{
		client:       api.NewClient(backendURL),
		hist:         history.Load(),
		width:        120,
		height:       40,
		checking:     true,
		jobs:         map[string]*JobPanel{},
		resolved:     map[string]bool{},
		polling:      map[string]bool{},
		input:        input,
		vp:           viewport.New(0, 0),
		recFooterIdx: -1,
		sessionStart: time.Now(),
		cwdLabel:     cwdLabel(),
	}
	m.vp.KeyMap = viewport.KeyMap{} // the root model owns all keys
	m.vp.MouseWheelEnabled = true

	m.pushAssistant("**Obsidian Agent** v" + appVersion + "\n\nAI-powered tools for your Obsidian vault. Type `/help` for all commands.")
	return m
}

func (m *Model) Init() tea.Cmd {
	return m.checkHealthCmd()
}

// ─── Item helpers (port of App.tsx pushItem/pushUser/...) ───────────────────

func (m *Model) pushItem(item ContentItem) {
	m.items = append(m.items, item)
}

func (m *Model) pushAssistant(text string) {
	m.pushItem(ContentItem{Kind: "assistant_message", Text: text})
}

// dropThinking removes all thinking_anim items (called when a concrete
// response arrives).
func (m *Model) dropThinking() {
	out := m.items[:0]
	for _, it := range m.items {
		if it.Kind != "thinking_anim" {
			out = append(out, it)
		}
	}
	m.items = out
	if m.recFooterIdx >= len(m.items) {
		m.recFooterIdx = -1
	}
}

// ─── Backend commands ────────────────────────────────────────────────────────

func (m *Model) checkHealthCmd() tea.Cmd {
	client := m.client
	return func() tea.Msg {
		resp, err := client.Health()
		return healthMsg{resp: resp, err: err}
	}
}

func healthTick() tea.Cmd {
	return tea.Tick(3*time.Second, func(time.Time) tea.Msg { return healthTickMsg{} })
}

func (m *Model) machinesCmd() tea.Cmd {
	client := m.client
	return func() tea.Msg {
		resp, err := client.GetMachines()
		return machinesMsg{resp: resp, err: err}
	}
}

func machinesTick() tea.Cmd {
	return tea.Tick(5*time.Second, func(time.Time) tea.Msg { return machinesTickMsg{} })
}

func (m *Model) pollJobCmd(key string) tea.Cmd {
	client := m.client
	return func() tea.Msg {
		resp, err := client.PollJob(key)
		return jobStatusMsg{key: key, resp: resp, err: err}
	}
}

func jobPollTick(key string) tea.Cmd {
	return tea.Tick(2*time.Second, func(time.Time) tea.Msg { return jobPollTickMsg{key: key} })
}

func animTick() tea.Cmd {
	return tea.Tick(250*time.Millisecond, func(time.Time) tea.Msg { return animTickMsg{} })
}

// animating reports whether anything on screen needs the heartbeat.
func (m *Model) animating() bool {
	if m.isRecording {
		return true
	}
	for _, it := range m.items {
		switch it.Kind {
		case "thinking_anim":
			return true
		case "tool_footer":
			if it.Footer != nil && it.Footer.Status == "running" {
				return true
			}
		case "job":
			if j, ok := m.jobs[it.JobID]; ok && (j.Status == "running" || j.Status == "pending") {
				return true
			}
		}
	}
	return false
}

// ensureAnim arms the heartbeat if something animates and it isn't armed yet.
func (m *Model) ensureAnim() tea.Cmd {
	if m.animTicking || !m.animating() {
		return nil
	}
	m.animTicking = true
	return animTick()
}

func cwdLabel() string {
	cwd, err := os.Getwd()
	if err != nil {
		return ""
	}
	if home, err := os.UserHomeDir(); err == nil && strings.HasPrefix(cwd, home) {
		cwd = "~" + strings.TrimPrefix(cwd, home)
	}
	branch := ""
	if out, err := exec.Command("git", "rev-parse", "--abbrev-ref", "HEAD").Output(); err == nil {
		branch = strings.TrimSpace(string(out))
	}
	if branch != "" {
		return cwd + ":" + branch
	}
	return cwd
}

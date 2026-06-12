// Obsidian Agent TUI — Go/Bubble Tea frontend. Launched by main.py, which
// owns the backend lifecycle; the backend URL comes from
// OBSIDIAN_AGENT_BACKEND_URL (env or project .env), default localhost:8000.
package main

import (
	"fmt"
	"os"

	tea "github.com/charmbracelet/bubbletea"

	"obsidian-agent/tui-go/internal/ui"
)

func main() {
	p := tea.NewProgram(ui.New(backendURL()), tea.WithAltScreen())
	if _, err := p.Run(); err != nil {
		fmt.Fprintf(os.Stderr, "obsidian-agent-tui: %v\n", err)
		os.Exit(1)
	}
}

// Package theme is the Obsidian-flavored dark palette. Lavender purple accent,
// deeper purple selection highlight, dark gray surfaces with a visibly lifted
// sidebar. Mirrors tui/src/styles/theme.ts — components MUST read from these,
// not from hex literals.
package theme

import "github.com/charmbracelet/lipgloss"

var (
	Bg            = lipgloss.Color("#181818")
	BgLifted      = lipgloss.Color("#232323")
	BoxBorder     = lipgloss.Color("#3a3a3a")
	Accent        = lipgloss.Color("#a78bfa")
	AccentDeep    = lipgloss.Color("#7c3aed")
	Text          = lipgloss.Color("#dcddde")
	Dim           = lipgloss.Color("#9e9e9e")
	Dimmer        = lipgloss.Color("#5e5e5e")
	ThinkingAmber = lipgloss.Color("#fab283")
	StatusGreen   = lipgloss.Color("#3fb950")
	StatusRed     = lipgloss.Color("#f85149")
	Warning       = lipgloss.Color("#f5a742")
	MdCodeBg      = lipgloss.Color("#1f1f1f")

	// ConfigWizard-only colors.
	WizardLabel = lipgloss.Color("#9895b5")
	WizardValue = lipgloss.Color("#c9c5d9")
)

// PhaseColor maps a machine phase to its status-dot color. In-flight lifecycle
// phases pulse accent so the panel reads as "something is happening on this
// machine right now".
func PhaseColor(phase string) lipgloss.Color {
	switch phase {
	case "running":
		return StatusGreen
	case "sleeping":
		return Warning
	case "error":
		return StatusRed
	case "creating", "waking", "setup", "refreshing", "destroying":
		return Accent
	default: // none / destroyed / registered / unknown
		return Dimmer
	}
}

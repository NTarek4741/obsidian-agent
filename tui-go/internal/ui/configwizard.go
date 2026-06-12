package ui

import (
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"obsidian-agent/tui-go/internal/theme"
)

var (
	wizTitleStyle = lipgloss.NewStyle().Bold(true).Foreground(theme.Accent)
	wizLabelStyle = lipgloss.NewStyle().Foreground(theme.WizardLabel)
	wizValueStyle = lipgloss.NewStyle().Foreground(theme.WizardValue)
	wizPadStyle   = lipgloss.NewStyle().Padding(2)
)

// renderConfigWizard draws the two-step setup modal: masked API key, then
// vault path. Port of ConfigWizard.tsx.
func (m *Model) renderConfigWizard() string {
	var lines []string
	lines = append(lines, wizTitleStyle.Render("  obsidian agent "))
	lines = append(lines, wizLabelStyle.Render(" setup"))
	lines = append(lines, " ")

	if m.wizStep == 0 {
		value := "_"
		if len(m.wizAPIKey) > 0 {
			value = strings.Repeat("•", len([]rune(m.wizAPIKey)))
		}
		lines = append(lines,
			wizLabelStyle.Render(" Dedalus API key"),
			wizValueStyle.Render("  "+value),
			" ",
			wizLabelStyle.Render(" enter to continue   esc to cancel"),
		)
	} else {
		value := m.wizVaultPath
		if value == "" {
			value = "_"
		}
		lines = append(lines,
			wizLabelStyle.Render(" Obsidian vault path"),
			wizValueStyle.Render("  "+value),
			" ",
			wizLabelStyle.Render(" enter to save   esc to go back"),
		)
	}
	return wizPadStyle.Render(strings.Join(lines, "\n"))
}

// handleWizardKey processes input while the wizard is open. Returns a command
// when the wizard submits the config.
func (m *Model) handleWizardKey(msg tea.KeyMsg) tea.Cmd {
	switch msg.Type {
	case tea.KeyEscape:
		if m.wizStep == 0 {
			m.closeWizard(false, "")
		} else {
			m.wizStep = 0
		}
		return nil

	case tea.KeyEnter:
		if m.wizStep == 0 {
			if strings.TrimSpace(m.wizAPIKey) != "" {
				m.wizStep = 1
			}
			return nil
		}
		if strings.TrimSpace(m.wizVaultPath) != "" {
			apiKey, vaultPath := m.wizAPIKey, m.wizVaultPath
			client := m.client
			return func() tea.Msg {
				resp, err := client.SaveConfig(apiKey, vaultPath)
				if err != nil {
					return configSavedMsg{ok: false}
				}
				return configSavedMsg{ok: true, vault: resp.Vault}
			}
		}
		return nil

	case tea.KeyBackspace, tea.KeyDelete:
		if m.wizStep == 0 {
			m.wizAPIKey = dropLastRune(m.wizAPIKey)
		} else {
			m.wizVaultPath = dropLastRune(m.wizVaultPath)
		}
		return nil

	case tea.KeyRunes, tea.KeySpace:
		if msg.Alt {
			return nil
		}
		input := string(msg.Runes)
		if msg.Type == tea.KeySpace {
			input = " "
		}
		if m.wizStep == 0 {
			m.wizAPIKey += input
		} else {
			m.wizVaultPath += input
		}
	}
	return nil
}

// closeWizard ends the wizard. On success the App pushes "✓ Config saved."
// and re-checks health; otherwise "✗ Config save failed." (the TS app prints
// the failure line on Esc-cancel too — preserved).
func (m *Model) closeWizard(success bool, vault string) {
	m.showConfig = false
	m.wizStep = 0
	m.wizAPIKey = ""
	m.wizVaultPath = ""
	if success {
		if vault != "" {
			m.vault = vault
		}
		m.pushAssistant("✓ Config saved.")
	} else {
		m.pushAssistant("✗ Config save failed.")
	}
}

func dropLastRune(s string) string {
	r := []rune(s)
	if len(r) == 0 {
		return s
	}
	return string(r[:len(r)-1])
}

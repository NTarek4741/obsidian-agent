// Package history persists command history at ~/.config/obsidian-agent/history
// (one command per line — the same file the previous TUI used) and provides
// Up/Down cursor navigation with the same semantics as tui/src/hooks/useHistory.ts.
package history

import (
	"os"
	"path/filepath"
	"strings"
)

type History struct {
	entries []string
	idx     int
	path    string
}

func Load() *History {
	home, err := os.UserHomeDir()
	if err != nil {
		home = "."
	}
	path := filepath.Join(home, ".config", "obsidian-agent", "history")
	var entries []string
	if data, err := os.ReadFile(path); err == nil {
		for _, line := range strings.Split(string(data), "\n") {
			if strings.TrimSpace(line) != "" {
				entries = append(entries, line)
			}
		}
	}
	return &History{entries: entries, idx: len(entries), path: path}
}

func (h *History) Append(cmd string) {
	h.entries = append(h.entries, cmd)
	h.idx = len(h.entries)
	if err := os.MkdirAll(filepath.Dir(h.path), 0o755); err == nil {
		_ = os.WriteFile(h.path, []byte(strings.Join(h.entries, "\n")+"\n"), 0o644)
	}
}

// Prev moves the cursor back one entry. Returns ("", false) when there is
// nothing to show (empty history).
func (h *History) Prev() (string, bool) {
	idx := h.idx
	if idx > 0 {
		idx--
	}
	if idx < 0 || idx >= len(h.entries) {
		return "", false
	}
	h.idx = idx
	return h.entries[idx], true
}

// Next moves the cursor forward; past the end it returns an empty string and
// resets (matching the TS hook, which clears the input there).
func (h *History) Next() (string, bool) {
	if h.idx < len(h.entries)-1 {
		h.idx++
		return h.entries[h.idx], true
	}
	h.idx = len(h.entries)
	return "", true
}

func (h *History) Reset() {
	h.idx = len(h.entries)
}

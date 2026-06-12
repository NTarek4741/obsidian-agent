package ui

import (
	"encoding/json"
	"fmt"
	"regexp"
	"slices"
	"strings"
	"time"
)

// JobPanel tracks one backend job in the UI. Port of types/index.ts JobPanel.
type JobPanel struct {
	ID           string
	Kind         string
	Status       string // pending | running | done | failed
	Progress     []string
	Start        time.Time
	Error        string
	Result       map[string]any
	SubmittedCmd string
}

// Lines emitted by setup/install scripts that are noise to the user. We
// collapse long apt-get / dpkg runs into a single "installing system
// packages…" line. Port of useJobs.ts APT_NOISE_RE.
var aptNoiseRE = regexp.MustCompile(`^(Reading database|Unpacking |Preparing to unpack|Selecting previously|Setting up |Processing triggers|\(Reading|Get:|Hit:|Fetched|Inst |Conf )`)

// Maximum length of any single progress line shown to the user.
const maxProgressLineLen = 160

func filterProgress(incoming, existing []string) []string {
	out := append([]string{}, existing...)
	bufferedNoise := false

	for _, raw := range incoming {
		line := strings.TrimRight(raw, " \t\r\n")
		if line == "" {
			continue
		}
		if aptNoiseRE.MatchString(line) {
			if !bufferedNoise {
				const summary = "Installing system packages…"
				if len(out) == 0 || out[len(out)-1] != summary {
					out = append(out, summary)
				}
				bufferedNoise = true
			}
			continue
		}
		bufferedNoise = false
		trimmed := line
		if len([]rune(line)) > maxProgressLineLen {
			trimmed = string([]rune(line)[:maxProgressLineLen-1]) + "…"
		}
		if !slices.Contains(out, trimmed) {
			out = append(out, trimmed)
		}
	}
	return out
}

// extractResultText tries common result keys in order, surfaces path fields
// as "Saved to `…`", and falls back to pretty JSON. Port of useJobs.ts.
func extractResultText(result map[string]any) string {
	if result == nil {
		return ""
	}
	for _, key := range []string{"text", "result", "message", "output", "content"} {
		if v, ok := result[key].(string); ok && strings.TrimSpace(v) != "" {
			return v
		}
	}
	for _, key := range []string{"path", "file", "filepath", "file_path", "note_path", "mind_map_path"} {
		if v, ok := result[key].(string); ok {
			return fmt.Sprintf("Saved to `%s`", v)
		}
	}
	if data, err := json.MarshalIndent(result, "", "  "); err == nil {
		return "```json\n" + string(data) + "\n```"
	}
	return fmt.Sprintf("%v", result)
}

// jobFooterDetail maps a JobPanel to its one-line detail. Port of JobBox.tsx.
func jobFooterDetail(job *JobPanel) string {
	switch job.Status {
	case "running", "pending":
		if len(job.Progress) > 0 {
			return job.Progress[len(job.Progress)-1]
		}
	case "failed":
		return job.Error
	case "done":
		if job.Result != nil {
			for _, key := range []string{"path", "file", "filepath", "file_path", "note_path", "mind_map_path", "output"} {
				if v, ok := job.Result[key].(string); ok {
					if len([]rune(v)) > 80 {
						return string([]rune(v)[:80])
					}
					return v
				}
			}
		}
	}
	return ""
}

// jobFooterAgent derives the footer agent label from the submitted command:
// "/research deep x" → "research-deep". Port of JobBox.tsx.
func jobFooterAgent(job *JobPanel) string {
	cmd := strings.TrimPrefix(job.SubmittedCmd, "/")
	if cmd == "" {
		return job.Kind
	}
	parts := strings.Fields(cmd)
	if len(parts) > 2 {
		parts = parts[:2]
	}
	if len(parts) == 0 {
		return job.Kind
	}
	return strings.Join(parts, "-")
}

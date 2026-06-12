package ui

import (
	"fmt"
	"strconv"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"obsidian-agent/tui-go/internal/api"
)

// SlashCmd is one autocomplete entry. The meta strings (incl. their internal
// padding) are verbatim from useCommands.ts.
type SlashCmd struct {
	Cmd  string
	Meta string
}

var allSlashCommands = []SlashCmd{
	{"/chat", "<question>       Ask your synced agent vault"},
	{"/podcast", "<note-path>      Generate WAV podcast from note"},
	{"/flashcard", "<note-path>      Generate Anki deck (ephemeral sandbox)"},
	{"/transcribe", "<file-path>      Transcribe audio/video file"},
	{"/transcribe yt", "<url>            Transcribe YouTube video"},
	{"/transcribe live", "                 Start microphone recording"},
	{"/research fast", "<topic>          Quick research note"},
	{"/research deep", "<topic>          Deep research (plan + build)"},
	{"/mindmap", "<note-path>      Create mind map from note"},
	{"/machines", "                 Show Dedalus machine status"},
	{"/config", "                 Setup API key + vault path"},
	{"/clear", "                 Clear the output area"},
	{"/help", "                 Show all commands"},
	{"/quit", "                 Quit"},
}

// getSlashHits returns commands prefix-matching the input. Port of SlashMenu.tsx.
func getSlashHits(input string) []SlashCmd {
	if !strings.HasPrefix(input, "/") {
		return nil
	}
	lo := strings.TrimSpace(strings.ToLower(input))
	var hits []SlashCmd
	for _, sc := range allSlashCommands {
		if strings.HasPrefix(strings.ToLower(sc.Cmd), lo) {
			hits = append(hits, sc)
		}
	}
	return hits
}

var helpText = strings.Join([]string{
	"**Commands**",
	"",
	"```",
	"/chat <question>          Ask your synced agent vault",
	"/podcast <note-path>      Generate WAV podcast from note",
	"/flashcard <note-path>    Generate Anki deck (ephemeral sandbox)",
	"/transcribe <file>        Transcribe audio/video file",
	"/transcribe yt <url>      Transcribe YouTube video",
	"/transcribe live          Start microphone recording",
	"/research fast <topic>    Quick research note",
	"/research deep <topic>    Deep research (plan + build)",
	"/mindmap <note-path>      Create mind map from note",
	"/machines                 Show Dedalus machine status",
	"/config                   Setup API key + vault path",
	"/clear                    Clear the output area",
	"/help                     Show all commands",
	"/quit                     Quit",
	"```",
}, "\n")

func padEnd(s string, n int) string {
	if len(s) >= n {
		return s
	}
	return s + strings.Repeat(" ", n-len(s))
}

// formatMachines renders the /machines report. Port of useCommands.ts.
func formatMachines(resp api.MachinesResp) string {
	lines := []string{"**Dedalus machines**", "", "```"}
	for _, m := range resp.Machines {
		lines = append(lines, fmt.Sprintf("%s %s %s %s",
			padEnd(m.Name, 10), padEnd(m.Lifecycle, 11), padEnd(m.Phase, 11), m.Resources))
		id := "—"
		if m.MachineID != nil {
			id = *m.MachineID
		}
		detail := fmt.Sprintf("           id: %s   autosleep: %s", id, m.Autosleep)
		if m.WakeSeconds != nil {
			detail += fmt.Sprintf("   last wake: %ss", strconv.FormatFloat(*m.WakeSeconds, 'f', -1, 64))
		}
		lines = append(lines, detail)
		if m.LastEvent != nil && *m.LastEvent != "" {
			lines = append(lines, "           "+*m.LastEvent)
		}
	}
	lines = append(lines, "```")
	events := resp.Events
	if len(events) > 8 {
		events = events[:8]
	}
	if len(events) > 0 {
		lines = append(lines, "", "**Recent events**", "", "```")
		for _, e := range events {
			t := time.Unix(int64(e.TS), 0).Local().Format("15:04:05")
			lines = append(lines, fmt.Sprintf("%s  %s %s", t, padEnd(e.Machine, 10), e.Event))
		}
		lines = append(lines, "```")
	}
	return strings.Join(lines, "\n")
}

func msgResult(text string) []cmdResult {
	return []cmdResult{{typ: "message", text: text}}
}

func jobResult(kind string, resp api.JobResp, err error, failText string) []cmdResult {
	if err != nil {
		return msgResult(fmt.Sprintf("%s: %v", failText, err))
	}
	return []cmdResult{{typ: "job_start", kind: kind, realID: resp.JobID}}
}

// dispatchCmd runs one command line off-thread and yields a commandResultMsg.
// Port of useCommands.ts dispatch (the activeJobID gate lives in Update —
// submissions are swallowed there, matching App.tsx).
func dispatchCmd(client *api.Client, input string) tea.Cmd {
	return func() tea.Msg {
		return commandResultMsg{submitted: input, results: dispatch(client, input)}
	}
}

func dispatch(client *api.Client, input string) []cmdResult {
	trimmed := strings.TrimSpace(input)
	if !strings.HasPrefix(trimmed, "/") {
		return msgResult("Commands start with /  — type / to see the list.")
	}

	parts := strings.Split(trimmed[1:], " ")
	verb := strings.ToLower(parts[0])
	args := strings.TrimSpace(strings.Join(parts[1:], " "))

	switch verb {
	case "chat":
		if args == "" {
			return msgResult("Usage: /chat <question>")
		}
		resp, err := client.StartChat(args)
		if err != nil {
			return msgResult(fmt.Sprintf("Chat failed: %v", err))
		}
		return []cmdResult{{typ: "job_start", kind: "chat", realID: resp.JobID}}

	case "machines":
		resp, err := client.GetMachines()
		if err != nil {
			return msgResult(fmt.Sprintf("Failed to fetch machines: %v", err))
		}
		return msgResult(formatMachines(resp))

	case "podcast":
		if args == "" {
			return msgResult("Usage: /podcast <note-path>")
		}
		resp, err := client.StartPodcast(args)
		return jobResult("podcast", resp, err, "Failed to start podcast")

	case "flashcard":
		if args == "" {
			return msgResult("Usage: /flashcard <note-path>")
		}
		resp, err := client.StartFlashcard(args)
		return jobResult("flashcard", resp, err, "Failed to start flashcard")

	case "transcribe":
		subParts := strings.Split(args, " ")
		sub := strings.ToLower(subParts[0])
		subArgs := strings.TrimSpace(strings.Join(subParts[1:], " "))

		switch sub {
		case "live":
			return []cmdResult{{typ: "transcribe_live"}}
		case "yt":
			if subArgs == "" {
				return msgResult("Usage: /transcribe yt <url>")
			}
			resp, err := client.StartTranscribe(subArgs)
			if err != nil {
				return msgResult(fmt.Sprintf("Transcribe failed: %v", err))
			}
			return []cmdResult{{typ: "job_start", kind: "transcribe", realID: resp.JobID}}
		default:
			content := strings.TrimSpace(args)
			if content == "" {
				return msgResult("Usage: /transcribe <file|url>  |  /transcribe yt <url>")
			}
			resp, err := client.StartTranscribe(content)
			if err != nil {
				return msgResult(fmt.Sprintf("Transcribe failed: %v", err))
			}
			return []cmdResult{{typ: "job_start", kind: "transcribe", realID: resp.JobID}}
		}

	case "research":
		rParts := strings.Split(args, " ")
		mode := strings.ToLower(rParts[0])
		topic := strings.TrimSpace(strings.Join(rParts[1:], " "))
		if topic == "" {
			return msgResult("Usage: /research fast <topic>   or   /research deep <topic>")
		}
		switch mode {
		case "fast":
			resp, err := client.StartFastResearch(topic)
			return jobResult("research fast", resp, err, "Failed to start research")
		case "deep":
			resp, err := client.StartDeepResearch(topic)
			return jobResult("research deep", resp, err, "Failed to start deep research")
		default:
			return msgResult(fmt.Sprintf("Unknown mode %q. Use fast or deep.", mode))
		}

	case "mindmap":
		if args == "" {
			return msgResult("Usage: /mindmap <note-path>")
		}
		resp, err := client.StartMindMap(args)
		return jobResult("mindmap", resp, err, "Failed to start mind map")

	case "config":
		return []cmdResult{{typ: "config"}}
	case "clear":
		return []cmdResult{{typ: "clear"}}
	case "help":
		return []cmdResult{{typ: "help"}}
	case "quit":
		return []cmdResult{{typ: "quit"}}
	}

	return msgResult(fmt.Sprintf("Unknown command: /%s  — type / to see the list.", verb))
}

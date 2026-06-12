package ui

import (
	"fmt"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"obsidian-agent/tui-go/internal/recorder"
)

// Verbs that start a "real" job — those clear the screen so request + status
// + result form one tight thread (port of App.tsx JOB_STARTING).
var jobStartingVerbs = map[string]bool{
	"podcast": true, "flashcard": true, "transcribe": true,
	"research": true, "mindmap": true,
}

func (m *Model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width, m.height = msg.Width, msg.Height
		m.syncLayout()
		m.syncViewport()
		return m, nil

	case tea.KeyMsg:
		return m.handleKey(msg)

	case healthMsg:
		return m.handleHealth(msg)

	case healthTickMsg:
		m.healthTickArmed = false
		return m, m.checkHealthCmd()

	case machinesMsg:
		if msg.err == nil {
			m.machines = msg.resp.Machines
		}
		if !m.machinesTickArmed {
			m.machinesTickArmed = true
			return m, machinesTick()
		}
		return m, nil

	case machinesTickMsg:
		m.machinesTickArmed = false
		if m.connected {
			return m, m.machinesCmd()
		}
		m.machinesTickArmed = true
		return m, machinesTick()

	case jobStatusMsg:
		return m.handleJobStatus(msg)

	case jobPollTickMsg:
		return m, m.pollJobCmd(msg.key)

	case commandResultMsg:
		return m.handleCommandResults(msg)

	case recorderStoppedMsg:
		return m.handleRecorderStopped(msg)

	case configSavedMsg:
		if msg.ok {
			m.closeWizard(true, msg.vault)
			m.syncViewport()
			return m, m.checkHealthCmd()
		}
		m.closeWizard(false, "")
		m.syncViewport()
		return m, nil

	case animTickMsg:
		m.animTicking = false
		m.syncViewport()
		if m.animating() {
			m.animTicking = true
			return m, animTick()
		}
		return m, nil
	}

	return m, nil
}

// ─── Key routing (port of InputBar.tsx useInput + App.tsx handlers) ─────────

func (m *Model) handleKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "ctrl+c", "ctrl+q":
		return m, tea.Quit
	}

	if m.showConfig {
		return m, m.handleWizardKey(msg)
	}

	switch msg.String() {
	case "pgup":
		m.vp.ScrollUp(m.scrollStep())
		return m, nil
	case "pgdown":
		m.vp.ScrollDown(m.scrollStep())
		return m, nil
	case "ctrl+p":
		// The status row advertises ctrl+p — drop a "/" in to open the menu.
		if m.input.Value() == "" && !m.isRecording {
			m.input.SetValue("/")
			m.input.CursorEnd()
			m.refreshMenu()
		}
		return m, nil
	}

	if m.menuVisible {
		hits := getSlashHits(m.input.Value())
		limit := len(hits)
		if limit > slashMenuLimit {
			limit = slashMenuLimit
		}
		switch msg.String() {
		case "up":
			if m.menuSelected > 0 {
				m.menuSelected--
			}
			return m, nil
		case "down":
			if m.menuSelected < limit-1 {
				m.menuSelected++
			}
			return m, nil
		case "enter":
			if m.menuSelected < len(hits) {
				m.input.SetValue(hits[m.menuSelected].Cmd + " ")
				m.input.CursorEnd()
				m.refreshMenu()
			}
			return m, nil
		case "esc":
			m.menuVisible = false
			m.syncLayout()
			m.syncViewport()
			return m, nil
		}
	}

	switch msg.String() {
	case "up":
		if val, ok := m.hist.Prev(); ok {
			m.input.SetValue(val)
			m.input.CursorEnd()
			m.refreshMenu()
		}
		return m, nil
	case "down":
		if val, ok := m.hist.Next(); ok {
			m.input.SetValue(val)
			m.input.CursorEnd()
			m.refreshMenu()
		}
		return m, nil
	case "esc":
		if m.isRecording {
			if m.rec != nil {
				m.rec.Cancel()
				m.rec = nil
			}
			m.isRecording = false
			elapsed := time.Since(m.recStart)
			m.updateRecFooter(func(f *FooterModel) {
				f.Status = "failed"
				f.Detail = "cancelled"
				f.Elapsed = elapsed
			})
			m.syncViewport()
		} else {
			m.input.SetValue("")
			m.hist.Reset()
			m.refreshMenu()
		}
		return m, nil
	case "enter":
		return m, m.handleSubmit()
	}

	var cmd tea.Cmd
	m.input, cmd = m.input.Update(msg)
	m.refreshMenu()
	return m, cmd
}

func (m *Model) scrollStep() int {
	_, _, _, _, viewportH := m.layout()
	step := viewportH - 2
	if step < 1 {
		step = 1
	}
	return step
}

// ─── Submit flow (port of App.tsx handleSubmit) ─────────────────────────────

func (m *Model) handleSubmit() tea.Cmd {
	if m.isRecording {
		return m.stopRecordingCmd()
	}

	// A job is already running — silently swallow new submissions. The user
	// sees the input clear; the current job's report lands when its poll
	// completes.
	if m.activeJobID != "" {
		m.input.SetValue("")
		m.menuVisible = false
		m.syncLayout()
		return nil
	}

	val := strings.TrimSpace(m.input.Value())
	if val == "" {
		return nil
	}

	m.hist.Append(val)
	m.input.SetValue("")
	m.menuVisible = false
	m.menuSelected = 0
	m.syncLayout()

	verb := ""
	if strings.HasPrefix(val, "/") {
		if fields := strings.Fields(val[1:]); len(fields) > 0 {
			verb = strings.ToLower(fields[0])
		}
	}

	if jobStartingVerbs[verb] {
		m.items = []ContentItem{
			{Kind: "user_message", Text: val},
			{Kind: "thinking_anim"},
		}
		m.recFooterIdx = -1
	} else {
		m.pushItem(ContentItem{Kind: "user_message", Text: val})
	}
	m.syncViewport()

	return tea.Batch(dispatchCmd(m.client, val), m.ensureAnim())
}

// ─── Message handlers ────────────────────────────────────────────────────────

func (m *Model) handleHealth(msg healthMsg) (tea.Model, tea.Cmd) {
	m.checking = false
	var cmds []tea.Cmd

	if msg.err != nil {
		m.connected = false
	} else {
		m.connected = true
		m.configured = msg.resp.Configured
		if msg.resp.Vault != nil {
			m.vault = *msg.resp.Vault
		}
		if !m.configured && !m.showConfig {
			m.showConfig = true
		}
		if !m.machinesLoop {
			m.machinesLoop = true
			cmds = append(cmds, m.machinesCmd())
		}
	}

	if !m.healthTickArmed {
		m.healthTickArmed = true
		cmds = append(cmds, healthTick())
	}
	return m, tea.Batch(cmds...)
}

func (m *Model) handleJobStatus(msg jobStatusMsg) (tea.Model, tea.Cmd) {
	job, ok := m.jobs[msg.key]
	if !ok {
		m.polling[msg.key] = false
		return m, nil
	}
	if msg.err != nil {
		// Transient poll failure — keep trying, matching useJobs.ts.
		return m, jobPollTick(msg.key)
	}

	job.Status = msg.resp.Status
	job.Progress = filterProgress(msg.resp.Progress, job.Progress)
	if msg.resp.Error != nil {
		job.Error = *msg.resp.Error
	}
	if msg.resp.Result != nil {
		job.Result = msg.resp.Result
	}

	if job.Status == "done" || job.Status == "failed" {
		m.polling[msg.key] = false
		if m.activeJobID == msg.key {
			m.activeJobID = ""
		}
		if !m.resolved[msg.key] {
			m.resolved[msg.key] = true
			m.dropThinking()
			if job.Status == "failed" {
				errText := job.Error
				if errText == "" {
					errText = "unknown error"
				}
				m.pushAssistant(fmt.Sprintf("✗ **%s** failed: %s", job.Kind, errText))
			} else if text := extractResultText(job.Result); text != "" {
				m.pushAssistant(text)
			}
		}
		m.syncViewport()
		return m, nil
	}

	m.syncViewport()
	return m, jobPollTick(msg.key)
}

func (m *Model) handleCommandResults(msg commandResultMsg) (tea.Model, tea.Cmd) {
	var cmds []tea.Cmd
	for _, res := range msg.results {
		switch res.typ {
		case "message":
			m.dropThinking()
			m.pushAssistant(res.text)

		case "job_start":
			m.dropThinking()
			key := res.realID
			if key == "" {
				key = "_" + res.kind
			}
			m.jobs[key] = &JobPanel{
				ID: key, Kind: res.kind, Status: "running",
				Start: time.Now(), SubmittedCmd: msg.submitted,
			}
			m.activeJobID = key
			m.pushItem(ContentItem{Kind: "job", JobID: key})
			if !m.polling[key] {
				m.polling[key] = true
				cmds = append(cmds, m.pollJobCmd(key))
			}

		case "transcribe_live":
			m.dropThinking()
			cmds = append(cmds, m.startRecording())

		case "config":
			m.showConfig = true

		case "clear":
			m.items = nil
			m.recFooterIdx = -1

		case "help":
			m.pushAssistant(helpText)

		case "quit":
			return m, tea.Quit
		}
	}
	m.syncViewport()
	cmds = append(cmds, m.ensureAnim())
	return m, tea.Batch(cmds...)
}

// ─── Live recording (port of App.tsx start/stopLiveRecord) ──────────────────

func (m *Model) startRecording() tea.Cmd {
	h, err := recorder.Start()
	if err != nil {
		m.pushAssistant(fmt.Sprintf("✗ Live recording failed: %v", err))
		return nil
	}
	m.rec = h
	m.recStart = time.Now()
	m.isRecording = true
	m.pushItem(ContentItem{Kind: "tool_footer", Footer: &FooterModel{
		Agent:   "transcribe-live",
		Model:   "microphone",
		Status:  "running",
		Detail:  "recording — press Enter to stop",
		Started: time.Now(),
	}})
	m.recFooterIdx = len(m.items) - 1
	return m.ensureAnim()
}

func (m *Model) stopRecordingCmd() tea.Cmd {
	h := m.rec
	if h == nil {
		return nil
	}
	m.rec = nil
	m.isRecording = false
	start := m.recStart
	m.updateRecFooter(func(f *FooterModel) { f.Detail = "processing…" })
	m.syncViewport()

	client := m.client
	return func() tea.Msg {
		_ = h.Stop()
		elapsed := time.Since(start).Milliseconds()
		if !recorder.FileLooksValid(h.FilePath) {
			return recorderStoppedMsg{invalid: true, elapsed: elapsed}
		}
		resp, err := client.StartTranscribe(h.FilePath)
		if err != nil {
			return recorderStoppedMsg{err: err, elapsed: elapsed}
		}
		recorder.Cleanup(h.FilePath, 60*time.Second)
		return recorderStoppedMsg{jobID: resp.JobID, elapsed: elapsed}
	}
}

func (m *Model) handleRecorderStopped(msg recorderStoppedMsg) (tea.Model, tea.Cmd) {
	elapsed := time.Duration(msg.elapsed) * time.Millisecond
	switch {
	case msg.invalid:
		m.updateRecFooter(func(f *FooterModel) {
			f.Status = "failed"
			f.Detail = "no audio captured"
			f.Elapsed = elapsed
		})
	case msg.err != nil:
		m.updateRecFooter(func(f *FooterModel) {
			f.Status = "failed"
			f.Detail = fmt.Sprintf("upload failed: %v", msg.err)
			f.Elapsed = elapsed
		})
	default:
		m.updateRecFooter(func(f *FooterModel) {
			f.Status = "done"
			f.Detail = fmt.Sprintf("submitted (%s)", msg.jobID)
			f.Elapsed = elapsed
		})
		key := msg.jobID
		m.jobs[key] = &JobPanel{
			ID: key, Kind: "transcribe-live", Status: "running",
			Start: time.Now(), SubmittedCmd: "/transcribe live",
		}
		m.activeJobID = key
		m.pushItem(ContentItem{Kind: "job", JobID: key})
		var cmd tea.Cmd
		if !m.polling[key] {
			m.polling[key] = true
			cmd = m.pollJobCmd(key)
		}
		m.syncViewport()
		return m, tea.Batch(cmd, m.ensureAnim())
	}
	m.syncViewport()
	return m, nil
}

// updateRecFooter mutates the live-recording footer item in place (port of
// App.tsx updateToolFooter).
func (m *Model) updateRecFooter(fn func(*FooterModel)) {
	if m.recFooterIdx < 0 || m.recFooterIdx >= len(m.items) {
		return
	}
	item := m.items[m.recFooterIdx]
	if item.Kind != "tool_footer" || item.Footer == nil {
		return
	}
	fn(item.Footer)
}

// ─── Menu & viewport sync ────────────────────────────────────────────────────

// refreshMenu recomputes slash-menu visibility from the input value (port of
// the App.tsx useEffect on inputValue).
func (m *Model) refreshMenu() {
	val := m.input.Value()
	hits := getSlashHits(val)
	m.menuVisible = strings.HasPrefix(val, "/") && len(hits) > 0
	limit := len(hits)
	if limit > slashMenuLimit {
		limit = slashMenuLimit
	}
	if m.menuSelected >= limit {
		m.menuSelected = 0
	}
	m.syncLayout()
	m.syncViewport()
}

func (m *Model) syncLayout() {
	_, mainW, innerW, _, viewportH := m.layout()
	m.vp.Width = mainW
	m.vp.Height = viewportH - 1 // one top padding/banner row rendered above
	if m.vp.Height < 1 {
		m.vp.Height = 1
	}
	m.input.Width = innerW - 3
	if m.input.Width < 5 {
		m.input.Width = 5
	}
}

// syncViewport rebuilds the viewport content, preserving stick-to-bottom.
func (m *Model) syncViewport() {
	atBottom := m.vp.AtBottom()
	m.vp.SetContent(m.renderContent())
	if atBottom {
		m.vp.GotoBottom()
	}
}

func (m *Model) renderContent() string {
	_, _, innerW, _, _ := m.layout()
	blocks := make([]string, 0, len(m.items))
	for _, it := range m.items {
		if rendered, ok := renderItem(it, m.jobs, innerW); ok {
			blocks = append(blocks, rendered)
		}
	}
	if len(blocks) == 0 {
		return ""
	}
	// marginBottom 1 between blocks, plus a trailing blank row like the
	// original (every item carried marginBottom).
	content := strings.Join(blocks, "\n\n") + "\n"
	return contentPadStyle.Render(content)
}

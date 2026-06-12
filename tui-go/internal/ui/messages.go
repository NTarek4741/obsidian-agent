package ui

import (
	"obsidian-agent/tui-go/internal/api"
)

// healthMsg carries the result of a GET /health probe.
type healthMsg struct {
	resp api.HealthResp
	err  error
}

// healthTickMsg re-arms the 3s health poll chain.
type healthTickMsg struct{}

// machinesMsg carries a GET /machines snapshot.
type machinesMsg struct {
	resp api.MachinesResp
	err  error
}

// machinesTickMsg re-arms the 5s machine poll chain.
type machinesTickMsg struct{}

// jobStatusMsg carries one GET /jobs/{id} poll result.
type jobStatusMsg struct {
	key  string
	resp api.JobStatusResp
	err  error
}

// jobPollTickMsg fires 2s after the last poll of a still-running job.
type jobPollTickMsg struct{ key string }

// animTickMsg drives all running animations (spinner, thinking dots, elapsed
// time). One ticker; frames derive from the wall clock.
type animTickMsg struct{}

// commandResultMsg carries dispatch results back into the update loop.
type commandResultMsg struct {
	submitted string // the raw command line, for job submittedCmd
	results   []cmdResult
}

// cmdResult mirrors the TS CommandResult union.
type cmdResult struct {
	typ    string // message | job_start | config | clear | help | quit | transcribe_live
	text   string
	kind   string
	realID string
}

// recorderStoppedMsg carries the outcome of stop-validate-submit for a live
// recording.
type recorderStoppedMsg struct {
	jobID   string
	elapsed int64 // ms
	err     error
	invalid bool // no audio captured
}

// configSavedMsg carries the POST /config outcome.
type configSavedMsg struct {
	ok    bool
	vault string
}

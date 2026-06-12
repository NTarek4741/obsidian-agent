package api

// HealthResp is the GET /health response.
type HealthResp struct {
	Status     string  `json:"status"`
	Configured bool    `json:"configured"`
	Vault      *string `json:"vault"`
}

// ConfigResp is the POST /config response.
type ConfigResp struct {
	Status string `json:"status"`
	Vault  string `json:"vault"`
}

// JobResp is returned by every job-starting endpoint.
type JobResp struct {
	JobID string `json:"job_id"`
}

// JobStatusResp is the GET /jobs/{id} polling response.
type JobStatusResp struct {
	JobID    string         `json:"job_id"`
	Kind     string         `json:"kind"`
	Status   string         `json:"status"` // pending | running | done | failed
	Progress []string       `json:"progress"`
	Result   map[string]any `json:"result"`
	Error    *string        `json:"error"`
}

// MachineSyncStats describes the last agent-folder sync onto a machine.
type MachineSyncStats struct {
	Files    int     `json:"files"`
	Uploaded int     `json:"uploaded"`
	Deleted  int     `json:"deleted"`
	TookS    float64 `json:"took_s"`
	At       float64 `json:"at"`
}

// MachineInfo is one machine snapshot in the GET /machines response.
type MachineInfo struct {
	Name        string            `json:"name"`
	Lifecycle   string            `json:"lifecycle"` // persistent | ephemeral
	Resources   string            `json:"resources"`
	Autosleep   string            `json:"autosleep"`
	MachineID   *string           `json:"machine_id"`
	Phase       string            `json:"phase"`
	LastEvent   *string           `json:"last_event"`
	LastEventTS *float64          `json:"last_event_ts"`
	WakeSeconds *float64          `json:"wake_seconds"`
	Sync        *MachineSyncStats `json:"sync"`
}

// MachineEvent is one lifecycle event in the GET /machines response.
type MachineEvent struct {
	TS      float64 `json:"ts"`
	Machine string  `json:"machine"`
	Event   string  `json:"event"`
}

// MachinesResp is the GET /machines response.
type MachinesResp struct {
	Machines []MachineInfo  `json:"machines"`
	Events   []MachineEvent `json:"events"`
}

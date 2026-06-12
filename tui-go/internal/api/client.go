// Package api is the HTTP client for the local FastAPI backend. Mirrors
// tui/src/api/client.ts: JSON in/out, no auth, errors as "HTTP {code}: {body}".
package api

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

type Client struct {
	BaseURL string
	// post handles job submissions and config saves; poll handles the cheap
	// health/jobs/machines GETs with a short timeout so a dead backend can't
	// wedge a tick chain.
	post *http.Client
	poll *http.Client
}

func NewClient(baseURL string) *Client {
	return &Client{
		BaseURL: strings.TrimRight(baseURL, "/"),
		post:    &http.Client{Timeout: 30 * time.Second},
		poll:    &http.Client{Timeout: 3 * time.Second},
	}
}

func decode[T any](resp *http.Response) (T, error) {
	var out T
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return out, err
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return out, fmt.Errorf("HTTP %d: %s", resp.StatusCode, string(body))
	}
	if err := json.Unmarshal(body, &out); err != nil {
		return out, err
	}
	return out, nil
}

func postJSON[T any](c *Client, path string, payload any) (T, error) {
	var out T
	body, err := json.Marshal(payload)
	if err != nil {
		return out, err
	}
	resp, err := c.post.Post(c.BaseURL+path, "application/json", bytes.NewReader(body))
	if err != nil {
		return out, err
	}
	return decode[T](resp)
}

func getJSON[T any](c *Client, path string) (T, error) {
	var out T
	resp, err := c.poll.Get(c.BaseURL + path)
	if err != nil {
		return out, err
	}
	return decode[T](resp)
}

func (c *Client) Health() (HealthResp, error) {
	return getJSON[HealthResp](c, "/health")
}

func (c *Client) SaveConfig(apiKey, vaultPath string) (ConfigResp, error) {
	return postJSON[ConfigResp](c, "/config", map[string]string{
		"api_key": apiKey, "vault_path": vaultPath,
	})
}

func (c *Client) StartPodcast(notePath string) (JobResp, error) {
	return postJSON[JobResp](c, "/podcast", map[string]string{"note_path": notePath})
}

func (c *Client) StartFlashcard(notePath string) (JobResp, error) {
	return postJSON[JobResp](c, "/flashcard", map[string]string{"note_path": notePath})
}

func (c *Client) StartDeepResearch(topic string) (JobResp, error) {
	return postJSON[JobResp](c, "/research/deep", map[string]string{"topic": topic})
}

func (c *Client) StartFastResearch(topic string) (JobResp, error) {
	return postJSON[JobResp](c, "/research/fast", map[string]string{"topic": topic})
}

func (c *Client) StartMindMap(notePath string) (JobResp, error) {
	return postJSON[JobResp](c, "/mind-map", map[string]string{"note_path": notePath})
}

func (c *Client) StartTranscribe(content string) (JobResp, error) {
	return postJSON[JobResp](c, "/transcribe", map[string]string{"content": content})
}

func (c *Client) StartChat(question string) (JobResp, error) {
	return postJSON[JobResp](c, "/chat", map[string]string{"question": question})
}

func (c *Client) GetMachines() (MachinesResp, error) {
	return getJSON[MachinesResp](c, "/machines")
}

func (c *Client) PollJob(jobID string) (JobStatusResp, error) {
	return getJSON[JobStatusResp](c, "/jobs/"+jobID)
}

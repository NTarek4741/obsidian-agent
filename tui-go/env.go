package main

import (
	"os"
	"path/filepath"
	"strings"
)

// findProjectRoot walks up ≤5 directories from cwd looking for a directory
// holding both .env and pyproject.toml — the repo root. Port of useEnv.ts.
func findProjectRoot() string {
	dir, err := os.Getwd()
	if err != nil {
		return "."
	}
	start := dir
	for i := 0; i < 5; i++ {
		_, envErr := os.Stat(filepath.Join(dir, ".env"))
		_, pyErr := os.Stat(filepath.Join(dir, "pyproject.toml"))
		if envErr == nil && pyErr == nil {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
		dir = parent
	}
	return start
}

// backendURL resolves the backend base URL: process env first, then the
// project .env file, then the default.
func backendURL() string {
	if v := strings.TrimSpace(os.Getenv("OBSIDIAN_AGENT_BACKEND_URL")); v != "" {
		return v
	}
	envPath := filepath.Join(findProjectRoot(), ".env")
	if data, err := os.ReadFile(envPath); err == nil {
		for _, line := range strings.Split(string(data), "\n") {
			line = strings.TrimSpace(line)
			if line == "" || strings.HasPrefix(line, "#") {
				continue
			}
			key, value, found := strings.Cut(line, "=")
			if !found || strings.TrimSpace(key) != "OBSIDIAN_AGENT_BACKEND_URL" {
				continue
			}
			value = strings.TrimSpace(value)
			value = strings.Trim(value, `"'`)
			if value != "" {
				return value
			}
		}
	}
	return "http://localhost:8000"
}

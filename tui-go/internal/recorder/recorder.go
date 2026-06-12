// Package recorder captures microphone audio with ffmpeg as 16 kHz mono PCM
// WAV — what the transcription backend prefers. Port of tui/src/api/liveRecorder.ts.
package recorder

import (
	"fmt"
	"math/rand"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"sync"
	"syscall"
	"time"
)

type Status string

const (
	StatusRecording Status = "recording"
	StatusStopped   Status = "stopped"
	StatusFailed    Status = "failed"
)

type Handle struct {
	FilePath string

	mu         sync.Mutex
	cmd        *exec.Cmd
	status     Status
	err        error
	stderrTail []byte
	done       chan struct{}
}

func findFFmpeg() (string, error) {
	if p, err := exec.LookPath("ffmpeg"); err == nil {
		return p, nil
	}
	for _, p := range []string{"/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"} {
		if _, err := os.Stat(p); err == nil {
			return p, nil
		}
	}
	return "", fmt.Errorf("ffmpeg not found — install it with `brew install ffmpeg`")
}

func buildArgs(filePath string) ([]string, error) {
	switch runtime.GOOS {
	case "darwin":
		return []string{
			"-hide_banner", "-loglevel", "error",
			"-y",
			"-f", "avfoundation",
			"-i", ":0", // default audio input
			"-ac", "1",
			"-ar", "16000",
			filePath,
		}, nil
	case "linux":
		return []string{
			"-hide_banner", "-loglevel", "error",
			"-y",
			"-f", "alsa",
			"-i", "default",
			"-ac", "1",
			"-ar", "16000",
			filePath,
		}, nil
	}
	return nil, fmt.Errorf("live recording is only supported on Linux and macOS")
}

// tailWriter keeps the last ~2-4KB of ffmpeg stderr for error reporting.
type tailWriter struct{ h *Handle }

func (w tailWriter) Write(p []byte) (int, error) {
	w.h.mu.Lock()
	defer w.h.mu.Unlock()
	w.h.stderrTail = append(w.h.stderrTail, p...)
	if len(w.h.stderrTail) > 4096 {
		w.h.stderrTail = w.h.stderrTail[len(w.h.stderrTail)-2048:]
	}
	return len(p), nil
}

func Start() (*Handle, error) {
	id := strconv.FormatInt(time.Now().UnixMilli(), 36) + "-" + strconv.FormatInt(rand.Int63n(1<<20), 36)
	filePath := filepath.Join(os.TempDir(), "obsidian-agent-recording-"+id+".wav")

	bin, err := findFFmpeg()
	if err != nil {
		return nil, err
	}
	args, err := buildArgs(filePath)
	if err != nil {
		return nil, err
	}

	h := &Handle{FilePath: filePath, status: StatusRecording, done: make(chan struct{})}
	cmd := exec.Command(bin, args...)
	cmd.Stderr = tailWriter{h}
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	h.cmd = cmd

	go func() {
		err := cmd.Wait()
		h.mu.Lock()
		if h.status == StatusRecording {
			h.status = StatusStopped
		}
		if err != nil && h.status != StatusStopped {
			h.err = fmt.Errorf("ffmpeg exited: %v: %s", err, string(h.stderrTail))
			h.status = StatusFailed
		}
		h.mu.Unlock()
		close(h.done)
	}()

	return h, nil
}

// Stop interrupts ffmpeg (SIGINT finalizes the WAV header), escalating to
// SIGKILL after 2s, and waits for the process to exit.
func (h *Handle) Stop() error {
	h.mu.Lock()
	if h.status != StatusRecording {
		h.mu.Unlock()
		return nil
	}
	h.status = StatusStopped
	cmd := h.cmd
	h.mu.Unlock()

	_ = cmd.Process.Signal(syscall.SIGINT)
	escalate := time.AfterFunc(2*time.Second, func() { _ = cmd.Process.Kill() })
	<-h.done
	escalate.Stop()
	return nil
}

// Cancel kills ffmpeg and removes the partial file.
func (h *Handle) Cancel() {
	h.mu.Lock()
	cmd := h.cmd
	h.status = StatusStopped
	h.mu.Unlock()
	_ = cmd.Process.Kill()
	_ = os.Remove(h.FilePath)
}

// FileLooksValid reports whether the recording captured real audio (the WAV
// header alone is 44 bytes; anything ≤1KB is silence or a failed start).
func FileLooksValid(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.Size() > 1024
}

// Cleanup removes the recording after the backend has had time to read it.
func Cleanup(path string, delay time.Duration) {
	time.AfterFunc(delay, func() { _ = os.Remove(path) })
}

import shutil
import subprocess
import sys
from pathlib import Path


def ensure_tui_binary(root: Path) -> Path:
    """Build the Go TUI if the binary is missing or any source file is newer."""
    src_dir = (root / "tui-go").resolve()
    binary = src_dir / "obsidian-tui"

    sources = list(src_dir.rglob("*.go")) + [src_dir / "go.mod", src_dir / "go.sum"]
    stale = not binary.exists() or any(
        f.exists() and f.stat().st_mtime > binary.stat().st_mtime for f in sources
    )
    if stale:
        go = shutil.which("go") or "/opt/homebrew/bin/go"
        print("Building TUI...")
        subprocess.run([go, "build", "-o", str(binary), "."], cwd=src_dir, check=True)
        # `go build` skips rewriting an unchanged binary, leaving a stale
        # mtime; bump it so the staleness check passes next launch.
        binary.touch()
    return binary


def main() -> None:
    root = Path(__file__).parent
    tui_binary = ensure_tui_binary(root)

    backend = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.app:app", "--port", "8000", "--log-level", "error"],
        cwd=root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        subprocess.run([str(tui_binary)], cwd=root)
    finally:
        backend.terminate()
        backend.wait()


if __name__ == "__main__":
    main()

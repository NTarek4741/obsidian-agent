import subprocess
import sys
from pathlib import Path


def main() -> None:
    root = Path(__file__).parent
    tui_dir = root / "tui"

    backend = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.app:app", "--port", "8000", "--log-level", "error"],
        cwd=root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        subprocess.run(["node", "dist/index.js"], cwd=tui_dir)
    finally:
        backend.terminate()
        backend.wait()


if __name__ == "__main__":
    main()

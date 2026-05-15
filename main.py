import asyncio
import sys

from obsidian_agent.tui import run_tui


def main():
    try:
        asyncio.run(run_tui())
    except KeyboardInterrupt:
        print("\nInterrupted. Goodbye.")
        sys.exit(0)


if __name__ == "__main__":
    main()

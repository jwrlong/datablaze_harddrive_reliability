"""Download, collapse, and write the memo and dashboard."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.analyze import main as analyze_main
from analysis.download_data import main as download_main
from analysis.process_data import main as process_main


def main() -> None:
    download_main()
    process_main()
    analyze_main()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 — top-level CLI boundary
        print(f"run failed: {exc}", file=sys.stderr)
        sys.exit(1)

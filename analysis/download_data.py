"""Download Backblaze quarterly zip files into data/raw.

A file is skipped when its local size matches the remote Content-Length.
The zips stay on disk for a rebuild and are gitignored.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.assumptions import DATA_RAW, QUARTERS, ZIP_BASE, ZIP_NAMES


def remote_size(url: str) -> int:
    """Return Content-Length from a HEAD request."""
    result = subprocess.run(
        ["curl", "-fsSIL", url],
        check=True,
        capture_output=True,
        text=True,
    )
    length = None
    # Redirects repeat headers. The last content-length is the file.
    for line in result.stdout.splitlines():
        if line.lower().startswith("content-length:"):
            length = int(line.split(":", 1)[1].strip())
    if length is None:
        raise RuntimeError(f"No Content-Length for {url}")
    return length


def download_quarter(quarter: str) -> None:
    """Resume one quarterly zip until it matches the remote size."""
    name = ZIP_NAMES[quarter]
    url = f"{ZIP_BASE}/{name}"
    dest = DATA_RAW / name
    DATA_RAW.mkdir(parents=True, exist_ok=True)
    expected = remote_size(url)
    if dest.exists() and dest.stat().st_size == expected:
        print(f"{quarter}: already complete ({expected} bytes)", flush=True)
        return
    print(f"{quarter}: downloading {expected} bytes", flush=True)
    subprocess.run(
        ["curl", "-fL", "--retry", "5", "--retry-delay", "2", "-C", "-", "-o", str(dest), url],
        check=True,
    )
    actual = dest.stat().st_size
    if actual != expected:
        raise RuntimeError(f"{name} is {actual} bytes, expected {expected}")
    print(f"{quarter}: complete", flush=True)


def main() -> None:
    for quarter in QUARTERS:
        download_quarter(quarter)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 — top-level CLI boundary
        print(f"download failed: {exc}", file=sys.stderr)
        sys.exit(1)

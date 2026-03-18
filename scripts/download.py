#!/usr/bin/env python3
"""Download ASTRA vehicle registration data with per-file freshness checking."""

import os
import sys
import time
import requests
from pathlib import Path
from email.utils import formatdate, parsedate_to_datetime

# Soft timeout: stop starting new downloads after this many seconds.
# Leaves headroom for the cache save steps before the workflow hard-kills at 30 min.
TIMEOUT_SECONDS = int(os.environ.get("DOWNLOAD_TIMEOUT", 0))  # 0 = no limit

BASE_URL = "https://opendata.astra.admin.ch/ivzod/1000-Fahrzeuge_IVZ/1200-Neuzulassungen/1210-Datensaetze_monatlich"
CURRENT_URL = f"{BASE_URL}/NEUZU.txt"
ARCHIVE_URL = f"{BASE_URL}/1213-Vorjahresdaten/NEUZU-{{year}}.txt"

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
ARCHIVE_YEARS = range(2016, 2026)


def download_file(url: str, dest: Path, force: bool = False) -> bool:
    """Download only if remote is newer than local. Returns True if file was updated."""
    if dest.exists() and not force:
        local_mtime = dest.stat().st_mtime
        headers = {"If-Modified-Since": formatdate(local_mtime, usegmt=True)}

        print(f"  Checking freshness: {dest.name}", flush=True)
        resp = requests.head(url, headers=headers, timeout=30, allow_redirects=True)

        if resp.status_code == 304:
            print(f"  Up to date (cached): {dest.name}", flush=True)
            return False
        elif resp.status_code == 200:
            print(f"  Newer version available: {dest.name}", flush=True)
        else:
            print(f"  HEAD failed ({resp.status_code}) -> downloading anyway", flush=True)

    # Perform download
    print(f"  Downloading: {url}", flush=True)
    try:
        resp = requests.get(url, stream=True, timeout=120)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  ERROR: {e}", flush=True)
        return False

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    total = int(resp.headers.get("content-length", 0))
    downloaded = 0
    with open(tmp, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)
            downloaded += len(chunk)
            if total:
                print(f"  {dest.name}: {downloaded / (1024*1024):.0f} / {total / (1024*1024):.0f} MB", flush=True)
            else:
                print(f"  {dest.name}: {downloaded / (1024*1024):.0f} MB downloaded", flush=True)

    # Atomic rename — partial downloads from killed processes won't poison the cache
    tmp.rename(dest)

    # Sync local mtime to server's Last-Modified (makes future checks accurate)
    if "Last-Modified" in resp.headers:
        try:
            server_dt = parsedate_to_datetime(resp.headers["Last-Modified"])
            server_mtime = server_dt.timestamp()
            os.utime(dest, (server_mtime, server_mtime))
        except Exception:
            pass

    size_mb = dest.stat().st_size / (1024 * 1024)
    print(f"  Saved: {dest.name} ({size_mb:.1f} MB)", flush=True)
    return True


def main():
    force = "--force" in sys.argv
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    # Clean up partial downloads from previous interrupted runs
    for tmp in RAW_DIR.glob("*.tmp"):
        print(f"  Removing partial download: {tmp.name}", flush=True)
        tmp.unlink()
    start = time.monotonic()

    print("=== ASTRA NEUZU Data Download (per-file invalidation) ===\n", flush=True)
    if TIMEOUT_SECONDS:
        print(f"  Soft timeout: {TIMEOUT_SECONDS}s\n", flush=True)

    updated_any = False
    timed_out = False

    def time_left() -> bool:
        if not TIMEOUT_SECONDS:
            return True
        return (time.monotonic() - start) < TIMEOUT_SECONDS

    # Current month (always check freshness)
    print("Current year:", flush=True)
    if download_file(CURRENT_URL, RAW_DIR / "NEUZU.txt", force):
        updated_any = True

    # All archive years (check freshness via If-Modified-Since)
    print("\nArchive years:", flush=True)
    for year in ARCHIVE_YEARS:
        if not time_left():
            print(f"\n  Soft timeout reached after {time.monotonic() - start:.0f}s, stopping downloads.", flush=True)
            timed_out = True
            break
        url = ARCHIVE_URL.format(year=year)
        dest = RAW_DIR / f"NEUZU-{year}.txt"
        if download_file(url, dest, force):
            updated_any = True

    elapsed = time.monotonic() - start
    print(f"\nDone in {elapsed:.0f}s. Data changed: {updated_any}", flush=True)
    if timed_out:
        print("  (partial download — remaining files will be fetched on next run)", flush=True)


if __name__ == "__main__":
    main()

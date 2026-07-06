#!/usr/bin/env python3
"""Download ASTRA vehicle registration data with per-file freshness checking."""

import os
import random
import sys
import time
import requests
from pathlib import Path
from email.utils import formatdate, parsedate_to_datetime

# Soft timeout: stop starting new downloads after this many seconds.
# Leaves headroom for the cache save steps before the workflow hard-kills at 30 min.
TIMEOUT_SECONDS = int(os.environ.get("DOWNLOAD_TIMEOUT", 0))  # 0 = no limit
MIN_HEADROOM_SECONDS = int(os.environ.get("DOWNLOAD_MIN_HEADROOM", 300))
REQUEST_ATTEMPTS = int(os.environ.get("DOWNLOAD_REQUEST_ATTEMPTS", 4))
RETRY_BACKOFF_SECONDS = tuple(
    float(value) for value in os.environ.get("DOWNLOAD_RETRY_BACKOFF", "2,4,8,16").split(",")
)
RETRY_JITTER_SECONDS = float(os.environ.get("DOWNLOAD_RETRY_JITTER", 0.5))
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}

BASE_URL = "https://opendata.astra.admin.ch/ivzod/1000-Fahrzeuge_IVZ/1200-Neuzulassungen/1210-Datensaetze_monatlich"
CURRENT_URL = f"{BASE_URL}/NEUZU.txt"
ARCHIVE_URL = f"{BASE_URL}/1213-Vorjahresdaten/NEUZU-{{year}}.txt"

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
ARCHIVE_YEARS = range(2016, __import__("datetime").date.today().year)


class RetryableStatus(Exception):
    """HTTP status that should be retried."""


def retry_delay(attempt: int) -> float:
    """Return retry delay before the next attempt."""
    backoff = RETRY_BACKOFF_SECONDS or (0.0,)
    delay = backoff[min(attempt - 1, len(backoff) - 1)]
    if RETRY_JITTER_SECONDS:
        delay += random.uniform(0, RETRY_JITTER_SECONDS)
    return delay


def sleep_before_retry(action: str, attempt: int, error: Exception) -> None:
    delay = retry_delay(attempt)
    print(
        f"  {action} failed ({error}) -> retrying in {delay:.1f}s "
        f"({attempt + 1}/{REQUEST_ATTEMPTS})",
        flush=True,
    )
    time.sleep(delay)


def request_once(method: str, url: str, **kwargs) -> requests.Response:
    resp = getattr(requests, method.lower())(url, **kwargs)
    if resp.status_code in RETRY_STATUS_CODES:
        close = getattr(resp, "close", None)
        if close:
            close()
        raise RetryableStatus(f"{method} returned HTTP {resp.status_code}")
    return resp


def head_with_retries(url: str, headers: dict[str, str]) -> requests.Response:
    for attempt in range(1, REQUEST_ATTEMPTS + 1):
        try:
            return request_once("HEAD", url, headers=headers, timeout=30, allow_redirects=True)
        except (requests.RequestException, RetryableStatus) as e:
            if attempt == REQUEST_ATTEMPTS:
                raise
            sleep_before_retry("HEAD", attempt, e)
    raise RuntimeError("unreachable")


def download_file(url: str, dest: Path, force: bool = False) -> bool | None:
    """Download only if remote is newer than local.

    Returns True if file was updated, False if unchanged, and None if the file
    could not be verified/downloaded and the overall run should stay partial.
    """
    if dest.exists() and not force:
        local_mtime = dest.stat().st_mtime
        headers = {"If-Modified-Since": formatdate(local_mtime, usegmt=True)}

        print(f"  Checking freshness: {dest.name}", flush=True)
        try:
            resp = head_with_retries(url, headers)
        except (requests.RequestException, RetryableStatus) as e:
            print(f"  HEAD failed ({e}) -> downloading anyway", flush=True)
        else:
            if resp.status_code == 304:
                print(f"  Up to date (cached): {dest.name}", flush=True)
                return False
            elif resp.status_code == 200:
                print(f"  Newer version available: {dest.name}", flush=True)
            else:
                print(f"  HEAD failed ({resp.status_code}) -> downloading anyway", flush=True)

    # Perform download. Write to .tmp and rename only after a complete response.
    print(f"  Downloading: {url}", flush=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")

    for attempt in range(1, REQUEST_ATTEMPTS + 1):
        if tmp.exists():
            tmp.unlink()
        try:
            resp = request_once("GET", url, stream=True, timeout=120)
            resp.raise_for_status()

            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        print(
                            f"  {dest.name}: {downloaded / (1024*1024):.0f} / {total / (1024*1024):.0f} MB",
                            flush=True,
                        )
                    else:
                        print(f"  {dest.name}: {downloaded / (1024*1024):.0f} MB downloaded", flush=True)

            # Atomic rename — partial downloads from killed processes won't poison the cache
            tmp.rename(dest)
            break
        except requests.HTTPError as e:
            if tmp.exists():
                tmp.unlink()
            print(f"  ERROR: {e}", flush=True)
            return None
        except (OSError, requests.RequestException, RetryableStatus) as e:
            if tmp.exists():
                tmp.unlink()
            if attempt == REQUEST_ATTEMPTS:
                print(f"  ERROR: {e}", flush=True)
                return None
            sleep_before_retry("GET", attempt, e)

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
    partial = False
    timed_out = False

    def time_left(min_headroom: int = 0) -> bool:
        if not TIMEOUT_SECONDS:
            return True
        return (TIMEOUT_SECONDS - (time.monotonic() - start)) > min_headroom

    def record_result(result: bool | None) -> None:
        nonlocal updated_any, partial
        if result is None:
            partial = True
        elif result:
            updated_any = True

    # Current month (always check freshness)
    print("Current year:", flush=True)
    record_result(download_file(CURRENT_URL, RAW_DIR / "NEUZU.txt", force))

    # All archive years (check freshness via If-Modified-Since)
    print("\nArchive years:", flush=True)
    for year in ARCHIVE_YEARS:
        if not time_left(MIN_HEADROOM_SECONDS):
            print(f"\n  Soft timeout reached after {time.monotonic() - start:.0f}s, stopping downloads.", flush=True)
            timed_out = True
            partial = True
            break
        url = ARCHIVE_URL.format(year=year)
        dest = RAW_DIR / f"NEUZU-{year}.txt"
        record_result(download_file(url, dest, force))

    elapsed = time.monotonic() - start
    print(f"\nDone in {elapsed:.0f}s. Data changed: {updated_any}", flush=True)
    if partial:
        print("  (partial download — remaining files will be fetched on next run)", flush=True)
    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        with open(gh_output, "a") as f:
            f.write(f"partial={str(partial).lower()}\n")
            f.write(f"complete={str(not partial).lower()}\n")
            f.write(f"timed_out={str(timed_out).lower()}\n")


if __name__ == "__main__":
    main()

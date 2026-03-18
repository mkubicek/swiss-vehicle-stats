"""Tests for scripts/download.py — targets 100% line coverage."""

import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

import download


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(
    status_code=200,
    headers=None,
    chunks=None,
    raise_for_status=None,
):
    """Build a fake requests.Response-like object."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}
    resp.iter_content = MagicMock(return_value=iter(chunks or [b"data"]))
    if raise_for_status:
        resp.raise_for_status.side_effect = raise_for_status
    else:
        resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# download_file – freshness / cache branches
# ---------------------------------------------------------------------------

class TestDownloadFileExistsNotForce:
    """File exists + force=False → HEAD-based freshness check."""

    def test_head_304_up_to_date(self, tmp_path):
        """HEAD 304 → file considered up-to-date, no download."""
        dest = tmp_path / "NEUZU.txt"
        dest.write_text("old")

        head_resp = _mock_response(status_code=304)

        with patch.object(requests, "head", return_value=head_resp) as mock_head:
            result = download.download_file("http://x/NEUZU.txt", dest, force=False)

        assert result is False
        mock_head.assert_called_once()
        assert dest.read_text() == "old"  # file unchanged

    def test_head_200_newer_available(self, tmp_path):
        """HEAD 200 → newer version on server → download happens."""
        dest = tmp_path / "NEUZU.txt"
        dest.write_text("old")

        head_resp = _mock_response(status_code=200)
        get_resp = _mock_response(
            headers={"content-length": "4"},
            chunks=[b"new!"],
        )

        with patch.object(requests, "head", return_value=head_resp), \
             patch.object(requests, "get", return_value=get_resp):
            result = download.download_file("http://x/NEUZU.txt", dest, force=False)

        assert result is True
        assert dest.read_bytes() == b"new!"

    def test_head_other_status_downloads_anyway(self, tmp_path):
        """HEAD returns unexpected status (e.g. 403) → download anyway."""
        dest = tmp_path / "NEUZU.txt"
        dest.write_text("old")

        head_resp = _mock_response(status_code=403)
        get_resp = _mock_response(
            headers={"content-length": "5"},
            chunks=[b"fresh"],
        )

        with patch.object(requests, "head", return_value=head_resp), \
             patch.object(requests, "get", return_value=get_resp):
            result = download.download_file("http://x/NEUZU.txt", dest, force=False)

        assert result is True
        assert dest.read_bytes() == b"fresh"


class TestDownloadFileDoesNotExist:
    """File does not exist → skip HEAD, go straight to download."""

    def test_downloads_directly(self, tmp_path):
        dest = tmp_path / "NEUZU.txt"
        get_resp = _mock_response(
            headers={"content-length": "7"},
            chunks=[b"payload"],
        )

        with patch.object(requests, "head") as mock_head, \
             patch.object(requests, "get", return_value=get_resp):
            result = download.download_file("http://x/NEUZU.txt", dest, force=False)

        assert result is True
        mock_head.assert_not_called()
        assert dest.read_bytes() == b"payload"


class TestDownloadFileForce:
    """force=True → skip freshness check even if file exists."""

    def test_force_skips_head(self, tmp_path):
        dest = tmp_path / "NEUZU.txt"
        dest.write_text("old")

        get_resp = _mock_response(
            headers={"content-length": "6"},
            chunks=[b"forced"],
        )

        with patch.object(requests, "head") as mock_head, \
             patch.object(requests, "get", return_value=get_resp):
            result = download.download_file("http://x/NEUZU.txt", dest, force=True)

        assert result is True
        mock_head.assert_not_called()
        assert dest.read_bytes() == b"forced"


# ---------------------------------------------------------------------------
# download_file – download failure
# ---------------------------------------------------------------------------

class TestDownloadFileFailure:
    """requests.get raises RequestException → returns False."""

    def test_request_exception_returns_false(self, tmp_path):
        dest = tmp_path / "NEUZU.txt"

        with patch.object(
            requests, "get", side_effect=requests.RequestException("timeout")
        ):
            result = download.download_file("http://x/NEUZU.txt", dest, force=True)

        assert result is False
        assert not dest.exists()

    def test_raise_for_status_exception(self, tmp_path):
        """raise_for_status() triggers RequestException → returns False."""
        dest = tmp_path / "NEUZU.txt"

        get_resp = _mock_response(
            raise_for_status=requests.RequestException("500 Server Error"),
        )

        with patch.object(requests, "get", return_value=get_resp):
            result = download.download_file("http://x/NEUZU.txt", dest, force=True)

        assert result is False


# ---------------------------------------------------------------------------
# download_file – progress reporting branches
# ---------------------------------------------------------------------------

class TestDownloadFileProgress:
    """Cover both progress-reporting branches (with and without content-length)."""

    def test_progress_with_content_length(self, tmp_path):
        """content-length present → shows 'X / Y MB'."""
        dest = tmp_path / "NEUZU.txt"
        get_resp = _mock_response(
            headers={"content-length": "2048"},
            chunks=[b"a" * 1024, b"b" * 1024],
        )

        with patch.object(requests, "get", return_value=get_resp):
            result = download.download_file("http://x/NEUZU.txt", dest, force=True)

        assert result is True
        assert dest.stat().st_size == 2048

    def test_progress_without_content_length(self, tmp_path):
        """No content-length → shows 'X MB downloaded'."""
        dest = tmp_path / "NEUZU.txt"
        get_resp = _mock_response(
            headers={},  # no content-length
            chunks=[b"abc"],
        )

        with patch.object(requests, "get", return_value=get_resp):
            result = download.download_file("http://x/NEUZU.txt", dest, force=True)

        assert result is True
        assert dest.read_bytes() == b"abc"


# ---------------------------------------------------------------------------
# download_file – Last-Modified mtime syncing
# ---------------------------------------------------------------------------

class TestDownloadFileMtime:
    """Cover Last-Modified header handling."""

    def test_last_modified_syncs_mtime(self, tmp_path):
        """Valid Last-Modified → local mtime updated."""
        dest = tmp_path / "NEUZU.txt"
        get_resp = _mock_response(
            headers={
                "content-length": "5",
                "Last-Modified": "Wed, 01 Jan 2025 12:00:00 GMT",
            },
            chunks=[b"hello"],
        )

        with patch.object(requests, "get", return_value=get_resp):
            download.download_file("http://x/NEUZU.txt", dest, force=True)

        # mtime should be set to 2025-01-01 12:00:00 UTC
        from email.utils import parsedate_to_datetime
        expected_ts = parsedate_to_datetime("Wed, 01 Jan 2025 12:00:00 GMT").timestamp()
        assert abs(dest.stat().st_mtime - expected_ts) < 2

    def test_no_last_modified_skips_mtime(self, tmp_path):
        """No Last-Modified → mtime not touched (no crash)."""
        dest = tmp_path / "NEUZU.txt"
        get_resp = _mock_response(
            headers={"content-length": "5"},
            chunks=[b"hello"],
        )

        with patch.object(requests, "get", return_value=get_resp):
            result = download.download_file("http://x/NEUZU.txt", dest, force=True)

        assert result is True

    def test_last_modified_parse_failure(self, tmp_path):
        """Malformed Last-Modified → exception caught silently."""
        dest = tmp_path / "NEUZU.txt"
        get_resp = _mock_response(
            headers={
                "content-length": "5",
                "Last-Modified": "not-a-real-date",
            },
            chunks=[b"hello"],
        )

        with patch.object(requests, "get", return_value=get_resp):
            result = download.download_file("http://x/NEUZU.txt", dest, force=True)

        assert result is True  # no crash


# ---------------------------------------------------------------------------
# main() – orchestrator
# ---------------------------------------------------------------------------

def _always_false(*args, **kwargs):
    """Stub download_file that always returns False."""
    return False


def _always_true(*args, **kwargs):
    """Stub download_file that always returns True."""
    return True


class TestMain:
    """Cover main() branches: force flag, timeout, tmp cleanup."""

    def test_normal_run_no_force(self, tmp_path, monkeypatch):
        """Normal run, no --force, no timeout, files up-to-date."""
        monkeypatch.setattr(download, "RAW_DIR", tmp_path)
        monkeypatch.setattr(download, "TIMEOUT_SECONDS", 0)
        monkeypatch.setattr(download, "ARCHIVE_YEARS", range(2024, 2025))
        monkeypatch.setattr("sys.argv", ["download.py"])

        with patch.object(download, "download_file", side_effect=_always_false):
            download.main()

    def test_force_flag(self, tmp_path, monkeypatch):
        """--force in sys.argv → force=True passed to download_file."""
        monkeypatch.setattr(download, "RAW_DIR", tmp_path)
        monkeypatch.setattr(download, "TIMEOUT_SECONDS", 0)
        monkeypatch.setattr(download, "ARCHIVE_YEARS", range(2024, 2025))
        monkeypatch.setattr("sys.argv", ["download.py", "--force"])

        calls = []

        def capture_call(url, dest, force=False):
            calls.append(force)
            return False

        with patch.object(download, "download_file", side_effect=capture_call):
            download.main()

        # Both the current-year call and the archive call should have force=True
        assert all(calls), f"Expected all force=True, got {calls}"

    def test_updated_any_true(self, tmp_path, monkeypatch):
        """When download_file returns True, updated_any is set."""
        monkeypatch.setattr(download, "RAW_DIR", tmp_path)
        monkeypatch.setattr(download, "TIMEOUT_SECONDS", 0)
        monkeypatch.setattr(download, "ARCHIVE_YEARS", range(2024, 2025))
        monkeypatch.setattr("sys.argv", ["download.py"])

        with patch.object(download, "download_file", side_effect=_always_true):
            download.main()  # no crash; "Data changed: True" printed

    def test_tmp_file_cleanup(self, tmp_path, monkeypatch):
        """.tmp files from previous runs are cleaned up."""
        monkeypatch.setattr(download, "RAW_DIR", tmp_path)
        monkeypatch.setattr(download, "TIMEOUT_SECONDS", 0)
        monkeypatch.setattr(download, "ARCHIVE_YEARS", range(2024, 2025))
        monkeypatch.setattr("sys.argv", ["download.py"])

        leftover = tmp_path / "NEUZU-2023.tmp"
        leftover.write_text("partial")

        with patch.object(download, "download_file", side_effect=_always_false):
            download.main()

        assert not leftover.exists()

    def test_soft_timeout_reached(self, tmp_path, monkeypatch):
        """TIMEOUT_SECONDS > 0 and time exhausted → stops early."""
        monkeypatch.setattr(download, "RAW_DIR", tmp_path)
        monkeypatch.setattr(download, "TIMEOUT_SECONDS", 1)
        # Use multiple archive years so timeout has a chance to trigger
        monkeypatch.setattr(download, "ARCHIVE_YEARS", range(2020, 2025))
        monkeypatch.setattr("sys.argv", ["download.py"])

        call_count = 0

        def slow_download(url, dest, force=False):
            nonlocal call_count
            call_count += 1
            # On the first archive call, simulate enough elapsed time
            # to make time_left() return False on the *next* iteration.
            if call_count == 1:
                # Don't actually sleep; instead advance the monotonic clock
                # by patching time.monotonic.
                pass
            return False

        # We need the first call to time.monotonic (inside main) to return 0,
        # and subsequent calls to return a value > TIMEOUT_SECONDS.
        clock_values = iter([0.0, 0.0, 0.5, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0])

        with patch.object(download, "download_file", side_effect=slow_download), \
             patch.object(time, "monotonic", side_effect=lambda: next(clock_values)):
            download.main()

        # Should have stopped before processing all 5 archive years
        # call_count = 1 (current) + however many archives before timeout
        assert call_count < 6  # less than 1 current + 5 archives

    def test_soft_timeout_not_reached(self, tmp_path, monkeypatch):
        """TIMEOUT_SECONDS > 0 but time not exhausted → all files processed."""
        monkeypatch.setattr(download, "RAW_DIR", tmp_path)
        monkeypatch.setattr(download, "TIMEOUT_SECONDS", 9999)
        monkeypatch.setattr(download, "ARCHIVE_YEARS", range(2024, 2025))
        monkeypatch.setattr("sys.argv", ["download.py"])

        call_count = 0

        def count_downloads(url, dest, force=False):
            nonlocal call_count
            call_count += 1
            return False

        with patch.object(download, "download_file", side_effect=count_downloads):
            download.main()

        # 1 current + 1 archive
        assert call_count == 2


# ---------------------------------------------------------------------------
# download_file – dest parent directory creation
# ---------------------------------------------------------------------------

class TestDownloadFileCreatesParent:
    """Ensure dest.parent.mkdir(parents=True) works for nested paths."""

    def test_creates_nested_parent_dirs(self, tmp_path):
        dest = tmp_path / "sub" / "dir" / "NEUZU.txt"
        get_resp = _mock_response(chunks=[b"data"])

        with patch.object(requests, "get", return_value=get_resp):
            result = download.download_file("http://x/NEUZU.txt", dest, force=True)

        assert result is True
        assert dest.exists()


# ---------------------------------------------------------------------------
# __name__ == "__main__" guard
# ---------------------------------------------------------------------------

class TestMainGuard:
    """Cover the if __name__ == '__main__': main() line (124)."""

    def test_main_guard(self, tmp_path, monkeypatch):
        """Run the script via runpy so the __main__ guard executes."""
        monkeypatch.setenv("DOWNLOAD_TIMEOUT", "0")
        monkeypatch.setattr("sys.argv", ["download.py"])

        head_resp = _mock_response(status_code=304)
        get_resp = _mock_response(chunks=[b"x"])

        # Mock at the requests module level so the fresh namespace sees it.
        with patch("requests.head", return_value=head_resp), \
             patch("requests.get", return_value=get_resp):
            import runpy

            # Build init_globals that override RAW_DIR and ARCHIVE_YEARS
            # so the fresh main() uses tmp_path and minimal iteration.
            runpy.run_path(
                download.__file__,
                init_globals={
                    "RAW_DIR": tmp_path,
                    "ARCHIVE_YEARS": range(2024, 2025),
                },
                run_name="__main__",
            )

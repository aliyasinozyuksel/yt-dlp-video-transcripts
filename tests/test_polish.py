from __future__ import annotations

import sys

import pytest

from tests.conftest import ct


class RetryableError(Exception):
    pass


def test_is_retryable_error_rejects_programming_errors():
    assert ct.is_retryable_error(TypeError("bad type")) is False
    assert ct.is_retryable_error(ValueError("bad value")) is False
    assert ct.is_retryable_error(KeyError("missing")) is False
    assert ct.is_retryable_error(AssertionError("assert")) is False


def test_is_retryable_error_accepts_transient_errors():
    assert ct.is_retryable_error(RetryableError("network")) is True
    assert ct.is_retryable_error(OSError("connection reset")) is True


def test_retry_call_retries_retryable_exception(monkeypatch):
    calls = {"count": 0}

    def flaky() -> str:
        calls["count"] += 1
        if calls["count"] < 2:
            raise RetryableError("temporary")
        return "ok"

    monkeypatch.setattr(ct.time, "sleep", lambda _delay: None)
    assert ct.retry_call(flaky, attempts=3, delay=0, action="Test action") == "ok"
    assert calls["count"] == 2


def test_retry_call_does_not_retry_type_error(monkeypatch):
    calls = {"count": 0}

    def broken() -> None:
        calls["count"] += 1
        raise TypeError("programming bug")

    monkeypatch.setattr(ct.time, "sleep", lambda _delay: None)
    with pytest.raises(TypeError):
        ct.retry_call(broken, attempts=3, delay=0, action="Test action")
    assert calls["count"] == 1


def test_retry_call_does_not_retry_value_error(monkeypatch):
    calls = {"count": 0}

    def broken() -> None:
        calls["count"] += 1
        raise ValueError("bad input")

    monkeypatch.setattr(ct.time, "sleep", lambda _delay: None)
    with pytest.raises(ValueError):
        ct.retry_call(broken, attempts=3, delay=0, action="Test action")
    assert calls["count"] == 1


def test_retry_call_retry_error_includes_action(monkeypatch):
    def always_fail() -> None:
        raise RetryableError("still failing")

    monkeypatch.setattr(ct.time, "sleep", lambda _delay: None)
    with pytest.raises(ct.RetryError, match="Fetching metadata failed after 2 attempt"):
        ct.retry_call(always_fail, attempts=2, delay=0, action="Fetching metadata")


def test_main_rejects_max_videos_with_start_index(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "channel_transcripts.py",
            "https://www.youtube.com/@test/videos",
            "--max-videos",
            "5",
            "--start-index",
            "1",
        ],
    )
    with pytest.raises(SystemExit) as exc_info:
        ct.main()
    assert exc_info.value.code != 0


def test_main_rejects_max_videos_with_end_index(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "channel_transcripts.py",
            "https://www.youtube.com/@test/videos",
            "--max-videos",
            "5",
            "--end-index",
            "10",
        ],
    )
    with pytest.raises(SystemExit) as exc_info:
        ct.main()
    assert exc_info.value.code != 0


def test_md_heading_text_newline_collapsed():
    assert ct.md_heading_text("Line one\nLine two") == "Line one Line two"


def test_md_heading_text_script_neutralized():
    assert "&lt;script&gt;" in ct.md_heading_text("<script>alert(1)</script>")
    assert "<script>" not in ct.md_heading_text("<script>alert(1)</script>")


def test_md_heading_text_normal_title_readable():
    assert ct.md_heading_text("How (and why) to take a logarithm") == (
        "How (and why) to take a logarithm"
    )


def test_md_heading_text_empty_defaults_to_untitled():
    assert ct.md_heading_text("") == "Untitled"
    assert ct.md_heading_text("   \n\t  ") == "Untitled"


def test_build_md_content_uses_safe_heading():
    content = ct.build_md_content(
        title="Bad\nTitle <b>bold</b>",
        url="https://example.com",
        upload_date="2024-01-01",
        channel="Channel",
        video_id="vid1",
        transcript="Hello",
    )
    lines = content.splitlines()
    heading_lines = [line for line in lines if line.startswith("# ")]
    assert len(heading_lines) == 1
    assert heading_lines[0] == "# Bad Title &lt;b&gt;bold&lt;/b&gt;"

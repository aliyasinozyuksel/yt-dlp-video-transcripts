from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import ct
from tests.test_disk_and_resume import write_pair


@pytest.fixture
def sample_videos() -> list[dict]:
    return [
        {
            "id": "vid1",
            "title": "New Video",
            "url": "https://www.youtube.com/watch?v=vid1",
            "upload_date": "20240101",
        },
        {
            "id": "vid2",
            "title": "Existing Video",
            "url": "https://www.youtube.com/watch?v=vid2",
            "upload_date": "20240102",
        },
        {
            "id": "vid3",
            "title": "Partial Video",
            "url": "https://www.youtube.com/watch?v=vid3",
            "upload_date": "20240103",
        },
    ]


def setup_existing_files(base: Path) -> None:
    write_pair(base, index=2, video_id="vid2", title="Existing Video")
    write_pair(
        base,
        index=3,
        video_id="vid3",
        title="Partial Video",
        include_md=True,
        include_txt=False,
    )


def list_output_files(base: Path) -> set[Path]:
    if not base.exists():
        return set()
    return {path for path in base.rglob("*") if path.is_file()}


def test_dry_run_writes_no_files(tmp_path: Path, sample_videos: list[dict], monkeypatch):
    output_dir = tmp_path / "output"
    channel_dir = output_dir / "test-channel"
    setup_existing_files(channel_dir)

    before = list_output_files(channel_dir)

    monkeypatch.setattr(
        ct,
        "list_channel_videos",
        lambda _url: ("Test Channel", sample_videos),
    )
    monkeypatch.setattr(ct, "slugify", lambda text, max_length=80: "test-channel")

    report = ct.process_channel(
        "https://www.youtube.com/@test/videos",
        output_dir,
        delay=0,
        dry_run=True,
    )

    after = list_output_files(channel_dir)
    assert after == before
    assert report["dry_run"] is True
    assert report["would_write_files"] is False
    assert not (channel_dir / "progress.json").exists()
    assert not (channel_dir / "report.json").exists()
    assert not (channel_dir / "index.md").exists()
    assert not (channel_dir / "reports").exists()


def test_dry_run_detects_existing_complete_pair(
    tmp_path: Path,
    sample_videos: list[dict],
    monkeypatch,
):
    output_dir = tmp_path / "output"
    channel_dir = output_dir / "test-channel"
    setup_existing_files(channel_dir)

    monkeypatch.setattr(
        ct,
        "list_channel_videos",
        lambda _url: ("Test Channel", sample_videos),
    )
    monkeypatch.setattr(ct, "slugify", lambda text, max_length=80: "test-channel")

    report = ct.process_channel(
        "https://www.youtube.com/@test/videos",
        output_dir,
        delay=0,
        dry_run=True,
    )

    existing = [item for item in report["planned"] if item["id"] == "vid2"][0]
    assert existing["action"] == "skip"
    assert existing["reason"] == "already_exists"
    assert report["would_skip_existing_count"] == 1


def test_dry_run_detects_partial_repair(
    tmp_path: Path,
    sample_videos: list[dict],
    monkeypatch,
):
    output_dir = tmp_path / "output"
    channel_dir = output_dir / "test-channel"
    setup_existing_files(channel_dir)

    monkeypatch.setattr(
        ct,
        "list_channel_videos",
        lambda _url: ("Test Channel", sample_videos),
    )
    monkeypatch.setattr(ct, "slugify", lambda text, max_length=80: "test-channel")

    report = ct.process_channel(
        "https://www.youtube.com/@test/videos",
        output_dir,
        delay=0,
        dry_run=True,
    )

    partial = [item for item in report["planned"] if item["id"] == "vid3"][0]
    assert partial["action"] == "repair"
    assert report["would_repair_partial_count"] == 1


def test_dry_run_returns_correct_counts(
    tmp_path: Path,
    sample_videos: list[dict],
    monkeypatch,
):
    output_dir = tmp_path / "output"
    setup_existing_files(output_dir / "test-channel")

    monkeypatch.setattr(
        ct,
        "list_channel_videos",
        lambda _url: ("Test Channel", sample_videos),
    )
    monkeypatch.setattr(ct, "slugify", lambda text, max_length=80: "test-channel")

    report = ct.process_channel(
        "https://www.youtube.com/@test/videos",
        output_dir,
        delay=0,
        dry_run=True,
    )

    assert report["would_process_count"] == 1
    assert report["would_skip_existing_count"] == 1
    assert report["would_repair_partial_count"] == 1
    assert report["total_videos"] == 3
    assert len(report["planned"]) == 3


def test_dry_run_does_not_fetch_metadata(
    tmp_path: Path,
    sample_videos: list[dict],
    monkeypatch,
):
    output_dir = tmp_path / "output"

    def fail_metadata(*_args, **_kwargs):
        raise AssertionError("fetch_video_metadata should not be called in dry-run")

    monkeypatch.setattr(
        ct,
        "list_channel_videos",
        lambda _url: ("Test Channel", sample_videos),
    )
    monkeypatch.setattr(ct, "slugify", lambda text, max_length=80: "test-channel")
    monkeypatch.setattr(ct, "fetch_video_metadata", fail_metadata)
    monkeypatch.setattr(ct, "download_subtitle", fail_metadata)

    ct.process_channel(
        "https://www.youtube.com/@test/videos",
        output_dir,
        delay=0,
        dry_run=True,
    )


def test_non_dry_run_still_processes_with_mocks(
    tmp_path: Path,
    sample_videos: list[dict],
    monkeypatch,
):
    output_dir = tmp_path / "output"

    monkeypatch.setattr(
        ct,
        "list_channel_videos",
        lambda _url: ("Test Channel", sample_videos[:1]),
    )
    monkeypatch.setattr(ct, "slugify", lambda text, max_length=80: "test-channel")
    monkeypatch.setattr(
        ct,
        "fetch_video_metadata",
        lambda _url: {
            "title": "New Video",
            "upload_date": "20240101",
            "channel": "Test Channel",
            "subtitles": {"en": [{}]},
            "automatic_captions": {},
        },
    )
    monkeypatch.setattr(
        ct,
        "download_subtitle",
        lambda *_args, **_kwargs: None,
    )

    report = ct.process_channel(
        "https://www.youtube.com/@test/videos",
        output_dir,
        delay=0,
        dry_run=False,
    )

    assert report.get("dry_run") is not True
    assert (output_dir / "test-channel" / "report.json").exists()

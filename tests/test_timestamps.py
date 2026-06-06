from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import ct

SAMPLE_VTT = """WEBVTT

00:00:01.000 --> 00:00:04.000
Hello &amp; welcome [Music]

00:00:04.000 --> 00:00:07.000
Line one
Line two

00:00:07.000 --> 00:00:10.000
Goodbye
"""

SAMPLE_SRT = """1
00:00:01,000 --> 00:00:04,000
Hello world [Laughter]

2
00:00:04,000 --> 00:00:07,000
Hello world
"""


def test_format_timestamp_full():
    assert ct.format_timestamp("00:00:01.000") == "00:00:01"
    assert ct.format_timestamp("00:00:01,000") == "00:00:01"
    assert ct.format_timestamp("01:02:03.456") == "01:02:03"


def test_format_timestamp_short_minutes():
    assert ct.format_timestamp("00:05.000") == "00:00:05"


def test_parse_subtitle_file_without_timestamps_unchanged(tmp_path: Path):
    path = tmp_path / "sample.en.vtt"
    path.write_text(SAMPLE_VTT, encoding="utf-8")
    text = ct.parse_subtitle_file(path, timestamps=False)
    assert "[" not in text
    assert "Hello & welcome" in text
    assert "Goodbye" in text
    assert "\n" not in text


def test_parse_subtitle_file_vtt_with_timestamps(tmp_path: Path):
    path = tmp_path / "sample.en.vtt"
    path.write_text(SAMPLE_VTT, encoding="utf-8")
    text = ct.parse_subtitle_file(path, timestamps=True)
    assert "[00:00:01] Hello & welcome" in text
    assert "[00:00:04] Line one Line two" in text
    assert "[00:00:07] Goodbye" in text
    assert "[Music]" not in text


def test_parse_subtitle_file_srt_with_timestamps(tmp_path: Path):
    path = tmp_path / "sample.en.srt"
    path.write_text(SAMPLE_SRT, encoding="utf-8")
    text = ct.parse_subtitle_file(path, timestamps=True)
    assert text.startswith("[00:00:01] Hello world")
    assert "[Laughter]" not in text
    assert text.count("Hello world") == 1


def test_parse_subtitle_file_timestamps_keep_cues(tmp_path: Path):
    path = tmp_path / "sample.en.vtt"
    path.write_text(SAMPLE_VTT, encoding="utf-8")
    text = ct.parse_subtitle_file(path, timestamps=True, keep_cues=True)
    assert "[00:00:01] Hello & welcome [Music]" in text


def test_build_md_content_includes_timestamps_frontmatter():
    content = ct.build_md_content(
        title="Test",
        url="https://example.com",
        upload_date="2024-01-01",
        channel="Channel",
        video_id="vid1",
        transcript="[00:00:01] Hello",
        timestamps=True,
    )
    assert "timestamps: true" in content


def _mock_download(
    _video_url: str,
    video_id: str,
    lang: str,
    _sub_type: str,
    out_dir: Path,
) -> Path:
    vtt_path = out_dir / f"{video_id}.{lang}.vtt"
    vtt_path.write_text(
        "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nHello world\n\n"
        "00:00:05.000 --> 00:00:08.000\nSecond cue\n",
        encoding="utf-8",
    )
    return vtt_path


@pytest.fixture
def mocked_channel(monkeypatch):
    videos = [
        {
            "id": "vid1",
            "title": "Timestamped Video",
            "url": "https://www.youtube.com/watch?v=vid1",
            "upload_date": "20240101",
        }
    ]
    metadata = {
        "title": "Timestamped Video",
        "upload_date": "20240101",
        "channel": "Test Channel",
        "subtitles": {"en": [{}]},
        "automatic_captions": {},
    }

    monkeypatch.setattr(ct, "list_channel_videos", lambda _url: ("Test Channel", videos))
    monkeypatch.setattr(ct, "slugify", lambda text, max_length=80: "test-channel")
    monkeypatch.setattr(ct, "fetch_video_metadata", lambda _url: metadata)
    monkeypatch.setattr(ct, "download_subtitle", _mock_download)


@pytest.mark.parametrize("output_format", ["txt", "md", "both"])
def test_timestamps_with_output_formats(
    tmp_path: Path,
    mocked_channel,
    output_format: str,
):
    output_dir = tmp_path / "output"
    report = ct.process_channel(
        "https://www.youtube.com/@test/videos",
        output_dir,
        delay=0,
        output_format=output_format,
        timestamps=True,
    )

    base = output_dir / "test-channel"
    assert report["timestamps"] is True

    if output_format in ("txt", "both"):
        txt = (base / "txt" / list((base / "txt").glob("*.txt"))[0]).read_text(encoding="utf-8")
        assert "[00:00:01] Hello world" in txt
        assert "[00:00:05] Second cue" in txt

    if output_format in ("md", "both"):
        md = (base / "md" / list((base / "md").glob("*.md"))[0]).read_text(encoding="utf-8")
        assert "[00:00:01] Hello world" in md
        assert "timestamps: true" in md


def test_timestamps_dry_run_writes_no_files(tmp_path: Path, mocked_channel, monkeypatch):
    output_dir = tmp_path / "output"

    report = ct.process_channel(
        "https://www.youtube.com/@test/videos",
        output_dir,
        delay=0,
        dry_run=True,
        timestamps=True,
    )

    assert report["timestamps"] is True
    assert report["would_write_files"] is False

    channel_dir = output_dir / "test-channel"
    if channel_dir.exists():
        files = [path for path in channel_dir.rglob("*") if path.is_file()]
        assert files == []


def test_metadata_only_ignores_timestamps(tmp_path: Path, monkeypatch):
    videos = [
        {
            "id": "vid1",
            "title": "Video 1",
            "url": "https://www.youtube.com/watch?v=vid1",
            "upload_date": "20240101",
        }
    ]

    monkeypatch.setattr(ct, "list_channel_videos", lambda _url: ("Test Channel", videos))
    monkeypatch.setattr(ct, "slugify", lambda text, max_length=80: "test-channel")

    output_dir = tmp_path / "output"
    report = ct.process_channel(
        "https://www.youtube.com/@test/videos",
        output_dir,
        delay=0,
        metadata_only=True,
        timestamps=True,
    )

    base = output_dir / "test-channel"
    assert report["metadata_only"] is True
    assert (base / "videos.json").exists()
    assert (base / "videos.csv").exists()
    assert list((base / "txt").glob("*.txt")) == []
    assert list((base / "md").glob("*.md")) == []

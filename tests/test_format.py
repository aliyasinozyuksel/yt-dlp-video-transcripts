from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from tests.conftest import ct
from tests.test_disk_and_resume import write_pair


def test_format_default_is_both():
    assert ct.DEFAULT_OUTPUT_FORMAT == "both"


def test_cli_format_choices():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--format",
        choices=["both", "txt", "md"],
        default=ct.DEFAULT_OUTPUT_FORMAT,
    )
    assert parser.parse_args([]).format == "both"
    assert parser.parse_args(["--format", "txt"]).format == "txt"
    assert parser.parse_args(["--format", "md"]).format == "md"


def test_resolve_video_action_txt_skips_when_txt_exists(tmp_path: Path):
    write_pair(
        tmp_path,
        index=1,
        video_id="vid1",
        title="Txt Only",
        include_md=False,
        include_txt=True,
    )
    existing = ct.build_existing_files_index(tmp_path / "md", tmp_path / "txt")
    action, _, _, _, _ = ct.resolve_video_action(
        "vid1",
        1,
        "001_other",
        tmp_path / "txt" / "001_other.txt",
        tmp_path / "md" / "001_other.md",
        existing,
        tmp_path / "txt",
        force=False,
        output_format="txt",
    )
    assert action == "skip"


def test_resolve_video_action_txt_repairs_when_only_md_exists(tmp_path: Path):
    write_pair(
        tmp_path,
        index=2,
        video_id="vid2",
        title="Md Only",
        include_md=True,
        include_txt=False,
    )
    existing = ct.build_existing_files_index(tmp_path / "md", tmp_path / "txt")
    action, _, _, _, partial = ct.resolve_video_action(
        "vid2",
        2,
        "002_other",
        tmp_path / "txt" / "002_other.txt",
        tmp_path / "md" / "002_other.md",
        existing,
        tmp_path / "txt",
        force=False,
        output_format="txt",
    )
    assert action == "repair"
    assert partial is True


def test_resolve_video_action_md_skips_when_md_exists(tmp_path: Path):
    write_pair(
        tmp_path,
        index=3,
        video_id="vid3",
        title="Md Complete",
        include_md=True,
        include_txt=False,
    )
    existing = ct.build_existing_files_index(tmp_path / "md", tmp_path / "txt")
    action, _, _, _, _ = ct.resolve_video_action(
        "vid3",
        3,
        "003_other",
        tmp_path / "txt" / "003_other.txt",
        tmp_path / "md" / "003_other.md",
        existing,
        tmp_path / "txt",
        force=False,
        output_format="md",
    )
    assert action == "skip"


def test_resolve_video_action_md_repairs_when_only_txt_exists(tmp_path: Path):
    write_pair(
        tmp_path,
        index=4,
        video_id="vid4",
        title="Txt Only",
        include_md=False,
        include_txt=True,
        basename="004_txt-only_vid4",
    )
    existing = ct.build_existing_files_index(tmp_path / "md", tmp_path / "txt")
    action, _, _, _, partial = ct.resolve_video_action(
        "vid4",
        4,
        "004_other",
        tmp_path / "txt" / "004_other.txt",
        tmp_path / "md" / "004_other.md",
        existing,
        tmp_path / "txt",
        force=False,
        output_format="md",
    )
    assert action == "repair"
    assert partial is True


def test_resolve_video_action_both_still_requires_pair(tmp_path: Path):
    write_pair(
        tmp_path,
        index=5,
        video_id="vid5",
        title="Partial",
        include_md=False,
        include_txt=True,
    )
    existing = ct.build_existing_files_index(tmp_path / "md", tmp_path / "txt")
    action, _, _, _, partial = ct.resolve_video_action(
        "vid5",
        5,
        "005_partial",
        tmp_path / "txt" / "005_partial.txt",
        tmp_path / "md" / "005_partial.md",
        existing,
        tmp_path / "txt",
        force=False,
        output_format="both",
    )
    assert action == "repair"
    assert partial is True


def test_collect_completed_from_disk_respects_format(tmp_path: Path):
    write_pair(tmp_path, index=1, video_id="a", title="Both", include_md=True, include_txt=True)
    write_pair(
        tmp_path,
        index=2,
        video_id="b",
        title="Txt",
        include_md=False,
        include_txt=True,
    )
    write_pair(
        tmp_path,
        index=3,
        video_id="c",
        title="Md",
        include_md=True,
        include_txt=False,
    )

    md_dir = tmp_path / "md"
    txt_dir = tmp_path / "txt"
    assert len(ct.collect_completed_from_disk(md_dir, txt_dir, "both")) == 1
    assert len(ct.collect_completed_from_disk(md_dir, txt_dir, "txt")) == 2
    assert len(ct.collect_completed_from_disk(md_dir, txt_dir, "md")) == 2


def test_write_index_md_links_txt_for_txt_format(tmp_path: Path):
    items = [
        {
            "index": 1,
            "title": "Example",
            "upload_date": "2024-01-01",
            "basename": "001_example",
            "link_ext": "txt",
        }
    ]
    index_path = tmp_path / "index.md"
    ct.write_index_md(index_path, "Test Channel", items, "txt")
    content = index_path.read_text(encoding="utf-8")
    assert "](txt/001_example.txt)" in content
    assert "](md/" not in content


def test_write_index_md_links_md_for_md_format(tmp_path: Path):
    items = [
        {
            "index": 1,
            "title": "Example",
            "upload_date": "2024-01-01",
            "basename": "001_example",
            "link_ext": "md",
        }
    ]
    index_path = tmp_path / "index.md"
    ct.write_index_md(index_path, "Test Channel", items, "md")
    content = index_path.read_text(encoding="utf-8")
    assert "](md/001_example.md)" in content


def _mock_download_subtitle(
    _video_url: str,
    video_id: str,
    lang: str,
    _sub_type: str,
    out_dir: Path,
) -> Path:
    vtt_path = out_dir / f"{video_id}.{lang}.vtt"
    vtt_path.write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello world\n",
        encoding="utf-8",
    )
    return vtt_path


@pytest.fixture
def mocked_process(monkeypatch):
    videos = [
        {
            "id": "vid1",
            "title": "New Video",
            "url": "https://www.youtube.com/watch?v=vid1",
            "upload_date": "20240101",
        }
    ]
    metadata = {
        "title": "New Video",
        "upload_date": "20240101",
        "channel": "Test Channel",
        "subtitles": {"en": [{}]},
        "automatic_captions": {},
    }

    monkeypatch.setattr(ct, "list_channel_videos", lambda _url: ("Test Channel", videos))
    monkeypatch.setattr(ct, "slugify", lambda text, max_length=80: "test-channel")
    monkeypatch.setattr(ct, "fetch_video_metadata", lambda _url: metadata)
    monkeypatch.setattr(ct, "download_subtitle", _mock_download_subtitle)
    return videos


def test_format_txt_writes_only_txt(tmp_path: Path, mocked_process, monkeypatch):
    output_dir = tmp_path / "output"
    report = ct.process_channel(
        "https://www.youtube.com/@test/videos",
        output_dir,
        delay=0,
        output_format="txt",
    )

    base = output_dir / "test-channel"
    txt_files = list((base / "txt").glob("*.txt"))
    md_files = list((base / "md").glob("*.md"))
    assert len(txt_files) == 1
    assert md_files == []
    assert report["output_format"] == "txt"
    assert "txt_file" in report["processed"][0]
    assert "md_file" not in report["processed"][0]


def test_format_md_writes_only_md(tmp_path: Path, mocked_process):
    output_dir = tmp_path / "output"
    report = ct.process_channel(
        "https://www.youtube.com/@test/videos",
        output_dir,
        delay=0,
        output_format="md",
    )

    base = output_dir / "test-channel"
    txt_files = list((base / "txt").glob("*.txt"))
    md_files = list((base / "md").glob("*.md"))
    assert txt_files == []
    assert len(md_files) == 1
    assert report["output_format"] == "md"
    assert "md_file" in report["processed"][0]
    assert "txt_file" not in report["processed"][0]


def test_format_both_writes_both_files(tmp_path: Path, mocked_process):
    output_dir = tmp_path / "output"
    report = ct.process_channel(
        "https://www.youtube.com/@test/videos",
        output_dir,
        delay=0,
        output_format="both",
    )

    base = output_dir / "test-channel"
    assert len(list((base / "txt").glob("*.txt"))) == 1
    assert len(list((base / "md").glob("*.md"))) == 1
    assert "txt_file" in report["processed"][0]
    assert "md_file" in report["processed"][0]


def test_resume_skips_txt_format_when_txt_exists(tmp_path: Path, mocked_process):
    output_dir = tmp_path / "output"
    channel_dir = output_dir / "test-channel"
    write_pair(channel_dir, index=1, video_id="vid1", title="New Video", include_md=False)

    report = ct.process_channel(
        "https://www.youtube.com/@test/videos",
        output_dir,
        delay=0,
        output_format="txt",
    )

    assert report["existing_count"] == 1
    assert len(list((channel_dir / "txt").glob("*.txt"))) == 1
    assert list((channel_dir / "md").glob("*.md")) == []


def test_resume_skips_md_format_when_md_exists(tmp_path: Path, mocked_process):
    output_dir = tmp_path / "output"
    channel_dir = output_dir / "test-channel"
    write_pair(channel_dir, index=1, video_id="vid1", title="New Video", include_txt=False)

    report = ct.process_channel(
        "https://www.youtube.com/@test/videos",
        output_dir,
        delay=0,
        output_format="md",
    )

    assert report["existing_count"] == 1
    assert list((channel_dir / "txt").glob("*.txt")) == []
    assert len(list((channel_dir / "md").glob("*.md"))) == 1


def test_dry_run_format_txt_respects_existing_txt(tmp_path: Path, monkeypatch):
    output_dir = tmp_path / "output"
    channel_dir = output_dir / "test-channel"
    write_pair(channel_dir, index=1, video_id="vid1", title="Existing", include_md=False)

    videos = [
        {
            "id": "vid1",
            "title": "Existing",
            "url": "https://www.youtube.com/watch?v=vid1",
            "upload_date": "20240101",
        },
        {
            "id": "vid2",
            "title": "New",
            "url": "https://www.youtube.com/watch?v=vid2",
            "upload_date": "20240102",
        },
    ]
    monkeypatch.setattr(ct, "list_channel_videos", lambda _url: ("Test Channel", videos))
    monkeypatch.setattr(ct, "slugify", lambda text, max_length=80: "test-channel")

    before = {p for p in channel_dir.rglob("*") if p.is_file()}
    report = ct.process_channel(
        "https://www.youtube.com/@test/videos",
        output_dir,
        delay=0,
        dry_run=True,
        output_format="txt",
    )
    after = {p for p in channel_dir.rglob("*") if p.is_file()}

    assert after == before
    assert report["output_format"] == "txt"
    assert report["would_write_files"] is False
    assert report["would_skip_existing_count"] == 1
    assert report["would_process_count"] == 1


def test_dry_run_format_md_processes_when_only_txt_exists(tmp_path: Path, monkeypatch):
    output_dir = tmp_path / "output"
    channel_dir = output_dir / "test-channel"
    write_pair(
        channel_dir,
        index=1,
        video_id="vid1",
        title="Txt Only",
        include_md=False,
        basename="001_txt-only_vid1",
    )

    videos = [
        {
            "id": "vid1",
            "title": "Txt Only",
            "url": "https://www.youtube.com/watch?v=vid1",
            "upload_date": "20240101",
        }
    ]
    monkeypatch.setattr(ct, "list_channel_videos", lambda _url: ("Test Channel", videos))
    monkeypatch.setattr(ct, "slugify", lambda text, max_length=80: "test-channel")

    report = ct.process_channel(
        "https://www.youtube.com/@test/videos",
        output_dir,
        delay=0,
        dry_run=True,
        output_format="md",
    )

    planned = report["planned"][0]
    assert planned["action"] == "repair"
    assert report["would_repair_partial_count"] == 1

from __future__ import annotations

from pathlib import Path

from tests.conftest import ct


def test_pick_english_subtitle_fallback_to_auto_by_default():
    subtitles: dict = {}
    automatic_captions = {"en": [{}]}
    lang, sub_type = ct.pick_english_subtitle(subtitles, automatic_captions)
    assert lang == "en"
    assert sub_type == "auto"


def test_pick_english_subtitle_prefers_manual_over_auto():
    subtitles = {"en": [{}]}
    automatic_captions = {"en": [{}]}
    lang, sub_type = ct.pick_english_subtitle(subtitles, automatic_captions)
    assert lang == "en"
    assert sub_type == "manual"


def test_pick_english_subtitle_manual_only_selects_manual():
    subtitles = {"en-US": [{}]}
    automatic_captions = {"en": [{}]}
    lang, sub_type = ct.pick_english_subtitle(
        subtitles,
        automatic_captions,
        manual_only=True,
    )
    assert lang == "en-US"
    assert sub_type == "manual"


def test_pick_english_subtitle_manual_only_ignores_auto():
    subtitles: dict = {}
    automatic_captions = {"en": [{}], "en-GB": [{}]}
    lang, sub_type = ct.pick_english_subtitle(
        subtitles,
        automatic_captions,
        manual_only=True,
    )
    assert lang is None
    assert sub_type is None


def test_pick_english_subtitle_manual_only_returns_none_when_only_auto():
    subtitles: dict = {}
    automatic_captions = {"en": [{}]}
    lang, sub_type = ct.pick_english_subtitle(
        subtitles,
        automatic_captions,
        manual_only=True,
    )
    assert (lang, sub_type) == (None, None)


def test_subtitle_skip_reason_manual_only():
    assert ct.subtitle_skip_reason(True) == "no_manual_english_subtitles"
    assert ct.subtitle_skip_reason(False) == "no_english_subtitles"


def test_manual_only_skips_with_correct_reason(tmp_path: Path, monkeypatch):
    output_dir = tmp_path / "output"
    videos = [
        {
            "id": "vid1",
            "title": "Auto Only",
            "url": "https://www.youtube.com/watch?v=vid1",
            "upload_date": "20240101",
        }
    ]

    monkeypatch.setattr(
        ct,
        "list_channel_videos",
        lambda _url: ("Test Channel", videos),
    )
    monkeypatch.setattr(ct, "slugify", lambda text, max_length=80: "test-channel")
    monkeypatch.setattr(
        ct,
        "fetch_video_metadata",
        lambda _url: {
            "title": "Auto Only",
            "upload_date": "20240101",
            "channel": "Test Channel",
            "subtitles": {},
            "automatic_captions": {"en": [{}]},
        },
    )

    report = ct.process_channel(
        "https://www.youtube.com/@test/videos",
        output_dir,
        delay=0,
        manual_only=True,
    )

    assert report["manual_only"] is True
    assert len(report["skipped"]) == 1
    assert report["skipped"][0]["reason"] == "no_manual_english_subtitles"
    assert not (output_dir / "test-channel" / "txt").exists() or not list(
        (output_dir / "test-channel" / "txt").glob("*.txt")
    )

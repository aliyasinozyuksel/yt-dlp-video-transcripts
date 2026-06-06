from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import ct


def test_parse_langs_single():
    assert ct.parse_langs("en") == ["en"]


def test_parse_langs_multiple():
    assert ct.parse_langs("en,tr") == ["en", "tr"]


def test_parse_langs_dedupes_and_trims():
    assert ct.parse_langs(" tr, en ,tr ") == ["tr", "en"]


def test_parse_langs_empty_raises():
    with pytest.raises(ct.UserError):
        ct.parse_langs(" , , ")


def test_language_candidates_en_static():
    candidates = ct.language_candidates("en")
    assert candidates[:4] == ["en", "en-US", "en-GB", "en-orig"]


def test_language_candidates_tr_static():
    candidates = ct.language_candidates("tr")
    assert candidates[:3] == ["tr", "tr-TR", "tr-orig"]


def test_language_candidates_pt_static():
    candidates = ct.language_candidates("pt")
    assert "pt-BR" in candidates
    assert "pt-PT" in candidates


def test_language_candidates_zh_static():
    candidates = ct.language_candidates("zh")
    for code in ("zh-Hans", "zh-Hant", "zh-CN", "zh-TW"):
        assert code in candidates


def test_language_candidates_with_available_prefix_match():
    available = ["en-GB", "en-CA"]
    candidates = ct.language_candidates("en", available)
    assert candidates == ["en-GB", "en-CA"]


def test_pick_subtitle_prefers_manual_over_auto():
    subtitles = {"en": [{}]}
    automatic = {"en": [{}]}
    lang, sub_type = ct.pick_subtitle(subtitles, automatic, ["en"])
    assert lang == "en"
    assert sub_type == "manual"


def test_pick_subtitle_respects_requested_language_order():
    subtitles = {"en": [{}], "tr": [{}]}
    lang, sub_type = ct.pick_subtitle(subtitles, {}, ["tr", "en"])
    assert lang == "tr"
    assert sub_type == "manual"


def test_pick_subtitle_falls_back_to_auto_when_no_manual():
    subtitles: dict = {}
    automatic = {"tr": [{}]}
    lang, sub_type = ct.pick_subtitle(subtitles, automatic, ["en", "tr"])
    assert lang == "tr"
    assert sub_type == "auto"


def test_pick_subtitle_manual_only_never_selects_auto():
    subtitles: dict = {}
    automatic = {"en": [{}], "tr": [{}]}
    lang, sub_type = ct.pick_subtitle(
        subtitles,
        automatic,
        ["tr", "en"],
        manual_only=True,
    )
    assert (lang, sub_type) == (None, None)


def test_pick_english_subtitle_default_english_compatible():
    subtitles: dict = {}
    automatic = {"en-US": [{}]}
    lang, sub_type = ct.pick_english_subtitle(subtitles, automatic)
    assert lang == "en-US"
    assert sub_type == "auto"


def test_subtitle_skip_reasons():
    assert ct.subtitle_skip_reason(False) == "no_requested_subtitles"
    assert ct.subtitle_skip_reason(True) == "no_manual_requested_subtitles"


def test_missing_requested_subtitles_integration(tmp_path: Path, monkeypatch):
    output_dir = tmp_path / "output"
    videos = [
        {
            "id": "vid1",
            "title": "No Subs",
            "url": "https://www.youtube.com/watch?v=vid1",
            "upload_date": "20240101",
        }
    ]

    monkeypatch.setattr(ct, "list_channel_videos", lambda _url: ("Test Channel", videos))
    monkeypatch.setattr(ct, "slugify", lambda text, max_length=80: "test-channel")
    monkeypatch.setattr(
        ct,
        "fetch_video_metadata",
        lambda _url: {
            "title": "No Subs",
            "upload_date": "20240101",
            "channel": "Test Channel",
            "subtitles": {},
            "automatic_captions": {},
        },
    )

    report = ct.process_channel(
        "https://www.youtube.com/@test/videos",
        output_dir,
        delay=0,
        requested_langs=["tr"],
    )

    assert report["requested_langs"] == ["tr"]
    assert report["skipped"][0]["reason"] == "no_requested_subtitles"


def test_manual_only_missing_requested_subtitles_integration(tmp_path: Path, monkeypatch):
    output_dir = tmp_path / "output"
    videos = [
        {
            "id": "vid1",
            "title": "Auto Only",
            "url": "https://www.youtube.com/watch?v=vid1",
            "upload_date": "20240101",
        }
    ]

    monkeypatch.setattr(ct, "list_channel_videos", lambda _url: ("Test Channel", videos))
    monkeypatch.setattr(ct, "slugify", lambda text, max_length=80: "test-channel")
    monkeypatch.setattr(
        ct,
        "fetch_video_metadata",
        lambda _url: {
            "title": "Auto Only",
            "upload_date": "20240101",
            "channel": "Test Channel",
            "subtitles": {},
            "automatic_captions": {"tr": [{}]},
        },
    )

    report = ct.process_channel(
        "https://www.youtube.com/@test/videos",
        output_dir,
        delay=0,
        requested_langs=["tr"],
        manual_only=True,
    )

    assert report["skipped"][0]["reason"] == "no_manual_requested_subtitles"


def test_dry_run_includes_requested_langs(tmp_path: Path, monkeypatch):
    output_dir = tmp_path / "output"
    videos = [
        {
            "id": "vid1",
            "title": "Video",
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
        requested_langs=["tr", "en"],
    )

    assert report["requested_langs"] == ["tr", "en"]
    assert report["dry_run"] is True

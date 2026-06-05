from __future__ import annotations

from pathlib import Path

from tests.conftest import ct

SAMPLE_VTT = """WEBVTT

00:00:01.000 --> 00:00:04.000
Hello &amp; welcome [Music]

00:00:04.000 --> 00:00:07.000
This is a test [Applause]

00:00:07.000 --> 00:00:10.000
[Submit subtitle corrections at https://example.com]
Goodbye
"""

SAMPLE_SRT = """1
00:00:01,000 --> 00:00:04,000
Hello world [Laughter]

2
00:00:04,000 --> 00:00:07,000
Hello world
"""


def test_parse_subtitle_file_vtt_removes_cues(tmp_path: Path):
    path = tmp_path / "sample.en.vtt"
    path.write_text(SAMPLE_VTT, encoding="utf-8")
    text = ct.parse_subtitle_file(path)
    assert "Hello & welcome" in text
    assert "[Music]" not in text
    assert "[Applause]" not in text
    assert "Submit subtitle corrections" not in text
    assert "Goodbye" in text


def test_parse_subtitle_file_srt(tmp_path: Path):
    path = tmp_path / "sample.en.srt"
    path.write_text(SAMPLE_SRT, encoding="utf-8")
    text = ct.parse_subtitle_file(path)
    assert "Hello world" in text
    assert "[Laughter]" not in text


def test_parse_subtitle_file_keep_cues(tmp_path: Path):
    path = tmp_path / "sample.en.vtt"
    path.write_text(SAMPLE_VTT, encoding="utf-8")
    text = ct.parse_subtitle_file(path, keep_cues=True)
    assert "[Music]" in text
    assert "[Applause]" in text


def test_clean_transcript_text_music_note():
    text = ct.clean_transcript_text("Sing ♪ along", keep_cues=False)
    assert "♪" not in text
    assert "Sing" in text

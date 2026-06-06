#!/usr/bin/env python3
"""Download English subtitles from a YouTube channel and convert to txt/md."""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, TypeVar
from urllib.parse import urlsplit, urlunsplit

try:
    import yt_dlp
except ImportError as exc:
    yt_dlp = None  # type: ignore[assignment]
    _YT_DLP_IMPORT_ERROR = exc
else:
    _YT_DLP_IMPORT_ERROR = None

VERSION = "1.6.2"

DEFAULT_LANGS = ["en"]
DEFAULT_OUTPUT_FORMAT = "both"
DEFAULT_PROGRESS_INTERVAL = 10
OutputFormat = Literal["both", "txt", "md"]
CHANNEL_TAB_SUFFIXES = ("/videos", "/shorts", "/streams", "/playlists", "/featured", "/about")
SUBTITLE_CUE_PATTERNS = [
    re.compile(r"\[Music\]", re.IGNORECASE),
    re.compile(r"\[Applause\]", re.IGNORECASE),
    re.compile(r"\[Laughter\]", re.IGNORECASE),
    re.compile(r"\[applause and cheering\]", re.IGNORECASE),
    re.compile(r"\[Submit subtitle corrections at[^\]]*\]", re.IGNORECASE),
]
MD_INDEX_RE = re.compile(r"^(\d+)_.+\.md$")
TXT_INDEX_RE = re.compile(r"^(\d+)_.+\.txt$")
YOUTUBE_ID_RE = re.compile(r"^[\w-]{11}$")

T = TypeVar("T")
VideoAction = Literal["skip", "repair", "process"]


class UserError(Exception):
    """User-facing error with a clear message."""


class DependencyError(UserError):
    """Missing runtime dependency."""


class RetryError(UserError):
    """Operation failed after all retry attempts."""


def require_yt_dlp() -> None:
    if yt_dlp is None:
        raise DependencyError(
            "yt-dlp is not installed or could not be imported. "
            "Install it with: pip install yt-dlp"
        ) from _YT_DLP_IMPORT_ERROR


def normalize_channel_url(url: str) -> str:
    url = url.strip()
    if not url:
        return url

    if "watch?v=" in url or "playlist?list=" in url:
        return url.rstrip("/")

    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    has_tab_suffix = any(path.endswith(suffix) for suffix in CHANNEL_TAB_SUFFIXES)

    if "/@" in path and not has_tab_suffix:
        path = f"{path}/videos"
    elif "/channel/" in path and not has_tab_suffix:
        path = f"{path}/videos"

    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def resolve_video_url(entry: dict[str, Any], video_id: str) -> str:
    raw_url = entry.get("webpage_url") or entry.get("url")
    if raw_url and str(raw_url).startswith("http"):
        return str(raw_url)
    return f"https://www.youtube.com/watch?v={video_id}"


def slugify(text: str, max_length: int = 80) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "-", text).strip("-")
    return text[:max_length].rstrip("-") or "untitled"


def format_upload_date(raw: str | None) -> str:
    if not raw or len(raw) != 8:
        return ""
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def yaml_str(value: str) -> str:
    return json.dumps(value or "", ensure_ascii=False)


def md_table_cell(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def md_heading_text(value: str) -> str:
    text = re.sub(r"[\r\n\t]+", " ", value or "")
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\s+", " ", text).strip()
    return text or "Untitled"


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text)


def clean_transcript_text(text: str, *, keep_cues: bool = False) -> str:
    text = strip_html(text)
    text = re.sub(r"\{[^}]+\}", "", text)
    if not keep_cues:
        for pattern in SUBTITLE_CUE_PATTERNS:
            text = pattern.sub("", text)
        text = text.replace("♪", "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd: int | None = None
    tmp_path: Path | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        tmp_path = Path(tmp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = None
            handle.write(content)
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if fd is not None:
            os.close(fd)
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


NON_RETRYABLE_EXCEPTIONS = (TypeError, ValueError, KeyError, AssertionError)


def is_retryable_error(exc: Exception) -> bool:
    if isinstance(exc, NON_RETRYABLE_EXCEPTIONS):
        return False
    if isinstance(exc, (UserError, DependencyError)):
        return False
    return True


def retry_call(fn: Callable[[], T], attempts: int, delay: float, action: str) -> T:
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:
            if not is_retryable_error(exc):
                raise
            last_exc = exc
            if attempt < attempts - 1:
                time.sleep(delay)
    assert last_exc is not None
    raise RetryError(
        f"{action} failed after {attempts} attempt(s): {last_exc}"
    ) from last_exc


CUE_TIMESTAMP_RE = re.compile(
    r"^(\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3})\s+-->",
)


def format_timestamp(raw: str) -> str:
    normalized = raw.strip().replace(",", ".")
    if "." in normalized:
        normalized = normalized.split(".", 1)[0]

    parts = normalized.split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return f"00:{int(minutes):02d}:{int(seconds):02d}"
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}"
    return normalized


def _parse_cue_timestamp(line: str) -> str | None:
    match = CUE_TIMESTAMP_RE.match(line.strip())
    if not match:
        return None
    return format_timestamp(match.group(1))


def parse_subtitle_file(
    path: Path,
    *,
    keep_cues: bool = False,
    timestamps: bool = False,
) -> str:
    content = path.read_text(encoding="utf-8", errors="replace")

    if not timestamps:
        lines: list[str] = []

        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("WEBVTT") or line.startswith("NOTE"):
                continue
            if re.match(r"^\d+$", line):
                continue
            if CUE_TIMESTAMP_RE.match(line):
                continue
            if line.startswith("Kind:") or line.startswith("Language:"):
                continue

            cleaned = clean_transcript_text(line, keep_cues=keep_cues)
            if cleaned:
                lines.append(cleaned)

        merged: list[str] = []
        for line in lines:
            if merged and merged[-1] == line:
                continue
            merged.append(line)

        return re.sub(r"\s+", " ", " ".join(merged)).strip()

    cues: list[tuple[str, str]] = []
    current_ts: str | None = None
    current_lines: list[str] = []

    def flush_cue() -> None:
        nonlocal current_ts, current_lines
        if current_ts and current_lines:
            parts: list[str] = []
            for cue_line in current_lines:
                cleaned = clean_transcript_text(cue_line, keep_cues=keep_cues)
                if cleaned:
                    parts.append(cleaned)
            if parts:
                cues.append((current_ts, " ".join(parts)))
        current_ts = None
        current_lines = []

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            flush_cue()
            continue
        if line.startswith("WEBVTT") or line.startswith("NOTE"):
            continue
        if re.match(r"^\d+$", line):
            continue
        if line.startswith("Kind:") or line.startswith("Language:"):
            continue

        cue_ts = _parse_cue_timestamp(line)
        if cue_ts:
            flush_cue()
            current_ts = cue_ts
            continue

        if current_ts is not None:
            current_lines.append(line)

    flush_cue()

    merged_cues: list[tuple[str, str]] = []
    for ts, text in cues:
        if merged_cues and merged_cues[-1][1] == text:
            continue
        merged_cues.append((ts, text))

    return "\n".join(f"[{ts}] {text}" for ts, text in merged_cues).strip()


def list_channel_videos(channel_url: str) -> tuple[str, list[dict[str, Any]]]:
    require_yt_dlp()
    channel_url = normalize_channel_url(channel_url)
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(channel_url, download=False)

    channel_name = info.get("channel") or info.get("uploader") or info.get("title") or "channel"
    entries = info.get("entries")

    videos: list[dict[str, Any]] = []
    if entries:
        for entry in entries:
            if not entry:
                continue
            video_id = entry.get("id")
            if not video_id:
                continue
            videos.append(
                {
                    "id": video_id,
                    "title": entry.get("title") or video_id,
                    "url": resolve_video_url(entry, video_id),
                    "upload_date": entry.get("upload_date"),
                }
            )
    elif info.get("id"):
        video_id = info["id"]
        videos.append(
            {
                "id": video_id,
                "title": info.get("title") or video_id,
                "url": resolve_video_url(info, video_id),
                "upload_date": info.get("upload_date"),
            }
        )
        channel_name = info.get("channel") or info.get("uploader") or channel_name

    return channel_name, videos


def select_videos(
    all_videos: list[dict[str, Any]],
    max_videos: int | None,
    start_index: int | None,
    end_index: int | None,
) -> list[tuple[int, dict[str, Any]]]:
    total = len(all_videos)
    if start_index is not None or end_index is not None:
        start = start_index if start_index is not None else 1
        end = end_index if end_index is not None else total
        if start > total:
            return []
        end = min(end, total)
        return [(index, all_videos[index - 1]) for index in range(start, end + 1)]

    if max_videos is not None:
        count = min(max_videos, total)
        return [(index, all_videos[index - 1]) for index in range(1, count + 1)]

    return [(index, all_videos[index - 1]) for index in range(1, total + 1)]


def build_metadata_videos_list(
    selected_videos: list[tuple[int, dict[str, Any]]],
) -> list[dict[str, Any]]:
    return [
        {
            "index": global_index,
            "id": video["id"],
            "title": video["title"],
            "url": video["url"],
            "upload_date": format_upload_date(video.get("upload_date")),
        }
        for global_index, video in selected_videos
    ]


def build_metadata_payload(
    *,
    channel_url: str,
    channel_name: str,
    total_channel_videos: int,
    selected_count: int,
    start_index: int | None,
    end_index: int | None,
    max_videos: int | None,
    videos: list[dict[str, Any]],
    generated_at: str | None = None,
) -> dict[str, Any]:
    return {
        "channel_url": channel_url,
        "channel_name": channel_name,
        "total_channel_videos": total_channel_videos,
        "total_videos": total_channel_videos,
        "selected_count": selected_count,
        "start_index": start_index,
        "end_index": end_index,
        "max_videos": max_videos,
        "generated_at": generated_at or utc_now(),
        "videos": videos,
    }


def write_videos_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_json(path, payload)


def safe_csv_cell(value: Any) -> str:
    text = str(value)
    if text and text[0] in ("=", "+", "-", "@"):
        return f"'{text}"
    return text


def write_videos_csv(path: Path, videos: list[dict[str, Any]]) -> None:
    fieldnames = ["index", "id", "title", "url", "upload_date"]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for video in videos:
        writer.writerow(
            {field: safe_csv_cell(video[field]) for field in fieldnames}
        )
    atomic_write_text(path, buffer.getvalue())


def print_metadata_only_summary(
    *,
    videos_json_path: Path,
    videos_csv_path: Path,
    dry_run: bool,
) -> None:
    if dry_run:
        print("\nWould write metadata files: no")
        return
    print("\nMetadata written:")
    print(f"videos.json: {videos_json_path}")
    print(f"videos.csv:  {videos_csv_path}")


def process_metadata_only(
    channel_url: str,
    output_dir: Path,
    *,
    max_videos: int | None = None,
    start_index: int | None = None,
    end_index: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    channel_name, all_videos = list_channel_videos(channel_url)
    total_channel_videos = len(all_videos)

    if total_channel_videos == 0:
        raise UserError("No videos found for the provided URL.")

    selected_videos = select_videos(all_videos, max_videos, start_index, end_index)
    selected_count = len(selected_videos)

    if selected_count == 0:
        raise UserError("The selected range contains no videos.")

    channel_slug = slugify(channel_name)
    base_dir = output_dir / channel_slug
    if not dry_run:
        base_dir.mkdir(parents=True, exist_ok=True)

    videos_json_path = base_dir / "videos.json"
    videos_csv_path = base_dir / "videos.csv"
    metadata_videos = build_metadata_videos_list(selected_videos)
    generated_at = utc_now()

    print(f"Channel: {channel_name}")
    print(f"Total videos (channel): {total_channel_videos}")
    print(f"Videos selected: {selected_count}")
    print("Metadata only: yes")
    if dry_run:
        print("Dry run: yes")
    if start_index is not None or end_index is not None:
        print(f"Index range: {start_index or 1}-{end_index or total_channel_videos}")
    elif max_videos is not None:
        print(f"Max videos: {max_videos}")
    print(f"Output: {base_dir}\n")

    for position, (global_index, video) in enumerate(selected_videos, start=1):
        print(f"[{position}/{selected_count}] (#{global_index}) {video['title']}")

    payload = build_metadata_payload(
        channel_url=channel_url,
        channel_name=channel_name,
        total_channel_videos=total_channel_videos,
        selected_count=selected_count,
        start_index=start_index,
        end_index=end_index,
        max_videos=max_videos,
        videos=metadata_videos,
        generated_at=generated_at,
    )

    report: dict[str, Any] = {
        "metadata_only": True,
        "dry_run": dry_run,
        "channel_url": channel_url,
        "channel_name": channel_name,
        "total_channel_videos": total_channel_videos,
        "total_videos": total_channel_videos,
        "selected_count": selected_count,
        "start_index": start_index,
        "end_index": end_index,
        "max_videos": max_videos,
        "generated_at": generated_at,
        "output_path": str(base_dir),
        "videos_json_path": str(videos_json_path),
        "videos_csv_path": str(videos_csv_path),
        "videos": metadata_videos,
        "would_write_metadata_files": not dry_run,
    }

    if not dry_run:
        write_videos_json(videos_json_path, payload)
        write_videos_csv(videos_csv_path, metadata_videos)

    print_metadata_only_summary(
        videos_json_path=videos_json_path,
        videos_csv_path=videos_csv_path,
        dry_run=dry_run,
    )
    return report


def fetch_video_metadata(video_url: str) -> dict[str, Any]:
    require_yt_dlp()
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(video_url, download=False)


def parse_langs(value: str) -> list[str]:
    parts = [part.strip() for part in value.split(",")]
    langs: list[str] = []
    for part in parts:
        if part and part not in langs:
            langs.append(part)
    if not langs:
        raise UserError("At least one language must be provided in --lang.")
    return langs


def _known_static_candidates(lang: str) -> list[str]:
    base = lang.split("-")[0]
    ordered: list[str] = []

    def add(code: str) -> None:
        if code and code not in ordered:
            ordered.append(code)

    add(lang)

    if base == "en":
        for code in ("en", "en-US", "en-GB", "en-orig"):
            add(code)
    elif base == "tr":
        for code in ("tr", "tr-TR", "tr-orig"):
            add(code)
    elif base == "pt":
        for code in ("pt", "pt-BR", "pt-PT", "pt-orig"):
            add(code)
    elif base == "zh":
        for code in ("zh", "zh-Hans", "zh-Hant", "zh-CN", "zh-TW", "zh-orig"):
            add(code)
    else:
        add(f"{lang}-orig")
        if base != lang:
            add(base)
            add(f"{base}-orig")

    return ordered


def language_candidates(lang: str, available_keys: list[str] | None = None) -> list[str]:
    lang = lang.strip()
    ordered = _known_static_candidates(lang)
    if available_keys is None:
        return ordered

    available_set = set(available_keys)
    matched: list[str] = []
    for code in ordered:
        if code in available_set and code not in matched:
            matched.append(code)

    prefix = lang.split("-")[0]
    for code in sorted(available_keys):
        if code.startswith(prefix) and code not in matched:
            matched.append(code)

    return matched


def pick_subtitle(
    subtitles: dict,
    automatic_captions: dict,
    requested_langs: list[str],
    *,
    manual_only: bool = False,
) -> tuple[str | None, str | None]:
    manual_keys = list(subtitles.keys())
    auto_keys = list(automatic_captions.keys())

    for requested in requested_langs:
        for candidate in language_candidates(requested, manual_keys):
            if candidate in subtitles:
                return candidate, "manual"

    if manual_only:
        return None, None

    for requested in requested_langs:
        for candidate in language_candidates(requested, auto_keys):
            if candidate in automatic_captions:
                return candidate, "auto"

    return None, None


def pick_english_subtitle(
    subtitles: dict,
    automatic_captions: dict,
    *,
    manual_only: bool = False,
) -> tuple[str | None, str | None]:
    return pick_subtitle(
        subtitles,
        automatic_captions,
        DEFAULT_LANGS,
        manual_only=manual_only,
    )


def subtitle_skip_reason(manual_only: bool) -> str:
    if manual_only:
        return "no_manual_requested_subtitles"
    return "no_requested_subtitles"


def subtitle_skip_message(manual_only: bool) -> str:
    if manual_only:
        return "no manual subtitles for requested language"
    return "no requested subtitles"


def find_subtitle_file(out_dir: Path, video_id: str, lang: str) -> Path | None:
    patterns = [
        f"*.{lang}.vtt",
        f"*.{lang}.srt",
        f"*.{lang}.*.vtt",
        f"*.{lang}.*.srt",
        f"{video_id}.{lang}.vtt",
        f"{video_id}.{lang}.srt",
        f"{video_id}.{lang}.*.vtt",
        f"{video_id}.{lang}.*.srt",
    ]

    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(out_dir.glob(pattern))

    if candidates:
        return sorted(set(candidates))[0]

    fallback = sorted(out_dir.glob("*.vtt")) + sorted(out_dir.glob("*.srt"))
    return fallback[0] if fallback else None


def download_subtitle(
    video_url: str,
    video_id: str,
    lang: str,
    sub_type: str,
    out_dir: Path,
) -> Path | None:
    require_yt_dlp()
    if sub_type == "manual":
        sub_opts = {"writesubtitles": True, "writeautomaticsub": False, "subtitleslangs": [lang]}
    else:
        sub_opts = {"writesubtitles": False, "writeautomaticsub": True, "subtitleslangs": [lang]}

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "skip_download": True,
        "subtitlesformat": "vtt/best",
        "outtmpl": str(out_dir / "%(id)s"),
        **sub_opts,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([video_url])

    return find_subtitle_file(out_dir, video_id, lang)


def build_txt_content(transcript: str) -> str:
    return transcript.strip() + "\n"


def build_md_content(
    title: str,
    url: str,
    upload_date: str,
    channel: str,
    video_id: str,
    transcript: str,
    *,
    timestamps: bool = False,
) -> str:
    frontmatter = [
        "---",
        f"title: {yaml_str(title)}",
        f"url: {yaml_str(url)}",
        f"upload_date: {yaml_str(upload_date)}",
        f"channel: {yaml_str(channel)}",
        f"video_id: {yaml_str(video_id)}",
    ]
    if timestamps:
        frontmatter.append("timestamps: true")
    lines = [
        *frontmatter,
        "---",
        "",
        f"# {md_heading_text(title)}",
        "",
        transcript.strip(),
        "",
    ]
    return "\n".join(lines)


def frontmatter_value_to_str(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if value is None:
        return ""
    return str(value)


def parse_md_frontmatter(md_path: Path) -> dict[str, str]:
    content = md_path.read_text(encoding="utf-8", errors="replace")
    if not content.startswith("---"):
        return {}

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}

    fields: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        try:
            fields[key] = frontmatter_value_to_str(json.loads(value))
        except json.JSONDecodeError:
            fields[key] = value.strip('"')
    return fields


def transcript_file_fields(
    output_format: OutputFormat,
    txt_path: Path,
    md_path: Path,
    base_dir: Path,
) -> dict[str, str]:
    fields: dict[str, str] = {}
    if output_format in ("both", "txt"):
        fields["txt_file"] = str(txt_path.relative_to(base_dir))
    if output_format in ("both", "md"):
        fields["md_file"] = str(md_path.relative_to(base_dir))
    return fields


def sanitize_video_id_for_filename(video_id: str) -> str:
    sanitized = re.sub(r"[^\w-]", "-", video_id)
    sanitized = re.sub(r"-+", "-", sanitized).strip("-")
    return sanitized or "unknown"


def find_txt_file_for_video_id(txt_dir: Path, video_id: str) -> Path | None:
    safe_id = sanitize_video_id_for_filename(video_id)
    candidates = [
        path
        for path in txt_dir.glob(f"*_{safe_id}.txt")
        if TXT_INDEX_RE.match(path.name)
    ]
    if not candidates:
        return None
    return sorted(candidates)[0]


def find_txt_file_by_index_prefix(txt_dir: Path, global_index: int) -> Path | None:
    prefix = f"{global_index:03d}_"
    candidates = [
        path
        for path in txt_dir.glob(f"{prefix}*.txt")
        if TXT_INDEX_RE.match(path.name)
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None


def build_existing_files_index(
    md_dir: Path,
    txt_dir: Path,
) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for md_path in md_dir.glob("*.md"):
        meta = parse_md_frontmatter(md_path)
        video_id = meta.get("video_id")
        if not video_id:
            continue

        basename = md_path.stem
        txt_path = txt_dir / f"{basename}.txt"
        match = MD_INDEX_RE.match(md_path.name)
        index[video_id] = {
            "video_id": video_id,
            "basename": basename,
            "txt_path": txt_path,
            "md_path": md_path,
            "index": int(match.group(1)) if match else 0,
            "title": meta.get("title") or basename,
        }

    for txt_path in txt_dir.glob("*.txt"):
        if not TXT_INDEX_RE.match(txt_path.name):
            continue

        basename = txt_path.stem
        md_path = md_dir / f"{basename}.md"
        if md_path.exists():
            continue

        video_id: str | None = None
        if "_" in basename:
            suffix = basename.rsplit("_", 1)[-1]
            if YOUTUBE_ID_RE.fullmatch(suffix):
                video_id = suffix

        if video_id and video_id not in index:
            match = TXT_INDEX_RE.match(txt_path.name)
            index[video_id] = {
                "video_id": video_id,
                "basename": basename,
                "txt_path": txt_path,
                "md_path": md_path,
                "index": int(match.group(1)) if match else 0,
                "title": basename,
            }

    return index


def resolve_video_action(
    video_id: str,
    global_index: int,
    proposed_basename: str,
    proposed_txt_path: Path,
    proposed_md_path: Path,
    existing_by_id: dict[str, dict[str, Any]],
    txt_dir: Path,
    force: bool,
    output_format: OutputFormat = "both",
) -> tuple[VideoAction, Path, Path, str, bool]:
    entry = existing_by_id.get(video_id)
    if entry:
        txt_path = entry["txt_path"]
        md_path = entry["md_path"]
        basename = entry["basename"]
    else:
        txt_path = proposed_txt_path
        md_path = proposed_md_path
        basename = proposed_basename

        txt_fallback = find_txt_file_for_video_id(txt_dir, video_id)
        if txt_fallback is None:
            txt_fallback = find_txt_file_by_index_prefix(txt_dir, global_index)
        if txt_fallback is not None:
            txt_path = txt_fallback
            basename = txt_fallback.stem
            md_path = md_path.parent / f"{basename}.md"

    txt_exists = txt_path.exists()
    md_exists = md_path.exists()

    if output_format == "both":
        if not force and txt_exists and md_exists:
            return "skip", txt_path, md_path, basename, False
        if not force and (txt_exists or md_exists):
            return "repair", txt_path, md_path, basename, True
        return "process", txt_path, md_path, basename, False

    if output_format == "txt":
        if not force and txt_exists:
            return "skip", txt_path, md_path, basename, False
        if not force and md_exists and not txt_exists:
            return "repair", txt_path, md_path, basename, True
        return "process", txt_path, md_path, basename, False

    if not force and md_exists:
        return "skip", txt_path, md_path, basename, False
    if not force and txt_exists and not md_exists:
        return "repair", txt_path, md_path, basename, True
    return "process", txt_path, md_path, basename, False


def enrich_report_fields(report: dict[str, Any], total_channel_videos: int) -> dict[str, Any]:
    report["total_channel_videos"] = total_channel_videos
    report["total_videos"] = total_channel_videos
    return report


def run_report_filename(
    start_index: int | None,
    end_index: int | None,
    max_videos: int | None,
    total_channel_videos: int,
) -> str:
    if start_index is not None or end_index is not None:
        start = start_index if start_index is not None else 1
        end = end_index if end_index is not None else total_channel_videos
        return f"report_{start:03d}_{end:03d}.json"
    if max_videos is not None:
        return f"report_001_{max_videos:03d}.json"
    return f"report_001_{total_channel_videos:03d}.json"


def collect_completed_from_disk(
    md_dir: Path,
    txt_dir: Path,
    output_format: OutputFormat = "both",
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    if output_format == "txt":
        for txt_path in txt_dir.glob("*.txt"):
            match = TXT_INDEX_RE.match(txt_path.name)
            if not match:
                continue

            basename = txt_path.stem
            file_index = int(match.group(1))
            md_path = md_dir / f"{basename}.md"
            meta = parse_md_frontmatter(md_path) if md_path.exists() else {}
            item: dict[str, Any] = {
                "index": file_index,
                "id": meta.get("video_id") or "",
                "title": meta.get("title") or basename,
                "url": meta.get("url") or "",
                "upload_date": meta.get("upload_date") or "",
                "basename": basename,
                "txt_file": str(txt_path.relative_to(txt_dir.parent)),
            }
            if md_path.exists():
                item["md_file"] = str(md_path.relative_to(md_dir.parent))
            items.append(item)
        return sorted(items, key=lambda row: row["index"])

    for md_path in md_dir.glob("*.md"):
        match = MD_INDEX_RE.match(md_path.name)
        if not match:
            continue

        basename = md_path.stem
        txt_path = txt_dir / f"{basename}.txt"
        if output_format == "both" and not txt_path.exists():
            continue

        file_index = int(match.group(1))
        meta = parse_md_frontmatter(md_path)
        item = {
            "index": file_index,
            "id": meta.get("video_id") or "",
            "title": meta.get("title") or basename,
            "url": meta.get("url") or "",
            "upload_date": meta.get("upload_date") or "",
            "basename": basename,
            "md_file": str(md_path.relative_to(md_dir.parent)),
        }
        if txt_path.exists():
            item["txt_file"] = str(txt_path.relative_to(txt_dir.parent))
        items.append(item)

    return sorted(items, key=lambda row: row["index"])


def build_cumulative_report(
    *,
    base_dir: Path,
    channel_url: str,
    channel_name: str,
    total_channel_videos: int,
    md_dir: Path,
    txt_dir: Path,
    reports_dir: Path,
    output_format: OutputFormat = "both",
    timestamps: bool = False,
) -> dict[str, Any]:
    completed = collect_completed_from_disk(md_dir, txt_dir, output_format)
    completed_ids = {item["id"] for item in completed if item.get("id")}

    skipped_by_id: dict[str, dict[str, Any]] = {}
    partial_repaired_count = 0
    run_report_files: list[str] = []

    for report_path in sorted(reports_dir.glob("report_*.json")):
        run_report_files.append(str(report_path.relative_to(base_dir)))
        try:
            run_report = json.loads(report_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        partial_repaired_count += sum(
            1 for item in run_report.get("processed", []) if item.get("partial_repaired")
        )

        for item in run_report.get("skipped", []):
            vid = item.get("id")
            if not vid or vid in completed_ids:
                continue
            if item.get("reason") == "already_exists":
                continue
            skipped_by_id[vid] = item

    cumulative_skipped = sorted(
        skipped_by_id.values(),
        key=lambda row: row.get("index", 0),
    )

    return {
        "channel_url": channel_url,
        "channel_name": channel_name,
        "total_channel_videos": total_channel_videos,
        "total_videos": total_channel_videos,
        "output_format": output_format,
        "timestamps": timestamps,
        "completed_count": len(completed),
        "skipped_count": len(cumulative_skipped),
        "partial_repaired_count": partial_repaired_count,
        "run_reports": run_report_files,
        "completed": completed,
        "skipped": cumulative_skipped,
        "last_updated_at": utc_now(),
        "output_path": str(base_dir),
        "cumulative_report_path": str(base_dir / "cumulative_report.json"),
    }


def collect_index_items_from_disk(
    md_dir: Path,
    txt_dir: Path,
    output_format: OutputFormat = "both",
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    if output_format == "txt":
        for txt_path in txt_dir.glob("*.txt"):
            match = TXT_INDEX_RE.match(txt_path.name)
            if not match:
                continue

            file_index = int(match.group(1))
            basename = txt_path.stem
            md_path = md_dir / f"{basename}.md"
            if md_path.exists():
                meta = parse_md_frontmatter(md_path)
                title = meta.get("title") or basename
                upload_date = meta.get("upload_date") or ""
            else:
                title = basename
                upload_date = ""
            items.append(
                {
                    "index": file_index,
                    "title": title,
                    "upload_date": upload_date,
                    "basename": basename,
                    "link_ext": "txt",
                }
            )
        return sorted(items, key=lambda row: row["index"])

    for md_path in md_dir.glob("*.md"):
        match = MD_INDEX_RE.match(md_path.name)
        if not match:
            continue

        file_index = int(match.group(1))
        basename = md_path.stem
        meta = parse_md_frontmatter(md_path)
        items.append(
            {
                "index": file_index,
                "title": meta.get("title") or basename,
                "upload_date": meta.get("upload_date") or "",
                "basename": basename,
                "link_ext": "md",
            }
        )

    return sorted(items, key=lambda row: row["index"])


def write_index_md(
    path: Path,
    channel: str,
    index_items: list[dict[str, Any]],
    output_format: OutputFormat = "both",
) -> None:
    link_ext = "txt" if output_format == "txt" else "md"
    lines = [
        f"# {channel} — Transcript Index",
        "",
        f"Total transcripts: {len(index_items)}",
        "",
        "| # | Title | Date | File |",
        "|---|-------|------|------|",
    ]
    for item in index_items:
        date = md_table_cell(item.get("upload_date") or "-")
        title = md_table_cell(item["title"])
        file_label = md_table_cell(f"{item['basename']}.{link_ext}")
        lines.append(
            f"| {item['index']:03d} | {title} | {date} | "
            f"[{file_label}]({link_ext}/{item['basename']}.{link_ext}) |"
        )
    lines.append("")
    atomic_write_text(path, "\n".join(lines))


def make_basename(index: int, title: str, video_id: str, used_basenames: set[str]) -> str:
    basename = f"{index:03d}_{slugify(title)}"
    if basename in used_basenames:
        basename = f"{basename}_{sanitize_video_id_for_filename(video_id)}"
    used_basenames.add(basename)
    return basename


def count_skip_reasons(skipped: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "existing_count": 0,
        "error_count": 0,
        "other_skipped_count": 0,
    }
    for item in skipped:
        reason = item.get("reason")
        if reason == "already_exists":
            counts["existing_count"] += 1
        elif reason == "error":
            counts["error_count"] += 1
        else:
            counts["other_skipped_count"] += 1
    return counts


def count_partial_repaired(processed: list[dict[str, Any]]) -> int:
    return sum(1 for item in processed if item.get("partial_repaired"))


def should_write_progress(
    position: int,
    progress_interval: int,
    *,
    is_last: bool = False,
    on_error: bool = False,
) -> bool:
    if on_error or is_last:
        return True
    if position == 1:
        return True
    return position % progress_interval == 0


def save_progress(
    progress_path: Path,
    *,
    channel_url: str,
    channel_name: str,
    total_channel_videos: int,
    run_videos: int,
    start_index: int | None,
    end_index: int | None,
    max_videos: int | None,
    force: bool,
    manual_only: bool,
    requested_langs: list[str],
    output_format: OutputFormat,
    timestamps: bool,
    progress_interval: int,
    retries: int,
    processed: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    last_global_index: int | None,
    run_started_at: str,
) -> None:
    skip_counts = count_skip_reasons(skipped)
    progress = {
        "channel_url": channel_url,
        "channel_name": channel_name,
        "total_channel_videos": total_channel_videos,
        "total_videos": total_channel_videos,
        "run_videos": run_videos,
        "start_index": start_index,
        "end_index": end_index,
        "max_videos": max_videos,
        "forced": force,
        "manual_only": manual_only,
        "requested_langs": requested_langs,
        "output_format": output_format,
        "timestamps": timestamps,
        "progress_interval": progress_interval,
        "retries": retries,
        "run_started_at": run_started_at,
        "last_updated_at": utc_now(),
        "last_global_index": last_global_index,
        "processed_count": len(processed),
        "skipped_count": len(skipped),
        "existing_count": skip_counts["existing_count"],
        "partial_repaired_count": count_partial_repaired(processed),
        "error_count": skip_counts["error_count"],
        "other_skipped_count": skip_counts["other_skipped_count"],
        "processed": processed,
        "skipped": skipped,
    }
    atomic_write_json(progress_path, progress)


def maybe_save_progress(
    progress_path: Path,
    *,
    position: int,
    run_videos: int,
    progress_interval: int,
    on_error: bool = False,
    **kwargs: Any,
) -> None:
    if should_write_progress(
        position,
        progress_interval,
        is_last=position == run_videos,
        on_error=on_error,
    ):
        save_progress(
            progress_path,
            progress_interval=progress_interval,
            run_videos=run_videos,
            **kwargs,
        )


def print_dry_run_summary(
    *,
    total_channel_videos: int,
    run_videos: int,
    would_process_count: int,
    would_skip_existing_count: int,
    would_repair_partial_count: int,
    output_format: OutputFormat,
    timestamps: bool,
    output_path: Path,
) -> None:
    print("\nDry run complete.")
    print(f"Total videos (channel): {total_channel_videos}")
    print(f"Videos in this run:     {run_videos}")
    print(f"Output format:          {output_format}")
    print(f"Timestamps:             {'yes' if timestamps else 'no'}")
    print(f"Would process:          {would_process_count}")
    print(f"Would skip existing:    {would_skip_existing_count}")
    print(f"Would repair partial:   {would_repair_partial_count}")
    print("Would write files:      no")
    print(f"Output:                 {output_path}")


def print_final_summary(
    *,
    total_channel_videos: int,
    run_videos: int,
    processed_count: int,
    existing_count: int,
    other_skipped_count: int,
    error_count: int,
    output_path: Path,
    report_path: Path,
    progress_path: Path,
    index_path: Path,
    cumulative_report_path: Path,
    last_run_report_path: Path,
    chunk_report_path: Path | None,
    partial_repaired_count: int,
) -> None:
    print("\nDone.")
    print(f"Total videos (channel): {total_channel_videos}")
    print(f"Videos in this run:     {run_videos}")
    print(f"Processed:              {processed_count}")
    print(f"Already existing:       {existing_count}")
    print(f"Partial repaired:       {partial_repaired_count}")
    print(f"Skipped:                {other_skipped_count}")
    print(f"Errors:                 {error_count}")
    print(f"Output:                 {output_path}")
    print(f"Report:                 {report_path}")
    print(f"Last run report:        {last_run_report_path}")
    if chunk_report_path is not None:
        print(f"Chunk report:           {chunk_report_path}")
    print(f"Cumulative report:      {cumulative_report_path}")
    print(f"Progress:               {progress_path}")
    print(f"Index:                  {index_path}")


def process_channel(
    channel_url: str,
    output_dir: Path,
    delay: float,
    max_videos: int | None = None,
    start_index: int | None = None,
    end_index: int | None = None,
    retries: int = 3,
    retry_delay: float = 2.0,
    force: bool = False,
    keep_cues: bool = False,
    dry_run: bool = False,
    manual_only: bool = False,
    requested_langs: list[str] | None = None,
    output_format: OutputFormat = "both",
    metadata_only: bool = False,
    timestamps: bool = False,
    progress_interval: int = DEFAULT_PROGRESS_INTERVAL,
) -> dict[str, Any]:
    if metadata_only:
        return process_metadata_only(
            channel_url,
            output_dir,
            max_videos=max_videos,
            start_index=start_index,
            end_index=end_index,
            dry_run=dry_run,
        )

    if requested_langs is None:
        requested_langs = list(DEFAULT_LANGS)
    channel_name, all_videos = list_channel_videos(channel_url)
    total_channel_videos = len(all_videos)

    if total_channel_videos == 0:
        raise UserError("No videos found for the provided URL.")

    selected_videos = select_videos(all_videos, max_videos, start_index, end_index)
    run_videos = len(selected_videos)

    if run_videos == 0:
        raise UserError("The selected range contains no videos.")

    channel_slug = slugify(channel_name)
    base_dir = output_dir / channel_slug
    txt_dir = base_dir / "txt"
    md_dir = base_dir / "md"
    txt_dir.mkdir(parents=True, exist_ok=True)
    md_dir.mkdir(parents=True, exist_ok=True)

    progress_path = base_dir / "progress.json"
    report_path = base_dir / "report.json"
    last_run_report_path = base_dir / "last_run_report.json"
    cumulative_report_path = base_dir / "cumulative_report.json"
    reports_dir = base_dir / "reports"
    if not dry_run:
        reports_dir.mkdir(parents=True, exist_ok=True)
    index_path = base_dir / "index.md"

    processed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    planned: list[dict[str, Any]] = []
    used_basenames: set[str] = set()
    existing_by_id = build_existing_files_index(md_dir, txt_dir)
    run_started_at = utc_now()
    last_global_index: int | None = None
    would_process_count = 0
    would_skip_existing_count = 0
    would_repair_partial_count = 0

    print(f"Channel: {channel_name}")
    print(f"Total videos (channel): {total_channel_videos}")
    print(f"Videos in this run: {run_videos}")
    if dry_run:
        print("Dry run: yes")
    print(f"Output format: {output_format}")
    if timestamps:
        print("Timestamps: yes")
    if start_index is not None or end_index is not None:
        print(f"Index range: {start_index or 1}-{end_index or total_channel_videos}")
    elif max_videos is not None:
        print(f"Max videos: {max_videos}")
    print(f"Output: {base_dir}\n")

    def write_progress(*, position: int, on_error: bool = False) -> None:
        maybe_save_progress(
            progress_path,
            position=position,
            run_videos=run_videos,
            progress_interval=progress_interval,
            on_error=on_error,
            channel_url=channel_url,
            channel_name=channel_name,
            total_channel_videos=total_channel_videos,
            start_index=start_index,
            end_index=end_index,
            max_videos=max_videos,
            force=force,
            manual_only=manual_only,
            requested_langs=requested_langs,
            output_format=output_format,
            timestamps=timestamps,
            retries=retries,
            processed=processed,
            skipped=skipped,
            last_global_index=last_global_index,
            run_started_at=run_started_at,
        )

    for position, (global_index, video) in enumerate(selected_videos, start=1):
        video_id = video["id"]
        title = video["title"]
        video_url = video["url"]
        last_global_index = global_index
        on_error = False
        print(f"[{position}/{run_videos}] (#{global_index}) {title}")

        proposed_basename = make_basename(global_index, title, video_id, used_basenames)
        proposed_txt_path = txt_dir / f"{proposed_basename}.txt"
        proposed_md_path = md_dir / f"{proposed_basename}.md"

        action, txt_path, md_path, basename, partial_repair = resolve_video_action(
            video_id,
            global_index,
            proposed_basename,
            proposed_txt_path,
            proposed_md_path,
            existing_by_id,
            txt_dir,
            force,
            output_format,
        )

        if dry_run:
            if action == "skip":
                print("  -> would skip (already exists)")
                would_skip_existing_count += 1
                planned.append(
                    {
                        "index": global_index,
                        "id": video_id,
                        "title": title,
                        "url": video_url,
                        "action": "skip",
                        "reason": "already_exists",
                        "basename": basename,
                    }
                )
            elif action == "repair":
                print("  -> would repair partial files")
                would_repair_partial_count += 1
                planned.append(
                    {
                        "index": global_index,
                        "id": video_id,
                        "title": title,
                        "url": video_url,
                        "action": "repair",
                        "basename": basename,
                    }
                )
            else:
                print("  -> would process")
                would_process_count += 1
                planned.append(
                    {
                        "index": global_index,
                        "id": video_id,
                        "title": title,
                        "url": video_url,
                        "action": "process",
                        "basename": basename,
                        "forced": force,
                    }
                )
            continue

        if action == "skip":
            print("  -> skipped (already exists)")
            skip_entry: dict[str, Any] = {
                "index": global_index,
                "id": video_id,
                "title": title,
                "url": video_url,
                "reason": "already_exists",
                "basename": basename,
            }
            skip_entry.update(
                transcript_file_fields(output_format, txt_path, md_path, base_dir)
            )
            skipped.append(skip_entry)
            write_progress(position=position)
            if delay > 0:
                time.sleep(delay)
            continue

        if action == "repair":
            print("  -> repairing partial files")

        try:
            metadata = retry_call(
                lambda: fetch_video_metadata(video_url),
                attempts=retries,
                delay=retry_delay,
                action="Fetching video metadata",
            )
            raw_upload_date = metadata.get("upload_date") or video.get("upload_date")
            upload_date = format_upload_date(raw_upload_date)
            resolved_title = metadata.get("title") or title
            resolved_channel = metadata.get("channel") or metadata.get("uploader") or channel_name

            subtitles = metadata.get("subtitles") or {}
            automatic_captions = metadata.get("automatic_captions") or {}
            lang, sub_type = pick_subtitle(
                subtitles,
                automatic_captions,
                requested_langs,
                manual_only=manual_only,
            )

            if not lang:
                print(f"  -> skipped ({subtitle_skip_message(manual_only)})")
                skipped.append(
                    {
                        "index": global_index,
                        "id": video_id,
                        "title": resolved_title,
                        "url": video_url,
                        "reason": subtitle_skip_reason(manual_only),
                    }
                )
                write_progress(position=position)
                if delay > 0:
                    time.sleep(delay)
                continue

            transcript = ""
            sub_path = None
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)

                def download_with_retry() -> Path | None:
                    return download_subtitle(video_url, video_id, lang, sub_type, tmp_path)

                sub_path = retry_call(
                    download_with_retry,
                    attempts=retries,
                    delay=retry_delay,
                    action="Downloading subtitles",
                )
                if sub_path:
                    transcript = parse_subtitle_file(
                        sub_path,
                        keep_cues=keep_cues,
                        timestamps=timestamps,
                    )

            if not sub_path:
                print(f"  -> skipped (failed to download {sub_type} subtitles)")
                skipped.append(
                    {
                        "index": global_index,
                        "id": video_id,
                        "title": resolved_title,
                        "url": video_url,
                        "reason": "subtitle_download_failed",
                    }
                )
                write_progress(position=position)
                if delay > 0:
                    time.sleep(delay)
                continue

            if not transcript.strip():
                print("  -> skipped (empty transcript)")
                skipped.append(
                    {
                        "index": global_index,
                        "id": video_id,
                        "title": resolved_title,
                        "url": video_url,
                        "reason": "empty_transcript",
                    }
                )
                write_progress(position=position)
                if delay > 0:
                    time.sleep(delay)
                continue

            if output_format in ("both", "txt"):
                atomic_write_text(txt_path, build_txt_content(transcript))
            if output_format in ("both", "md"):
                atomic_write_text(
                    md_path,
                    build_md_content(
                        title=resolved_title,
                        url=video_url,
                        upload_date=upload_date,
                        channel=resolved_channel,
                        video_id=video_id,
                        transcript=transcript,
                        timestamps=timestamps,
                    ),
                )

            existing_by_id[video_id] = {
                "video_id": video_id,
                "basename": basename,
                "txt_path": txt_path,
                "md_path": md_path,
                "index": global_index,
                "title": resolved_title,
            }

            processed_entry: dict[str, Any] = {
                "index": global_index,
                "id": video_id,
                "title": resolved_title,
                "url": video_url,
                "upload_date": upload_date,
                "basename": basename,
                "subtitle_lang": lang,
                "subtitle_type": sub_type,
            }
            processed_entry.update(
                transcript_file_fields(output_format, txt_path, md_path, base_dir)
            )
            if partial_repair:
                processed_entry["partial_repaired"] = True
            processed.append(processed_entry)
            if partial_repair:
                print(f"  -> repaired ({sub_type}, {lang})")
            else:
                print(f"  -> saved ({sub_type}, {lang})")

        except RetryError as exc:
            on_error = True
            print(f"  -> error: {exc}")
            skipped.append(
                {
                    "index": global_index,
                    "id": video_id,
                    "title": title,
                    "url": video_url,
                    "reason": "error",
                    "error": str(exc),
                }
            )
        except Exception as exc:
            on_error = True
            print(f"  -> error: {exc}")
            skipped.append(
                {
                    "index": global_index,
                    "id": video_id,
                    "title": title,
                    "url": video_url,
                    "reason": "error",
                    "error": str(exc),
                }
            )

        write_progress(position=position, on_error=on_error)

        if delay > 0 and not dry_run:
            time.sleep(delay)

    if dry_run:
        report: dict[str, Any] = {
            "dry_run": True,
            "channel_url": channel_url,
            "channel_name": channel_name,
            "run_videos": run_videos,
            "start_index": start_index,
            "end_index": end_index,
            "max_videos": max_videos,
            "forced": force,
            "keep_cues": keep_cues,
            "manual_only": manual_only,
            "requested_langs": requested_langs,
            "output_format": output_format,
            "timestamps": timestamps,
            "would_process_count": would_process_count,
            "would_skip_existing_count": would_skip_existing_count,
            "would_repair_partial_count": would_repair_partial_count,
            "would_write_files": False,
            "run_started_at": run_started_at,
            "run_finished_at": utc_now(),
            "output_path": str(base_dir),
            "planned": planned,
        }
        enrich_report_fields(report, total_channel_videos)
        print_dry_run_summary(
            total_channel_videos=total_channel_videos,
            run_videos=run_videos,
            would_process_count=would_process_count,
            would_skip_existing_count=would_skip_existing_count,
            would_repair_partial_count=would_repair_partial_count,
            output_format=output_format,
            timestamps=timestamps,
            output_path=base_dir,
        )
        return report

    skip_counts = count_skip_reasons(skipped)
    partial_repaired_count = count_partial_repaired(processed)
    index_items = collect_index_items_from_disk(md_dir, txt_dir, output_format)

    report: dict[str, Any] = {
        "channel_url": channel_url,
        "channel_name": channel_name,
        "run_videos": run_videos,
        "start_index": start_index,
        "end_index": end_index,
        "max_videos": max_videos,
        "processed_count": len(processed),
        "skipped_count": len(skipped),
        "existing_count": skip_counts["existing_count"],
        "partial_repaired_count": partial_repaired_count,
        "error_count": skip_counts["error_count"],
        "other_skipped_count": skip_counts["other_skipped_count"],
        "forced": force,
        "keep_cues": keep_cues,
        "manual_only": manual_only,
        "requested_langs": requested_langs,
        "output_format": output_format,
        "timestamps": timestamps,
        "progress_interval": progress_interval,
        "retries": retries,
        "run_started_at": run_started_at,
        "run_finished_at": utc_now(),
        "output_path": str(base_dir),
        "report_path": str(report_path),
        "last_run_report_path": str(last_run_report_path),
        "cumulative_report_path": str(cumulative_report_path),
        "progress_path": str(progress_path),
        "index_path": str(index_path),
        "processed": processed,
        "skipped": skipped,
    }
    enrich_report_fields(report, total_channel_videos)

    chunk_report_name = run_report_filename(
        start_index, end_index, max_videos, total_channel_videos
    )
    chunk_report_path = reports_dir / chunk_report_name
    report["chunk_report_path"] = str(chunk_report_path.relative_to(base_dir))

    atomic_write_json(chunk_report_path, report)
    atomic_write_json(last_run_report_path, report)
    atomic_write_json(report_path, report)

    cumulative_report = build_cumulative_report(
        base_dir=base_dir,
        channel_url=channel_url,
        channel_name=channel_name,
        total_channel_videos=total_channel_videos,
        md_dir=md_dir,
        txt_dir=txt_dir,
        reports_dir=reports_dir,
        output_format=output_format,
        timestamps=timestamps,
    )
    atomic_write_json(cumulative_report_path, cumulative_report)
    write_index_md(index_path, channel_name, index_items, output_format)

    print_final_summary(
        total_channel_videos=total_channel_videos,
        run_videos=run_videos,
        processed_count=len(processed),
        existing_count=skip_counts["existing_count"],
        other_skipped_count=skip_counts["other_skipped_count"],
        error_count=skip_counts["error_count"],
        output_path=base_dir,
        report_path=report_path,
        progress_path=progress_path,
        index_path=index_path,
        cumulative_report_path=cumulative_report_path,
        last_run_report_path=last_run_report_path,
        chunk_report_path=chunk_report_path,
        partial_repaired_count=partial_repaired_count,
    )

    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download transcripts from a YouTube channel and save as txt/md.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {VERSION}",
    )
    parser.add_argument("channel_url", help="YouTube channel, playlist, or video URL")
    parser.add_argument(
        "-o",
        "--output",
        default="./output",
        help="Output directory (default: ./output)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay in seconds between videos (default: 0.5)",
    )
    parser.add_argument(
        "--max-videos",
        type=int,
        default=None,
        help="Process only the first N videos (useful for testing)",
    )
    parser.add_argument(
        "--start-index",
        type=int,
        default=None,
        help="1-based start index within the full channel list (for chunked runs)",
    )
    parser.add_argument(
        "--end-index",
        type=int,
        default=None,
        help="1-based end index within the full channel list (inclusive)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Retry attempts for metadata/subtitle fetch (default: 3)",
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=2.0,
        help="Delay in seconds between retries (default: 2.0)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing transcript files",
    )
    parser.add_argument(
        "--keep-cues",
        action="store_true",
        help="Preserve bracketed subtitle cues like [Music] and [Applause]",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview planned actions without downloading subtitles or writing files",
    )
    parser.add_argument(
        "--manual-only",
        action="store_true",
        help="Use only manual subtitles for requested languages; no auto caption fallback",
    )
    parser.add_argument(
        "--lang",
        default="en",
        help="Comma-separated subtitle language priority list (default: en)",
    )
    parser.add_argument(
        "--format",
        choices=["both", "txt", "md"],
        default=DEFAULT_OUTPUT_FORMAT,
        help="Output transcript format: both, txt, or md (default: both)",
    )
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="Write selected video list metadata without downloading transcripts",
    )
    parser.add_argument(
        "--timestamps",
        action="store_true",
        help="Include subtitle cue start timestamps in transcript output",
    )
    parser.add_argument(
        "--progress-interval",
        type=int,
        default=DEFAULT_PROGRESS_INTERVAL,
        help=(
            "Write progress.json every N videos (default: "
            f"{DEFAULT_PROGRESS_INTERVAL}); also on first, last, and errors"
        ),
    )
    args = parser.parse_args()

    if args.max_videos is not None and args.max_videos <= 0:
        parser.error("--max-videos must be a positive integer")
    if args.max_videos is not None and (
        args.start_index is not None or args.end_index is not None
    ):
        parser.error("--max-videos cannot be combined with --start-index/--end-index")
    if args.start_index is not None and args.start_index <= 0:
        parser.error("--start-index must be a positive integer")
    if args.end_index is not None and args.end_index <= 0:
        parser.error("--end-index must be a positive integer")
    if (
        args.start_index is not None
        and args.end_index is not None
        and args.end_index < args.start_index
    ):
        parser.error("--end-index must be greater than or equal to --start-index")
    if args.delay < 0:
        parser.error("--delay must not be negative")
    if args.retries < 1:
        parser.error("--retries must be at least 1")
    if args.retry_delay < 0:
        parser.error("--retry-delay must not be negative")
    if args.progress_interval < 1:
        parser.error("--progress-interval must be at least 1")

    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        require_yt_dlp()
        requested_langs = parse_langs(args.lang)
        process_channel(
            args.channel_url,
            output_dir,
            args.delay,
            args.max_videos,
            args.start_index,
            args.end_index,
            args.retries,
            args.retry_delay,
            args.force,
            args.keep_cues,
            args.dry_run,
            args.manual_only,
            requested_langs,
            args.format,
            args.metadata_only,
            args.timestamps,
            args.progress_interval,
        )
    except UserError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

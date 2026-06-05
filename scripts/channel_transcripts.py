#!/usr/bin/env python3
"""Download English subtitles from a YouTube channel and convert to txt/md."""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import tempfile
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, TypeVar

try:
    import yt_dlp
except ImportError as exc:
    yt_dlp = None  # type: ignore[assignment]
    _YT_DLP_IMPORT_ERROR = exc
else:
    _YT_DLP_IMPORT_ERROR = None

VERSION = "1.0.0"

EN_SUB_LANGS = ["en", "en-US", "en-GB", "en-orig"]
CHANNEL_TAB_SUFFIXES = ("/videos", "/shorts", "/streams", "/playlists", "/featured", "/about")
SUBTITLE_CUE_PATTERNS = [
    re.compile(r"\[Music\]", re.IGNORECASE),
    re.compile(r"\[Applause\]", re.IGNORECASE),
    re.compile(r"\[Laughter\]", re.IGNORECASE),
    re.compile(r"\[applause and cheering\]", re.IGNORECASE),
    re.compile(r"\[Submit subtitle corrections at[^\]]*\]", re.IGNORECASE),
]
MD_INDEX_RE = re.compile(r"^(\d+)_.+\.md$")

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
    url = url.strip().rstrip("/")
    if "watch?v=" in url or "playlist?list=" in url:
        return url
    if "/@" in url and not any(url.endswith(suffix) for suffix in CHANNEL_TAB_SUFFIXES):
        return f"{url}/videos"
    if "/channel/" in url and not any(url.endswith(suffix) for suffix in CHANNEL_TAB_SUFFIXES):
        return f"{url}/videos"
    return url


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
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def retry_call(fn: Callable[[], T], attempts: int, delay: float, action: str) -> T:
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt < attempts - 1:
                time.sleep(delay)
    assert last_exc is not None
    raise RetryError(
        f"{action} failed after {attempts} attempt(s): {last_exc}"
    ) from last_exc


def parse_subtitle_file(path: Path, *, keep_cues: bool = False) -> str:
    content = path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("WEBVTT") or line.startswith("NOTE"):
            continue
        if re.match(r"^\d+$", line):
            continue
        if re.match(r"^\d{2}:\d{2}:\d{2}[.,]\d{3}\s+-->", line):
            continue
        if re.match(r"^\d{2}:\d{2}[.,]\d{3}\s+-->", line):
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


def fetch_video_metadata(video_url: str) -> dict[str, Any]:
    require_yt_dlp()
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(video_url, download=False)


def pick_english_subtitle(
    subtitles: dict,
    automatic_captions: dict,
) -> tuple[str | None, str | None]:
    for lang in EN_SUB_LANGS:
        if lang in subtitles:
            return lang, "manual"

    for lang in EN_SUB_LANGS:
        if lang in automatic_captions:
            return lang, "auto"

    for lang in subtitles:
        if lang.startswith("en"):
            return lang, "manual"

    for lang in automatic_captions:
        if lang.startswith("en"):
            return lang, "auto"

    return None, None


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
) -> str:
    lines = [
        "---",
        f"title: {yaml_str(title)}",
        f"url: {yaml_str(url)}",
        f"upload_date: {yaml_str(upload_date)}",
        f"channel: {yaml_str(channel)}",
        f"video_id: {yaml_str(video_id)}",
        "---",
        "",
        f"# {title}",
        "",
        transcript.strip(),
        "",
    ]
    return "\n".join(lines)


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
            fields[key] = json.loads(value)
        except json.JSONDecodeError:
            fields[key] = value.strip('"')
    return fields


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
    return index


def resolve_video_action(
    video_id: str,
    proposed_basename: str,
    proposed_txt_path: Path,
    proposed_md_path: Path,
    existing_by_id: dict[str, dict[str, Any]],
    force: bool,
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

    txt_exists = txt_path.exists()
    md_exists = md_path.exists()

    if not force and txt_exists and md_exists:
        return "skip", txt_path, md_path, basename, False
    if not force and (txt_exists or md_exists):
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


def collect_completed_from_disk(md_dir: Path, txt_dir: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for md_path in md_dir.glob("*.md"):
        match = MD_INDEX_RE.match(md_path.name)
        if not match:
            continue

        basename = md_path.stem
        txt_path = txt_dir / f"{basename}.txt"
        if not txt_path.exists():
            continue

        file_index = int(match.group(1))
        meta = parse_md_frontmatter(md_path)
        items.append(
            {
                "index": file_index,
                "id": meta.get("video_id") or "",
                "title": meta.get("title") or basename,
                "url": meta.get("url") or "",
                "upload_date": meta.get("upload_date") or "",
                "basename": basename,
                "txt_file": str(txt_path.relative_to(txt_dir.parent)),
                "md_file": str(md_path.relative_to(md_dir.parent)),
            }
        )

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
) -> dict[str, Any]:
    completed = collect_completed_from_disk(md_dir, txt_dir)
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


def collect_index_items_from_disk(md_dir: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
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
            }
        )

    return sorted(items, key=lambda row: row["index"])


def write_index_md(path: Path, channel: str, index_items: list[dict[str, Any]]) -> None:
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
        file_label = md_table_cell(f"{item['basename']}.md")
        lines.append(
            f"| {item['index']:03d} | {title} | {date} | [{file_label}](md/{item['basename']}.md) |"
        )
    lines.append("")
    atomic_write_text(path, "\n".join(lines))


def make_basename(index: int, title: str, video_id: str, used_basenames: set[str]) -> str:
    basename = f"{index:03d}_{slugify(title)}"
    if basename in used_basenames:
        basename = f"{basename}_{video_id}"
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
) -> dict[str, Any]:
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
    reports_dir.mkdir(parents=True, exist_ok=True)
    index_path = base_dir / "index.md"

    processed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    used_basenames: set[str] = set()
    existing_by_id = build_existing_files_index(md_dir, txt_dir)
    run_started_at = utc_now()
    last_global_index: int | None = None

    print(f"Channel: {channel_name}")
    print(f"Total videos (channel): {total_channel_videos}")
    print(f"Videos in this run: {run_videos}")
    if start_index is not None or end_index is not None:
        print(f"Index range: {start_index or 1}-{end_index or total_channel_videos}")
    elif max_videos is not None:
        print(f"Max videos: {max_videos}")
    print(f"Output: {base_dir}\n")

    for position, (global_index, video) in enumerate(selected_videos, start=1):
        video_id = video["id"]
        title = video["title"]
        video_url = video["url"]
        last_global_index = global_index
        print(f"[{position}/{run_videos}] (#{global_index}) {title}")

        proposed_basename = make_basename(global_index, title, video_id, used_basenames)
        proposed_txt_path = txt_dir / f"{proposed_basename}.txt"
        proposed_md_path = md_dir / f"{proposed_basename}.md"

        action, txt_path, md_path, basename, partial_repair = resolve_video_action(
            video_id,
            proposed_basename,
            proposed_txt_path,
            proposed_md_path,
            existing_by_id,
            force,
        )

        if action == "skip":
            print("  -> skipped (already exists)")
            skipped.append(
                {
                    "index": global_index,
                    "id": video_id,
                    "title": title,
                    "url": video_url,
                    "reason": "already_exists",
                    "basename": basename,
                    "txt_file": str(txt_path.relative_to(base_dir)),
                    "md_file": str(md_path.relative_to(base_dir)),
                }
            )
            save_progress(
                progress_path,
                channel_url=channel_url,
                channel_name=channel_name,
                total_channel_videos=total_channel_videos,
                run_videos=run_videos,
                start_index=start_index,
                end_index=end_index,
                max_videos=max_videos,
                force=force,
                retries=retries,
                processed=processed,
                skipped=skipped,
                last_global_index=last_global_index,
                run_started_at=run_started_at,
            )
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
            lang, sub_type = pick_english_subtitle(subtitles, automatic_captions)

            if not lang:
                print("  -> skipped (no English subtitles)")
                skipped.append(
                    {
                        "index": global_index,
                        "id": video_id,
                        "title": resolved_title,
                        "url": video_url,
                        "reason": "no_english_subtitles",
                    }
                )
                save_progress(
                    progress_path,
                    channel_url=channel_url,
                    channel_name=channel_name,
                    total_channel_videos=total_channel_videos,
                    run_videos=run_videos,
                    start_index=start_index,
                    end_index=end_index,
                    max_videos=max_videos,
                    force=force,
                    retries=retries,
                    processed=processed,
                    skipped=skipped,
                    last_global_index=last_global_index,
                    run_started_at=run_started_at,
                )
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
                    transcript = parse_subtitle_file(sub_path, keep_cues=keep_cues)

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
                save_progress(
                    progress_path,
                    channel_url=channel_url,
                    channel_name=channel_name,
                    total_channel_videos=total_channel_videos,
                    run_videos=run_videos,
                    start_index=start_index,
                    end_index=end_index,
                    max_videos=max_videos,
                    force=force,
                    retries=retries,
                    processed=processed,
                    skipped=skipped,
                    last_global_index=last_global_index,
                    run_started_at=run_started_at,
                )
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
                save_progress(
                    progress_path,
                    channel_url=channel_url,
                    channel_name=channel_name,
                    total_channel_videos=total_channel_videos,
                    run_videos=run_videos,
                    start_index=start_index,
                    end_index=end_index,
                    max_videos=max_videos,
                    force=force,
                    retries=retries,
                    processed=processed,
                    skipped=skipped,
                    last_global_index=last_global_index,
                    run_started_at=run_started_at,
                )
                if delay > 0:
                    time.sleep(delay)
                continue

            atomic_write_text(txt_path, build_txt_content(transcript))
            atomic_write_text(
                md_path,
                build_md_content(
                    title=resolved_title,
                    url=video_url,
                    upload_date=upload_date,
                    channel=resolved_channel,
                    video_id=video_id,
                    transcript=transcript,
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
                "txt_file": str(txt_path.relative_to(base_dir)),
                "md_file": str(md_path.relative_to(base_dir)),
            }
            if partial_repair:
                processed_entry["partial_repaired"] = True
            processed.append(processed_entry)
            if partial_repair:
                print(f"  -> repaired ({sub_type}, {lang})")
            else:
                print(f"  -> saved ({sub_type}, {lang})")

        except RetryError as exc:
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

        save_progress(
            progress_path,
            channel_url=channel_url,
            channel_name=channel_name,
            total_channel_videos=total_channel_videos,
            run_videos=run_videos,
            start_index=start_index,
            end_index=end_index,
            max_videos=max_videos,
            force=force,
            retries=retries,
            processed=processed,
            skipped=skipped,
            last_global_index=last_global_index,
            run_started_at=run_started_at,
        )

        if delay > 0:
            time.sleep(delay)

    skip_counts = count_skip_reasons(skipped)
    partial_repaired_count = count_partial_repaired(processed)
    index_items = collect_index_items_from_disk(md_dir)

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
    )
    atomic_write_json(cumulative_report_path, cumulative_report)
    write_index_md(index_path, channel_name, index_items)

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
        description="Download English transcripts from a YouTube channel and save as txt/md.",
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
    args = parser.parse_args()

    if args.max_videos is not None and args.max_videos <= 0:
        parser.error("--max-videos must be a positive integer")
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

    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        require_yt_dlp()
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

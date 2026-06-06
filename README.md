# YouTube Channel Transcripts

[![CI](https://github.com/aliyasinozyuksel/yt-dlp-video-transcripts/actions/workflows/ci.yml/badge.svg)](https://github.com/aliyasinozyuksel/yt-dlp-video-transcripts/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

Download subtitles/transcripts from YouTube channels, playlists, or individual videos and save them as plain `.txt` and agent-friendly `.md` files. English is the default language, but you can request any YouTube/yt-dlp subtitle language code with `--lang`.

This tool uses [yt-dlp](https://github.com/yt-dlp/yt-dlp) to fetch subtitles only. It does **not** download video or audio files.

## What it does

- Lists videos from a channel, playlist, or single video URL
- Downloads manual subtitles first, then auto-generated captions for your requested language(s)
- Defaults to English (`--lang en`); accepts any valid YouTube subtitle language code
- Converts VTT/SRT subtitles into clean transcript text
- Writes both `.txt` and `.md` outputs with YAML frontmatter
- Supports chunked processing for large channels (600+ videos)
- Resumes safely by skipping completed transcript pairs
- Repairs partial transcript files after interrupted runs
- Detects existing transcripts by `video_id`, not just filename

## Quick start

```bash
git clone https://github.com/aliyasinozyuksel/yt-dlp-video-transcripts.git
cd yt-dlp-video-transcripts
pip install -r requirements.txt

python scripts/channel_transcripts.py \
  "https://www.youtube.com/@3blue1brown/videos" \
  -o ./output --max-videos 5
```

Preview actions without downloading or writing files:

```bash
python scripts/channel_transcripts.py \
  "https://www.youtube.com/@3blue1brown/videos" \
  -o ./output --max-videos 5 --dry-run
```

## Installation

```bash
pip install yt-dlp
```

Or:

```bash
pip install -r requirements.txt
```

For development:

```bash
pip install -r requirements-dev.txt
```

## Usage

### Single video

```bash
python scripts/channel_transcripts.py \
  "https://www.youtube.com/watch?v=VIDEO_ID" \
  -o ./output
```

### Channel

```bash
python scripts/channel_transcripts.py \
  "https://www.youtube.com/@channel/videos" \
  -o ./output
```

Bare `@channel` URLs automatically use the `/videos` tab.

### Playlist

```bash
python scripts/channel_transcripts.py \
  "https://www.youtube.com/playlist?list=PLAYLIST_ID" \
  -o ./output
```

### Large channel (600+ videos)

Process in chunks using global channel indices:

```bash
python scripts/channel_transcripts.py \
  "https://www.youtube.com/@channel/videos" \
  -o ./output --start-index 1 --end-index 100

python scripts/channel_transcripts.py \
  "https://www.youtube.com/@channel/videos" \
  -o ./output --start-index 101 --end-index 200
```

### Resume after interruption

Re-run the same command. Completed `.txt` + `.md` pairs are skipped automatically:

```bash
python scripts/channel_transcripts.py \
  "https://www.youtube.com/@channel/videos" \
  -o ./output --start-index 1 --end-index 100
```

If only one of the pair exists, the script repairs it and rewrites both files.

### Force overwrite

```bash
python scripts/channel_transcripts.py \
  "https://www.youtube.com/@channel/videos" \
  -o ./output --force
```

### Preserve subtitle cues

By default, non-speech cues like `[Music]` and `[Applause]` are removed. To keep them:

```bash
python scripts/channel_transcripts.py \
  "https://www.youtube.com/@channel/videos" \
  -o ./output --keep-cues
```

### Language selection

`--lang` accepts a comma-separated priority list. For each video, the tool downloads **one** transcript using the first matching language in your list — it does not download multiple transcripts per video.

Priority order for `--lang en,tr`:

1. Manual English candidates
2. Manual Turkish candidates
3. Auto English candidates (unless `--manual-only`)
4. Auto Turkish candidates

Examples:

```bash
python scripts/channel_transcripts.py \
  "https://www.youtube.com/@channel/videos" \
  -o ./output --lang tr
```

```bash
python scripts/channel_transcripts.py \
  "https://www.youtube.com/@channel/videos" \
  -o ./output --lang tr,en
```

Regional variants are handled automatically (for example `en` also tries `en-US`, `en-GB`; `pt` tries `pt-BR` and `pt-PT`; `zh` tries `zh-Hans`, `zh-Hant`, and related codes).

### Manual subtitles only

By default, the tool uses manual subtitles first, then falls back to auto-generated captions for the requested languages. With `--manual-only`, auto captions are ignored and videos without manual subtitles in any requested language are skipped.

```bash
python scripts/channel_transcripts.py \
  "https://www.youtube.com/@channel/videos" \
  -o ./output --manual-only
```

Combine with `--lang` to restrict manual-only mode to specific languages:

```bash
python scripts/channel_transcripts.py \
  "https://www.youtube.com/@channel/videos" \
  -o ./output --lang tr,en --manual-only
```

### Output format

Choose which transcript file types to write with `--format`:

```bash
python scripts/channel_transcripts.py \
  "https://www.youtube.com/@channel/videos" \
  -o ./output --format md
```

- `both` (default) — write both `.txt` and `.md` transcript files
- `txt` — write only plain-text `.txt` transcripts
- `md` — write only Markdown `.md` transcripts with YAML frontmatter

`index.md` and JSON reports are still written as project metadata files; they are not controlled by `--format`.

Resume behavior depends on the requested format:

- `both` — complete when both `.txt` and `.md` exist; partial files are repaired
- `txt` — complete when `.txt` exists (`.md` is not required)
- `md` — complete when `.md` exists (`.txt` is not required)

### Timestamped transcripts

Include cue start timestamps in transcript output:

```bash
python scripts/channel_transcripts.py \
  "https://www.youtube.com/@channel/videos" \
  -o ./output --timestamps
```

Each subtitle cue is written on its own line with the cue start time:

```text
[00:00:01] Hello everyone
[00:00:05] Today we are going to talk about...
```

Works with `--format txt`, `--format md`, and `--format both`. Markdown output includes `timestamps: true` in the YAML frontmatter when enabled.

If transcript files already exist, resume will skip them. Use `--force` or a separate output directory to regenerate existing transcripts with timestamps.

### Metadata only

List selected videos without downloading subtitles or writing transcript files:

```bash
python scripts/channel_transcripts.py \
  "https://www.youtube.com/@channel/videos" \
  -o ./output --metadata-only
```

This uses yt-dlp flat extraction to list videos and writes:

- `videos.json` — structured metadata for the selected videos
- `videos.csv` — spreadsheet-friendly export with `index`, `id`, `title`, `url`, `upload_date` (cells starting with `=`, `+`, `-`, or `@` are prefixed for spreadsheet safety)

Useful before processing large channels to inspect or plan chunked runs. Combine with `--max-videos`, `--start-index`, or `--end-index` to limit the selection.

With `--metadata-only --dry-run`, videos are listed but `videos.json` and `videos.csv` are not written.

### Dry run (preview without downloading)

Preview what the script would do without fetching subtitles or writing transcript/report files:

```bash
python scripts/channel_transcripts.py \
  "https://www.youtube.com/@channel/videos" \
  -o ./output --max-videos 5 --dry-run
```

Dry run still lists videos and checks existing transcript files by `video_id`. It prints planned actions such as `would process`, `would skip (already exists)`, and `would repair partial files`.

### Version

```bash
python scripts/channel_transcripts.py --version
```

## Example output

### Normal run

```text
Channel: 3Blue1Brown
Total videos (channel): 149
Videos in this run: 2
Output: ./output/3blue1brown

[1/2] (#1) How (and why) to take a logarithm of an image
  -> saved (manual, en)
[2/2] (#2) The most beautiful formula not enough people understand
  -> saved (auto, en)

Done.
Processed:              2
Already existing:       0
Skipped:                0
Errors:                 0
```

### Dry run

```text
Channel: 3Blue1Brown
Total videos (channel): 149
Videos in this run: 3
Dry run: yes

[1/3] (#1) How (and why) to take a logarithm of an image
  -> would skip (already exists)
[2/3] (#2) The most beautiful formula not enough people understand
  -> would skip (already exists)
[3/3] (#3) The Hairy Ball Theorem
  -> would process

Dry run complete.
Output format:          both
Would process:          1
Would skip existing:    2
Would repair partial:   0
Would write files:      no
```

## CLI options

| Flag | Default | Description |
|------|---------|-------------|
| `-o`, `--output` | `./output` | Output directory |
| `--delay` | `0.5` | Seconds to wait between videos |
| `--max-videos` | all | Process only the first N videos (quick tests) |
| `--start-index` | — | 1-based start index in the full channel list |
| `--end-index` | — | 1-based end index in the full channel list (inclusive) |
| `--retries` | `3` | Retry attempts for metadata/subtitle fetch |
| `--retry-delay` | `2.0` | Seconds between retries |
| `--force` | off | Overwrite existing transcript files |
| `--keep-cues` | off | Preserve bracketed subtitle cues |
| `--dry-run` | off | Preview planned actions; no downloads or file writes |
| `--lang` | `en` | Comma-separated subtitle language priority list |
| `--format` | `both` | Output transcript format: `both`, `txt`, or `md` |
| `--timestamps` | off | Include subtitle cue start timestamps in transcript output |
| `--progress-interval` | `10` | Write `progress.json` every N videos (also first, last, and errors) |
| `--metadata-only` | off | Write selected video list metadata without downloading transcripts |
| `--manual-only` | off | Use only manual subtitles for requested languages; no auto caption fallback |
| `--version` | — | Show version and exit |

`--max-videos` cannot be combined with `--start-index` or `--end-index`. Use one selection method per run.

## Output structure

```
output/
  channel-slug/
    txt/
      001_video-title.txt
      101_another-video.txt
    md/
      001_video-title.md
      101_another-video.md
    videos.json
    videos.csv
    index.md
    progress.json
    report.json
    last_run_report.json
    cumulative_report.json
    reports/
      report_001_100.json
      report_101_200.json
```

- **videos.json** — selected video list metadata (written with `--metadata-only`)
- **videos.csv** — CSV export of selected videos (written with `--metadata-only`); formula-like cells are prefixed to reduce spreadsheet injection risk
- **txt/** — plain transcript text (written with `--format both` or `--format txt`)
- **md/** — Markdown with YAML frontmatter (`title`, `url`, `upload_date`, `channel`, `video_id`; written with `--format both` or `--format md`)
- **index.md** — table of all transcripts found on disk
- **progress.json** — live run state; written on the first video, every `--progress-interval` videos (default 10), on errors, and at the end of a run
- **report.json** — latest run summary (`total_videos` + `total_channel_videos`)
- **last_run_report.json** — copy of the latest run report
- **cumulative_report.json** — merged progress across all chunks
- **reports/** — archived per-chunk reports that are not overwritten by later chunks

## Resume behavior

- Existing transcripts are detected by `video_id` from Markdown frontmatter
- With `--format both`, if both `.txt` and `.md` exist for a `video_id`, the video is skipped unless `--force` is used
- With `--format txt`, only `.txt` is required for resume; with `--format md`, only `.md` is required
- Partial files are repaired automatically according to the requested format
- This avoids duplicate files when a video title changes

## Channel tabs

Pass the tab you want explicitly:

- `https://www.youtube.com/@channel/videos`
- `https://www.youtube.com/@channel/shorts`
- `https://www.youtube.com/@channel/streams`

If you process multiple tabs into the same output folder, numbering can collide. Use separate output directories per tab.

## Limitations and safety notes

- **Metadata-only** uses yt-dlp flat extraction, so `upload_date` may be empty in `videos.json` / `videos.csv`.
- **Resume** does not overwrite existing transcript files unless you pass `--force` or use a separate output directory.
- **`--lang`** is a priority list and downloads one transcript per video, not multiple languages per video.
- **`videos.csv`** prefixes spreadsheet formula-like cells (`=`, `+`, `-`, `@`) to reduce CSV injection risk when opened in Excel or similar apps.

## Development

```bash
python -m pytest
python -m compileall scripts
python -m ruff check .
```

## Troubleshooting

| Problem | What to do |
|---------|------------|
| `yt-dlp is not installed` | Run `pip install yt-dlp` |
| `No videos found for the provided URL` | Check the URL and make sure the channel/playlist is public |
| `The selected range contains no videos` | Adjust `--start-index` / `--end-index` |
| `skipped (no requested subtitles)` | That video has no manual or auto captions in any requested language |
| `skipped (no manual subtitles for requested language)` | With `--manual-only`, no manual subtitles matched the requested languages |
| `Fetching video metadata failed after N attempt(s)` | Retry later or increase `--retries` and `--retry-delay` |
| Interrupted run | Re-run the same command; completed files are skipped, partial files are repaired |

## Legal and copyright

Only download and use transcripts in ways you have the rights or permission for.

- Respect YouTube's Terms of Service and applicable copyright law
- Do not redistribute copyrighted transcripts without permission
- This tool is intended for personal research, accessibility, note-taking, and workflows where you are authorized to use the content

The authors provide this software as-is and do not encourage misuse of third-party content.

## License

MIT — see [LICENSE](LICENSE).

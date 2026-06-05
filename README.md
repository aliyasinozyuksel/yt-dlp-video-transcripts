# YouTube Channel Transcripts

Download English subtitles/transcripts from YouTube channels, playlists, or individual videos and save them as plain `.txt` and agent-friendly `.md` files.

This tool uses [yt-dlp](https://github.com/yt-dlp/yt-dlp) to fetch subtitles only. It does **not** download video or audio files.

## What it does

- Lists videos from a channel, playlist, or single video URL
- Downloads English manual subtitles first, then English auto-generated captions
- Converts VTT/SRT subtitles into clean transcript text
- Writes both `.txt` and `.md` outputs with YAML frontmatter
- Supports chunked processing for large channels (600+ videos)
- Resumes safely by skipping completed transcript pairs
- Repairs partial transcript files after interrupted runs
- Detects existing transcripts by `video_id`, not just filename

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
| `--version` | — | Show version and exit |

If either `--start-index` or `--end-index` is set, the index range takes priority over `--max-videos`.

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
    index.md
    progress.json
    report.json
    last_run_report.json
    cumulative_report.json
    reports/
      report_001_100.json
      report_101_200.json
```

- **txt/** — plain transcript text
- **md/** — Markdown with YAML frontmatter (`title`, `url`, `upload_date`, `channel`, `video_id`)
- **index.md** — table of all transcripts found on disk
- **progress.json** — live run state, updated after each video
- **report.json** — latest run summary (`total_videos` + `total_channel_videos`)
- **last_run_report.json** — copy of the latest run report
- **cumulative_report.json** — merged progress across all chunks
- **reports/** — archived per-chunk reports that are not overwritten by later chunks

## Resume behavior

- Existing transcripts are detected by `video_id` from Markdown frontmatter
- If both `.txt` and `.md` exist for a `video_id`, the video is skipped unless `--force` is used
- If only one file exists, the video is repaired automatically
- This avoids duplicate files when a video title changes

## Channel tabs

Pass the tab you want explicitly:

- `https://www.youtube.com/@channel/videos`
- `https://www.youtube.com/@channel/shorts`
- `https://www.youtube.com/@channel/streams`

If you process multiple tabs into the same output folder, numbering can collide. Use separate output directories per tab.

## Development

```bash
python -m pytest
python -m compileall scripts
ruff check .
```

## Troubleshooting

| Problem | What to do |
|---------|------------|
| `yt-dlp is not installed` | Run `pip install yt-dlp` |
| `No videos found for the provided URL` | Check the URL and make sure the channel/playlist is public |
| `The selected range contains no videos` | Adjust `--start-index` / `--end-index` |
| `skipped (no English subtitles)` | That video has no English manual or auto captions |
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

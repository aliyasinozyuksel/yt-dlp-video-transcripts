from __future__ import annotations

from pathlib import Path

from tests.conftest import ct


def write_pair(
    base: Path,
    *,
    index: int,
    video_id: str,
    title: str,
    txt_content: str = "transcript text",
    include_md: bool = True,
    include_txt: bool = True,
    basename: str | None = None,
) -> tuple[Path, Path, str]:
    slug = basename or f"{index:03d}_{ct.slugify(title)}"
    txt_dir = base / "txt"
    md_dir = base / "md"
    txt_dir.mkdir(parents=True, exist_ok=True)
    md_dir.mkdir(parents=True, exist_ok=True)

    txt_path = txt_dir / f"{slug}.txt"
    md_path = md_dir / f"{slug}.md"

    if include_txt:
        txt_path.write_text(txt_content + "\n", encoding="utf-8")
    if include_md:
        md_path.write_text(
            ct.build_md_content(
                title=title,
                url=f"https://www.youtube.com/watch?v={video_id}",
                upload_date="2024-01-01",
                channel="Test Channel",
                video_id=video_id,
                transcript=txt_content,
            ),
            encoding="utf-8",
        )
    return txt_path, md_path, slug


def test_collect_completed_from_disk(tmp_path: Path):
    write_pair(tmp_path, index=1, video_id="abc123", title="First Video")
    write_pair(
        tmp_path,
        index=2,
        video_id="def456",
        title="Second Video",
        include_md=True,
        include_txt=False,
    )

    completed = ct.collect_completed_from_disk(tmp_path / "md", tmp_path / "txt")
    assert len(completed) == 1
    assert completed[0]["id"] == "abc123"
    assert completed[0]["basename"] == "001_first-video"


def test_build_existing_files_index_by_video_id(tmp_path: Path):
    write_pair(tmp_path, index=5, video_id="vid999", title="Old Title Name")
    index = ct.build_existing_files_index(tmp_path / "md", tmp_path / "txt")
    assert "vid999" in index
    assert index["vid999"]["basename"] == "005_old-title-name"


def test_resolve_video_action_skip_complete(tmp_path: Path):
    _, _, slug = write_pair(tmp_path, index=1, video_id="vid1", title="Complete")
    existing = ct.build_existing_files_index(tmp_path / "md", tmp_path / "txt")
    action, txt_path, md_path, basename, partial = ct.resolve_video_action(
        "vid1",
        "001_new-title",
        tmp_path / "txt" / "001_new-title.txt",
        tmp_path / "md" / "001_new-title.md",
        existing,
        force=False,
    )
    assert action == "skip"
    assert basename == slug
    assert txt_path.name == f"{slug}.txt"
    assert md_path.name == f"{slug}.md"
    assert partial is False


def test_resolve_video_action_repair_partial_txt_only(tmp_path: Path):
    write_pair(
        tmp_path,
        index=2,
        video_id="vid2",
        title="Partial",
        include_md=False,
        include_txt=True,
    )
    existing = ct.build_existing_files_index(tmp_path / "md", tmp_path / "txt")
    action, _, _, _, partial = ct.resolve_video_action(
        "vid2",
        "002_partial",
        tmp_path / "txt" / "002_partial.txt",
        tmp_path / "md" / "002_partial.md",
        existing,
        force=False,
    )
    assert action == "repair"
    assert partial is True


def test_resolve_video_action_repair_partial_md_only_by_video_id(tmp_path: Path):
    write_pair(
        tmp_path,
        index=4,
        video_id="vid4",
        title="Md Only",
        include_md=True,
        include_txt=False,
    )
    existing = ct.build_existing_files_index(tmp_path / "md", tmp_path / "txt")
    action, txt_path, md_path, basename, partial = ct.resolve_video_action(
        "vid4",
        "004_other-slug",
        tmp_path / "txt" / "004_other-slug.txt",
        tmp_path / "md" / "004_other-slug.md",
        existing,
        force=False,
    )
    assert action == "repair"
    assert basename == "004_md-only"
    assert txt_path.name == "004_md-only.txt"
    assert md_path.name == "004_md-only.md"
    assert partial is True


def test_resolve_video_action_skip_by_video_id_when_title_changes(tmp_path: Path):
    write_pair(tmp_path, index=3, video_id="vid3", title="Original Title")
    existing = ct.build_existing_files_index(tmp_path / "md", tmp_path / "txt")
    action, txt_path, md_path, basename, _ = ct.resolve_video_action(
        "vid3",
        "003_completely-different-title",
        tmp_path / "txt" / "003_completely-different-title.txt",
        tmp_path / "md" / "003_completely-different-title.md",
        existing,
        force=False,
    )
    assert action == "skip"
    assert basename == "003_original-title"
    assert txt_path.exists()
    assert md_path.exists()


def test_write_index_md_escapes_pipe_in_title(tmp_path: Path):
    items = [
        {
            "index": 1,
            "title": "Part A | Part B",
            "upload_date": "2024-01-01",
            "basename": "001_part-a-part-b",
        }
    ]
    index_path = tmp_path / "index.md"
    ct.write_index_md(index_path, "Test Channel", items)
    content = index_path.read_text(encoding="utf-8")
    assert r"Part A \| Part B" in content
    assert "| Part A | Part B |" not in content

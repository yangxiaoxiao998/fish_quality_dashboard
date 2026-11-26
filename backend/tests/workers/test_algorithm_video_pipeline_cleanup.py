from pathlib import Path

from app import paths
from app.workers.handlers.algorithm_video_pipeline import _cleanup_video_copies


def _write_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"video")


def test_cleanup_keeps_all_source_and_playable_copies(
    tmp_path: Path, monkeypatch
) -> None:
    video_id = "20261026_0004"
    jobs_dir = tmp_path / "jobs"
    results_dir = tmp_path / "results"
    uploads_dir = tmp_path / "uploads" / "videos"

    monkeypatch.setattr(paths, "JOBS_DIR", jobs_dir)
    monkeypatch.setattr(paths, "RESULTS_DIR", results_dir)

    source_video = uploads_dir / f"{video_id}.mp4"
    playable_video = uploads_dir / "playable" / f"{video_id}_h264.mp4"
    job_copy = jobs_dir / video_id / "input" / f"{video_id}_source_video.mp4"
    result_copy = (
        results_dir / video_id / "input" / f"{video_id}_source_video.mp4"
    )

    for path in (source_video, playable_video, job_copy, result_copy):
        _write_file(path)

    _cleanup_video_copies(video_id)

    assert source_video.is_file()
    assert playable_video.is_file()
    assert job_copy.is_file()
    assert result_copy.is_file()


def test_cleanup_without_video_id_is_a_noop(tmp_path: Path, monkeypatch) -> None:
    jobs_dir = tmp_path / "jobs"
    results_dir = tmp_path / "results"
    monkeypatch.setattr(paths, "JOBS_DIR", jobs_dir)
    monkeypatch.setattr(paths, "RESULTS_DIR", results_dir)

    sentinel = jobs_dir / "sentinel.mp4"
    _write_file(sentinel)

    _cleanup_video_copies(None)

    assert sentinel.is_file()

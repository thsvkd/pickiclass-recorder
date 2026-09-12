import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from pickiclass.client import ProtectedMediaError
from pickiclass.models import Course, Lesson, VideoSource
from pickiclass.recorder import Recorder


def lesson():
    return Lesson("448:235", "회차", 10, 0, "강의", 0, 0)


def test_mp4_only_saved_then_transcribed_from_original(tmp_path, monkeypatch):
    client = SimpleNamespace(
        get_video_source=lambda *a: VideoSource("id", "https://a.test/a.mp4", None)
    )
    recorder = Recorder(client, Path("ffmpeg"), Path("ffprobe"), tmp_path)
    inputs = []
    monkeypatch.setattr(
        recorder, "_download_mp4", lambda url, path, *a: path.write_bytes(b"original") or True
    )
    monkeypatch.setattr(
        recorder, "_convert", lambda source, dest, *a: dest.write_bytes(b"converted")
    )

    def transcribe(source, output_dir=None, **kwargs):
        inputs.append(Path(source).read_bytes())
        for suffix in ("txt", "srt", "json"):
            (output_dir / (Path(source).stem + "." + suffix)).write_text("speech")

    recorder._transcriber = SimpleNamespace(transcribe=transcribe)
    course = Course("34:1", "강의", "")
    summary = recorder.run(course, [lesson()], lambda p: None, threading.Event())
    assert summary.done == 1
    assert inputs == [b"original"]
    assert list(tmp_path.rglob("전사 (원본 시간)/*.txt"))
    assert not list(tmp_path.rglob(".source/*.mp4"))


def test_drm_records_then_transcribes_when_device_selected(tmp_path, monkeypatch):
    def capture(client, course_id, lesson, destination, device_index, on_progress=None, cancel=None):
        assert device_index == 77
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"audio-bytes")
        return destination

    monkeypatch.setattr("pickiclass.recorder.capture_protected_lesson", capture)
    recorder = Recorder(
        SimpleNamespace(get_video_source=lambda *a: (_ for _ in ()).throw(ProtectedMediaError("암호화"))),
        Path("ffmpeg"),
        Path("ffprobe"),
        tmp_path,
        speed=1.0,
        capture_device_index=77,
    )

    def transcribe(source, output_dir=None, **kwargs):
        for suffix in ("txt", "srt", "json"):
            (output_dir / (Path(source).stem + "." + suffix)).write_text("speech")

    recorder._transcriber = SimpleNamespace(transcribe=transcribe)
    summary = recorder.run(
        Course("34:1", "강의", ""), [lesson()], lambda p: None, threading.Event()
    )
    assert summary.done == 1
    wavs = list(tmp_path.rglob("*.wav"))
    assert wavs and wavs[0].read_bytes() == b"audio-bytes"
    assert list(tmp_path.rglob("전사 (원본 시간)/*.txt"))


def test_drm_is_not_retried_as_a_transient_download_error(tmp_path):
    calls = []

    def source(*args):
        calls.append(args)
        raise ProtectedMediaError("암호화")

    recorder = Recorder(
        SimpleNamespace(get_video_source=source), Path("ffmpeg"), Path("ffprobe"), tmp_path
    )
    summary = recorder.run(
        Course("34:1", "강의", ""), [lesson()], lambda p: None, threading.Event()
    )
    assert summary.failed == 1
    assert len(calls) == 1


def test_raw_path_is_windows_safe(tmp_path):
    r = Recorder(SimpleNamespace(), Path("ffmpeg"), Path("ffprobe"), tmp_path)
    path = r._raw_path(Course("34:1", "강의", ""), lesson(), tmp_path / "01.mp4")
    assert ":" not in path.name


def staged_recorder(tmp_path):
    recorder = Recorder(SimpleNamespace(), Path("ffmpeg"), Path("ffprobe"), tmp_path)
    course = Course("34:1", "강의", "")
    final = recorder._final_path(course, lesson())
    final.parent.mkdir(parents=True)
    final.write_bytes(b"sped up")
    raw = recorder._raw_path(course, lesson(), final)
    raw.parent.mkdir()
    raw.write_bytes(b"original")
    return recorder, course, final, raw


def test_cancel_preserves_original_for_resume(tmp_path):
    from pickiclass.transcription import TranscriptionCancelled

    recorder, course, final, raw = staged_recorder(tmp_path)
    cancel = threading.Event()

    def cancelled(*args, **kwargs):
        cancel.set()
        raise TranscriptionCancelled("cancel")

    recorder._transcriber = SimpleNamespace(transcribe=cancelled)
    recorder.run(course, [lesson()], lambda p: None, cancel)
    assert raw.read_bytes() == b"original"
    assert final.read_bytes() == b"sped up"
    observed = []

    def resumed(source, **kwargs):
        observed.append(source.read_bytes())

    recorder._transcriber = SimpleNamespace(transcribe=resumed)
    summary = recorder.run(course, [lesson()], lambda p: None, threading.Event())
    assert summary.done == 1
    assert observed == [b"original"]


def test_transcription_failure_keeps_original(tmp_path):
    recorder, course, final, raw = staged_recorder(tmp_path)

    def fail(*args, **kwargs):
        raise RuntimeError("model unavailable")

    recorder._transcriber = SimpleNamespace(transcribe=fail)
    summary = recorder.run(course, [lesson()], lambda p: None, threading.Event())
    assert summary.failed == 1
    assert raw.read_bytes() == b"original"


def test_resume_never_transcribes_sped_up_file(tmp_path):
    recorder, course, final, raw = staged_recorder(tmp_path)
    raw.unlink()
    summary = recorder.run(course, [lesson()], lambda p: None, threading.Event())
    assert summary.failed == 1
    assert "원본 전사 입력이 없습니다" in summary.errors[0][1]
    assert final.read_bytes() == b"sped up"


def test_partial_transcript_is_preserved_without_retry(tmp_path, monkeypatch):
    recorder, course, final, raw = staged_recorder(tmp_path)
    folder = recorder._transcript_dir(final)
    folder.mkdir()
    txt = folder / final.with_suffix(".txt").name
    txt.write_text("keep me")
    attempts = []
    real = recorder._process_lesson_once

    def attempt(*args):
        attempts.append(1)
        return real(*args)

    monkeypatch.setattr(recorder, "_process_lesson_once", attempt)
    summary = recorder.run(course, [lesson()], lambda p: None, threading.Event())
    assert summary.failed == 1
    assert attempts == [1]
    assert txt.read_text() == "keep me"
    assert raw.exists()


@pytest.mark.parametrize("content_type", ["audio/mpeg", "audio/mp4", "audio/wav"])
def test_supported_audio_content_types(content_type):
    response = requests.Response()
    response.status_code = 200
    response.headers["content-type"] = content_type
    assert Recorder._is_mp4_response(response)


def test_hls_does_not_forward_authenticated_cookies(tmp_path):
    from pickiclass.client import SourceUnavailableError

    session = requests.Session()
    session.cookies.set("session", "private", domain="media.example", path="/")
    recorder = Recorder(SimpleNamespace(session=session), Path("ffmpeg"), Path("ffprobe"), tmp_path)
    with pytest.raises(SourceUnavailableError, match="쿠키 인증"):
        recorder._download_hls("https://media.example/video.m3u8", tmp_path / "x.mp4", 10,
                               lesson(), lambda p: None, threading.Event())


def test_hls_only_passes_safe_headers_not_unrelated_cookies(tmp_path, monkeypatch):
    session = requests.Session()
    session.cookies.set("session", "private", domain="pickiclass.com", path="/")
    session.headers["Referer"] = "https://pickiclass.com/"
    recorder = Recorder(SimpleNamespace(session=session), Path("ffmpeg"), Path("ffprobe"), tmp_path)
    calls = []
    monkeypatch.setattr(recorder, "_run_ffmpeg", lambda cmd, *args: calls.append(cmd))
    recorder._download_hls("https://media.example/video.m3u8", tmp_path / "x.mp4", 10,
                           lesson(), lambda p: None, threading.Event())
    assert "private" not in str(calls)
    assert "Referer: https://pickiclass.com/" in str(calls)

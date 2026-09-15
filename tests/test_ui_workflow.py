import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from ui.app import App
from ui.transcription_view import TranscriptionScreen


def test_login_starts_without_updater():
    app = App.__new__(App)
    app.client = object()
    app.show_login = AsyncMock()
    asyncio.run(app.start())
    app.show_login.assert_awaited_once()


def test_remember_stores_id_only_and_removes_old_password():
    app = App.__new__(App)
    app.prefs = SimpleNamespace(set=AsyncMock(), remove=AsyncMock())
    asyncio.run(app.save_login_prefs("example", True))
    assert app.prefs.set.await_args_list[1].args == ("pickiclass_login_id", "example")
    assert len(app.prefs.set.await_args_list) == 2
    app.prefs.remove.assert_awaited_with("pickiclass_login_password")


def test_login_does_not_load_saved_password(monkeypatch):
    import ui.app as app_module

    monkeypatch.setenv("pickiclass_id", "local-user")
    monkeypatch.setenv("pickiclass_pw", "ephemeral")
    app = App.__new__(App)
    app.prefs = SimpleNamespace(get=AsyncMock(return_value=False), remove=AsyncMock())
    app.show_view = Mock()
    build = Mock()
    monkeypatch.setattr(app_module, "build_login_view", build)
    asyncio.run(app.show_login())
    assert build.call_args.args[1:3] == ("local-user", "ephemeral")
    assert all(c.args[0] != "pickiclass_login_password" for c in app.prefs.get.await_args_list)


def make_screen():
    app = SimpleNamespace(page=SimpleNamespace(update=Mock()), show_view=Mock())
    return TranscriptionScreen(app, None)


def test_transcription_success_uses_local_file_and_displays_paths(tmp_path, monkeypatch):
    source = tmp_path / "input.wav"
    source.write_bytes(b"test fixture")
    result = SimpleNamespace(**{f"{ext}_path": tmp_path / f"input.{ext}" for ext in ["txt", "srt", "json"]})
    transcribe = Mock(return_value=result)
    monkeypatch.setitem(sys.modules, "pickiclass.transcription", SimpleNamespace(
        Transcriber=lambda: SimpleNamespace(transcribe=transcribe)
    ))
    screen = make_screen()
    screen.source.value = str(source)
    asyncio.run(screen._start())
    assert transcribe.call_args.args == (source,)
    assert screen.status.value == "전사 완료"
    assert len(screen.results.controls) == 3
    assert not screen.busy


def test_transcription_failure_restores_controls(tmp_path, monkeypatch):
    source = tmp_path / "input.wav"
    source.touch()
    monkeypatch.setitem(sys.modules, "pickiclass.transcription", SimpleNamespace(
        Transcriber=lambda: SimpleNamespace(transcribe=Mock(side_effect=RuntimeError("bad media")))
    ))
    screen = make_screen()
    screen.source.value = str(source)
    asyncio.run(screen._start())
    assert "bad media" in screen.status.value
    assert not screen.start_button.disabled
    assert not screen.folder_button.visible


def test_cancel_sets_worker_event():
    screen = make_screen()
    screen._set_busy(True)
    screen._cancel()
    assert screen.cancel.is_set()
    assert screen.cancel_button.disabled


def test_record_requires_explicit_device_selection():
    screen = make_screen()
    asyncio.run(screen._record())
    assert "직접 선택" in screen.status.value
    assert not screen.busy


def test_record_then_transcribe_uses_selected_device(tmp_path, monkeypatch):
    destination = tmp_path / "capture.wav"
    record = Mock(return_value=destination)
    monkeypatch.setitem(sys.modules, "pickiclass.audio_capture", SimpleNamespace(
        LoopbackRecorder=lambda: SimpleNamespace(record=record)
    ))
    screen = make_screen()
    screen.devices.value = "7"
    screen.duration.value = "0.5"
    screen.destination.value = str(destination)
    screen._start = AsyncMock()
    asyncio.run(screen._record())
    assert record.call_args.kwargs["device_index"] == 7
    assert record.call_args.kwargs["duration_sec"] == 30
    assert screen.source.value == str(destination)
    screen._start.assert_awaited_once()


def test_record_refuses_overwrite(tmp_path):
    destination = tmp_path / "existing.wav"
    destination.write_bytes(b"keep")
    screen = make_screen()
    screen.devices.value = "1"
    screen.destination.value = str(destination)
    asyncio.run(screen._record())
    assert "같은 이름" in screen.status.value
    assert destination.read_bytes() == b"keep"

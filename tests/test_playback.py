from pickiclass.playback import (
    CAPTURE_BLOCK_MESSAGE,
    PlaybackMonitor,
    capture_programs_from_names,
    parse_event,
)


def test_progress_then_done_is_success():
    monitor = PlaybackMonitor()
    monitor.apply({"status": "controller_loaded"})
    monitor.apply({"status": "progress", "position": 2.8, "duration": 665})
    assert monitor.started
    monitor.apply({"status": "done", "position": 665, "duration": 665})
    assert monitor.outcome == "done"


def test_pause_after_few_seconds_is_capture_block():
    monitor = PlaybackMonitor()
    monitor.apply({"status": "progress", "position": 2.85, "duration": 665})
    monitor.apply({"status": "pause", "position": 16.34, "duration": 665})
    assert monitor.outcome == "failed"
    assert monitor.message == CAPTURE_BLOCK_MESSAGE


def test_error_code_1002_is_capture_block_even_with_progress():
    monitor = PlaybackMonitor()
    monitor.apply({"status": "progress", "position": 3, "duration": 665})
    monitor.apply({"status": "error", "error_code": -1002})
    assert monitor.outcome == "failed"
    assert "Chrome Remote Desktop" in monitor.message or "원격" in monitor.message


def test_pause_near_end_counts_as_done():
    monitor = PlaybackMonitor()
    monitor.apply({"status": "progress", "position": 663.5, "duration": 665})
    monitor.apply({"status": "pause", "position": 664.2, "duration": 665})
    assert monitor.outcome == "done"


def test_chrome_remote_desktop_is_flagged_as_capture_program():
    assert capture_programs_from_names(
        ["Kollus Player v3", "Chrome Remote Desktop Host", "Notepad"]
    ) == ["Chrome Remote Desktop Host"]


def test_kollus_block_reason_reads_recent_cp949_log(tmp_path):
    from datetime import datetime, timedelta

    from pickiclass.playback import kollus_block_reason

    def line(when, msg):
        stamp = when.strftime("%Y-%m-%d, %H:%M:%S")
        return f"{stamp}.225, [1], info  , setCaptueCode code = -1002, msg = {msg}\r\n"

    log = tmp_path / "KollusAgent.log"
    now = datetime.now()
    log.write_bytes((line(now - timedelta(hours=1), "스팀 클라이언트(steam.exe)")
                     + line(now, "Chrome Remote Desktop")).encode("cp949"))
    assert kollus_block_reason(log) == "Chrome Remote Desktop"
    log.write_bytes(line(now - timedelta(hours=1), "스팀 클라이언트(steam.exe)").encode("cp949"))
    assert kollus_block_reason(log) is None
    assert kollus_block_reason(tmp_path / "missing.log") is None


def test_parse_event_ignores_non_json_and_secrets_shape():
    assert parse_event("not json") is None
    assert parse_event('{"status":"play","position":1}') == {"status": "play", "position": 1}

"""공식 내장 재생 이벤트만 해석한다. 보호 정책 우회는 하지 않는다."""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pickiclass.audio_capture import AudioCaptureCancelled, AudioCaptureError, LoopbackRecorder
from pickiclass.client import SourceUnavailableError
from pickiclass.models import Lesson

CAPTURE_BLOCK_MESSAGE = (
    "Kollus가 화면 공유·원격 제어 또는 캡처 프로그램을 감지해 재생을 중단했습니다. "
    "해당 프로그램을 종료한 뒤 다시 시도하세요. Chrome Remote Desktop Host가 설치돼 있으면 "
    "원격 접속 중이 아니어도 Chrome이 켜져 있는 동안 감지됩니다."
)
_CAPTURE_NAME_MARKERS = ("chrome remote desktop", "chromoting")
_START_TIMEOUT_SEC = 90
_EARLY_INTERRUPT_SEC = 30
# Kollus 에이전트는 차단할 때 감지한 프로그램을 사용자 TEMP의 로그에 남긴다.
_KOLLUS_BLOCK_RE = re.compile(
    r"^\W*(\d{4}-\d\d-\d\d, \d\d:\d\d:\d\d)[^\n]*setCaptueCode code = -?1002, msg = ([^\r\n]*)",
    re.M,
)


def kollus_block_reason(log_path: Path | None = None, max_age_sec: float = 120) -> str | None:
    """Kollus 에이전트 로그에서 최근 캡처 차단 사유(감지된 프로그램)를 읽는다."""
    if log_path is None:
        log_path = Path(os.environ.get("TEMP", "")) / "KollusAgent.log"
    try:
        data = log_path.read_bytes()
    except OSError:
        return None
    # 에이전트 버전에 따라 로그가 cp949이거나 BOM 있는 UTF-16이다(3.1.2.6은 UTF-16).
    text = data.decode("utf-16", "replace") if data[:2] == b"\xff\xfe" else data.decode(
        "cp949", "replace"
    )
    for stamp, msg in reversed(_KOLLUS_BLOCK_RE.findall(text)):
        logged = datetime.strptime(stamp, "%Y-%m-%d, %H:%M:%S")
        if abs((datetime.now() - logged).total_seconds()) > max_age_sec:
            return None
        # 한글 설명은 버전에 따라 깨진 채 기록되므로 괄호 안 실행 파일 이름을 우선 쓴다.
        program = re.search(r"\(([^,)]+)", msg)
        return program.group(1).strip() if program else (msg.strip() or None)
    return None


def _capture_block_message() -> str:
    reason = kollus_block_reason()
    return f"{CAPTURE_BLOCK_MESSAGE} (Kollus 감지: {reason})" if reason else CAPTURE_BLOCK_MESSAGE


def capture_programs_from_names(names: list[str]) -> list[str]:
    found = []
    seen = set()
    for name in names:
        lowered = name.lower()
        if name not in seen and any(marker in lowered for marker in _CAPTURE_NAME_MARKERS):
            found.append(name)
            seen.add(name)
    return found


def installed_capture_programs() -> list[str]:
    names: list[str] = []
    if sys.platform == "win32":
        import winreg

        roots = (
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        )
        for root, path in roots:
            try:
                with winreg.OpenKey(root, path) as hive:
                    for index in range(winreg.QueryInfoKey(hive)[0]):
                        try:
                            with winreg.OpenKey(hive, winreg.EnumKey(hive, index)) as key:
                                names.append(str(winreg.QueryValueEx(key, "DisplayName")[0]))
                        except OSError:
                            continue
            except OSError:
                continue
    return capture_programs_from_names(names)


# Kollus 에이전트가 실제로 차단한 프로세스(로그 실측)와 캡처 차단 모듈 문자열에 있던 이름.
# ponytail: 고정 목록이다. Kollus가 새 프로그램을 막으면 실패 메시지의 감지 이름을 보고 추가한다.
_BLOCKING_PROCESSES = frozenset({
    "remote_assistance_host.exe", "remoting_desktop.exe", "steam.exe", "snippingtool.exe",
    "obs.exe", "obs32.exe", "obs64.exe", "winvnc.exe", "realvnc.exe", "vncserver.exe",
    "anydesk.exe", "teamviewer_desktop.exe",
})


def running_blockers() -> list[str]:
    """Kollus가 재생을 중단시키는 실행 중 프로그램. CRD Host가 설치돼 있으면 Chrome도 포함한다."""
    if sys.platform != "win32":
        return []
    import subprocess

    try:
        listing = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    running = {row.split(",")[0].strip('"').lower() for row in listing.splitlines() if row}
    found = sorted(running & _BLOCKING_PROCESSES)
    if "chrome.exe" in running and installed_capture_programs():
        found.append("chrome.exe")
    return found


def parse_event(line: str) -> dict | None:
    text = line.strip()
    if not text or text[0] not in "{[":
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _is_capture_block(event: dict) -> bool:
    code = event.get("error_code")
    if code in (1002, -1002, "1002", "-1002"):
        return True
    return event.get("error_kind") == "capture_block"


class PlaybackMonitor:
    """내장 플레이어가 보낸 상태만으로 시작·완료·중단을 판정한다."""

    def __init__(self) -> None:
        self.status = "connecting"
        self.position = 0.0
        self.duration: float | None = None
        self.started = False
        self.outcome: str | None = None
        self.message = ""

    def apply(self, event: dict) -> None:
        if self.outcome is not None:
            return
        status = str(event.get("status") or self.status)
        self.status = status
        if isinstance(event.get("position"), (int, float)):
            self.position = float(event["position"])
        if isinstance(event.get("duration"), (int, float)) and event["duration"] > 0:
            self.duration = float(event["duration"])
        if _is_capture_block(event) or status == "error":
            self.outcome = "failed"
            self.message = _capture_block_message() if _is_capture_block(event) else (
                "내장 플레이어에서 재생 오류가 발생했습니다."
            )
            return
        if status in {"progress", "play"} and self.position >= 1.0:
            self.started = True
        if status == "done":
            self.started = True
            self.outcome = "done"
            return
        if status == "pause":
            remaining = None if self.duration is None else self.duration - self.position
            near_end = remaining is not None and remaining <= 2
            if self.position < _EARLY_INTERRUPT_SEC and not near_end:
                self.outcome = "failed"
                self.message = CAPTURE_BLOCK_MESSAGE
            elif near_end:
                self.started = True
                self.outcome = "done"
            return
        if status in {"controller_failed", "controller_load_failed", "no_player", "lesson_not_found"}:
            self.outcome = "failed"
            self.message = "내장 플레이어를 준비하지 못했습니다. 강의 페이지와 WebView2를 확인하세요."


def capture_protected_lesson(
    client,
    course_id: str,
    lesson: Lesson,
    destination: Path,
    device_index: int,
    on_progress: Callable[[float, str], None] | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """앱 소유 창에서 공식 재생을 시작하고, 선택된 출력 루프백만 저장한다."""
    from pickiclass.embedded_browser import browser_payload, start_browser_process

    def cancelled() -> bool:
        return cancel is not None and cancel.is_set()

    payload = browser_payload(client, course_id, lesson.lesson_id, lesson.global_index)
    process = start_browser_process(payload, capture_stdout=True)
    monitor = PlaybackMonitor()
    stop = threading.Event()
    rec_cancel = threading.Event()
    closed = threading.Event()
    errors: list[BaseException] = []
    duration_hint = float(lesson.duration_sec) if lesson.duration_sec and lesson.duration_sec >= 1 else 60 * 60

    def read_events() -> None:
        try:
            assert process.stdout is not None
            for line in process.stdout:
                event = parse_event(line)
                if event is None:
                    continue
                monitor.apply(event)
                if monitor.outcome == "done":
                    stop.set()
                elif monitor.outcome == "failed":
                    rec_cancel.set()
                    break
        finally:
            closed.set()

    def record() -> None:
        try:
            LoopbackRecorder().record(
                destination,
                min(duration_hint + 8, 6 * 60 * 60),
                device_index,
                on_progress=on_progress,
                cancel=rec_cancel if cancel is None else _combined(cancel, rec_cancel),
                stop=stop,
            )
        except BaseException as exc:
            errors.append(exc)

    reader = threading.Thread(target=read_events, daemon=True)
    worker = threading.Thread(target=record)
    reader.start()
    worker.start()
    try:
        deadline = time.monotonic() + _START_TIMEOUT_SEC
        while time.monotonic() < deadline:
            if cancelled():
                rec_cancel.set()
                raise AudioCaptureCancelled("녹음을 취소했습니다.")
            if monitor.outcome == "failed":
                rec_cancel.set()
                raise SourceUnavailableError(monitor.message)
            if monitor.started or monitor.outcome == "done":
                break
            if process.poll() is not None:
                rec_cancel.set()
                raise SourceUnavailableError("내장 재생 창이 먼저 종료되었습니다.")
            time.sleep(0.1)
        if not monitor.started and monitor.outcome != "done":
            rec_cancel.set()
            raise SourceUnavailableError(
                "내장 플레이어에서 재생이 시작되지 않았습니다. 창의 오류 메시지를 확인하세요."
            )
        while worker.is_alive():
            if cancelled():
                rec_cancel.set()
                break
            if monitor.outcome == "failed":
                rec_cancel.set()
                break
            if monitor.outcome == "done":
                stop.set()
            if process.poll() is not None and monitor.outcome != "done":
                rec_cancel.set()
                break
            time.sleep(0.2)
        worker.join()
        if monitor.outcome == "failed":
            raise SourceUnavailableError(monitor.message)
        if cancelled():
            raise AudioCaptureCancelled("녹음을 취소했습니다.")
        if errors:
            raise errors[0]
        return destination
    except (SourceUnavailableError, AudioCaptureError, AudioCaptureCancelled):
        destination.unlink(missing_ok=True)
        if monitor.outcome == "failed":
            raise SourceUnavailableError(monitor.message) from None
        raise
    finally:
        rec_cancel.set()
        if worker.is_alive():
            worker.join(timeout=5)
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except Exception:
                process.kill()
        closed.wait(timeout=2)
        if process.stdout is not None:
            process.stdout.close()


def _combined(first: threading.Event, second: threading.Event) -> threading.Event:
    merged = threading.Event()

    def watch() -> None:
        while not merged.is_set():
            if first.is_set() or second.is_set():
                merged.set()
                return
            time.sleep(0.05)

    threading.Thread(target=watch, daemon=True).start()
    return merged

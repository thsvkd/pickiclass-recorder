"""사용자가 선택한 로컬 미디어 파일을 백그라운드에서 전사한다."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import flet as ft

from ui.progress_view import _open_folder

if TYPE_CHECKING:
    from ui.app import App


class TranscriptionScreen:
    def __init__(self, app: App, previous_view: ft.Control | None) -> None:
        self.app = app
        self.previous_view = previous_view
        self.cancel = threading.Event()
        self.busy = False
        self.source = ft.TextField(label="영상·음성 파일 경로", expand=True)
        self.status = ft.Text("파일을 선택한 뒤 전사를 시작하세요.", selectable=True)
        self.progress = ft.ProgressBar(value=0)
        self.results = ft.Column()
        self.pick_button = ft.TextButton("파일 선택", on_click=self._pick)
        self.start_button = ft.ElevatedButton("전사 시작", on_click=self._start)
        self.cancel_button = ft.TextButton("취소", disabled=True, on_click=self._cancel)
        self.back_button = ft.TextButton("돌아가기", on_click=self._back)
        self.folder_button = ft.TextButton("결과 폴더 열기", visible=False)
        self.devices = ft.Dropdown(label="녹음할 출력 장치 (직접 선택)", options=[])
        self.devices_button = ft.TextButton("출력 장치 조회", on_click=self._load_devices)
        self.duration = ft.TextField(label="녹음 시간 (분)", value="5", width=160)
        self.destination = ft.TextField(
            label="저장할 WAV 파일의 전체 경로",
            value=str(Path.home() / "Downloads" / "pickiclass-audio.wav"),
        )
        self.transcribe_after = ft.Checkbox(label="녹음 후 바로 전사", value=True)
        self.record_button = ft.ElevatedButton("선택한 출력 장치 녹음 시작", on_click=self._record)
        self.root = ft.Column(
            [
                ft.Row([self.back_button, ft.Text("로컬 파일 전사", size=22)]),
                ft.Text(
                    "컴퓨터에 저장된 영상·음성을 한국어 TXT·SRT·JSON으로 만듭니다. "
                    "파일은 외부로 업로드하지 않습니다. 최초 실행 시 음성 인식 모델을 "
                    "내려받으며 준비에 시간이 걸릴 수 있습니다."
                ),
                ft.Row([self.source, self.pick_button]),
                ft.Text("결과는 입력 파일과 같은 폴더에 저장됩니다."),
                ft.Row([self.start_button, self.cancel_button]),
                self.progress,
                self.status,
                self.results,
                self.folder_button,
                ft.Divider(),
                ft.Text("재생 중인 출력 오디오 녹음", size=18),
                ft.Text(
                    "브라우저에서 강의를 정상 재생한 뒤 시작하세요. 선택한 출력 장치에서 "
                    "들리는 다른 앱 소리도 함께 저장됩니다. 마이크는 녹음하지 않습니다."
                ),
                self.devices_button,
                self.devices,
                self.duration,
                self.destination,
                self.transcribe_after,
                self.record_button,
            ],
            spacing=16,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )
        app.show_view(self.root)

    async def _pick(self, e=None) -> None:
        try:
            files = await self.app.file_picker.pick_files(
                dialog_title="전사할 영상·음성 선택", allow_multiple=False
            )
            if files and files[0].path:
                self.source.value = files[0].path
                self.app.page.update()
        except Exception:  # noqa: BLE001 - 파일 선택 서비스 실패를 UI에 표시
            self.status.value = "파일 선택 창을 열지 못했습니다. 파일 경로를 직접 입력하세요."
            self.app.page.update()

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.source.disabled = busy
        self.pick_button.disabled = busy
        self.start_button.disabled = busy
        self.back_button.disabled = busy
        self.cancel_button.disabled = not busy
        for control in (
            self.devices, self.devices_button, self.duration, self.destination,
            self.transcribe_after, self.record_button,
        ):
            control.disabled = busy

    async def _load_devices(self, e=None) -> None:
        try:
            from pickiclass.audio_capture import list_loopback_devices

            devices = await asyncio.to_thread(list_loopback_devices)
            self.devices.options = [
                ft.DropdownOption(key=str(d["index"]), text=d["name"]) for d in devices
            ]
            self.devices.value = None
            self.status.value = "녹음할 출력 장치를 선택하세요." if devices else "출력 장치가 없습니다."
        except Exception as exc:  # noqa: BLE001 - 장치 오류를 UI에 표시
            self.status.value = f"출력 장치 조회 실패: {exc}"
        self.app.page.update()

    async def _record(self, e=None) -> None:
        if self.busy:
            return
        try:
            if self.devices.value is None:
                raise ValueError("출력 장치를 먼저 조회하고 직접 선택하세요.")
            minutes = float(self.duration.value or "")
            if not 0 < minutes <= 240:
                raise ValueError("녹음 시간은 0분 초과, 240분 이하로 입력하세요.")
            destination = Path((self.destination.value or "").strip().strip('"')).expanduser()
            if destination.suffix.lower() != ".wav":
                raise ValueError("저장 경로는 .wav 파일이어야 합니다.")
            if destination.exists():
                raise ValueError("같은 이름의 파일이 있습니다. 다른 파일명을 입력하세요.")
        except ValueError as exc:
            self.status.value = str(exc)
            self.app.page.update()
            return
        self.cancel.clear()
        self._set_busy(True)
        self.progress.value = 0
        self.status.value = "선택한 출력 장치 녹음 중…"
        self.app.page.update()
        loop = asyncio.get_running_loop()
        recorded = None

        def report(fraction: float, message: str) -> None:
            loop.call_soon_threadsafe(self._apply_progress, fraction, message)

        def run():
            from pickiclass.audio_capture import LoopbackRecorder

            return LoopbackRecorder().record(
                destination, duration_sec=minutes * 60,
                device_index=int(self.devices.value), on_progress=report, cancel=self.cancel,
            )

        try:
            recorded = await asyncio.to_thread(run)
            self.source.value = str(recorded)
            self.status.value = f"오디오 저장 완료: {recorded}"
            self.progress.value = 1
        except Exception as exc:  # noqa: BLE001 - 녹음 실패를 UI에 표시
            self.status.value = "녹음이 취소되었습니다." if self.cancel.is_set() else f"녹음 실패: {exc}"
        finally:
            self._set_busy(False)
            self.app.page.update()
        if recorded is not None and self.transcribe_after.value and not self.cancel.is_set():
            await self._start()

    def _cancel(self, e=None) -> None:
        self.cancel.set()
        self.cancel_button.disabled = True
        self.status.value = "취소 요청 중… 현재 처리 구간이 끝나면 중단됩니다."
        self.app.page.update()

    def _back(self, e=None) -> None:
        if not self.busy and self.previous_view is not None:
            self.app.show_view(self.previous_view)

    async def _start(self, e=None) -> None:
        if self.busy:
            return
        source = Path((self.source.value or "").strip().strip('"')).expanduser()
        if not source.is_file():
            self.status.value = "존재하는 영상·음성 파일을 선택하세요."
            self.app.page.update()
            return
        self.cancel.clear()
        self._set_busy(True)
        self.results.controls.clear()
        self.folder_button.visible = False
        self.progress.value = None
        self.status.value = "음성 인식 모델 준비 중…"
        self.app.page.update()
        loop = asyncio.get_running_loop()

        def report(fraction: float, message: str) -> None:
            loop.call_soon_threadsafe(self._apply_progress, fraction, message)

        def run():
            from pickiclass.transcription import Transcriber

            return Transcriber().transcribe(source, on_progress=report, cancel=self.cancel)

        try:
            result = await asyncio.to_thread(run)
            self.progress.value = 1
            self.status.value = "전사 완료"
            paths = [result.txt_path, result.srt_path, result.json_path]
            self.results.controls = [ft.Text(str(path), selectable=True) for path in paths]
            self.folder_button.visible = True
            self.folder_button.on_click = lambda e: _open_folder(Path(result.txt_path).parent)
        except Exception as exc:  # noqa: BLE001 - 워커 실패를 UI에 표시
            self.progress.value = 0
            self.status.value = "전사가 취소되었습니다." if self.cancel.is_set() else f"전사 실패: {exc}"
        finally:
            self._set_busy(False)
            self.app.page.update()

    def _apply_progress(self, fraction: float, message: str) -> None:
        if self.busy and not self.cancel.is_set():
            self.progress.value = max(0.0, min(1.0, fraction))
            self.status.value = message
            self.app.page.update()

"""화면 전환과 공용 상태(로그인 세션, 설정)를 관리하는 앱 컨트롤러."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import flet as ft

from ui.core_bridge import (
    HAS_CLIENT,
    HAS_RECORDER,
    Course,
    Lesson,
    PickiclassClient,
    Recorder,
    find_ffmpeg,
    get_package_version,
    install_ffmpeg,
)
from ui.courses_view import build_course_list_view, build_error_view, build_loading_view
from ui.demo_data import (
    DemoClient,
    DemoRecorder,
    demo_current_version,
    demo_find_ffmpeg,
    demo_install_ffmpeg,
)
from ui.lesson_select_view import LessonSelectScreen
from ui.login_view import build_login_view
from ui.progress_view import ProgressScreen
from ui.transcription_view import TranscriptionScreen

DEFAULT_OUTPUT_DIR = Path.home() / "Downloads" / "피키클래스강의"

_PREF_REMEMBER = "pickiclass_remember_login"
_PREF_EMAIL = "pickiclass_login_id"
_PREF_PASSWORD = "pickiclass_login_password"


class CoreUnavailableError(RuntimeError):
    """코어 패키지의 필수 구성 요소가 아직 구현되지 않았을 때 발생."""


class App:
    def __init__(self, page: ft.Page, demo: bool = False) -> None:
        self.page = page
        self.demo = demo

        self.speed: float = 1.5
        self.output_dir: Path = DEFAULT_OUTPUT_DIR
        self.keep_original: bool = False
        self.workers: int = 2
        self.capture_device_index: int | None = None

        self.ffmpeg: Path | None = None
        self.ffprobe: Path | None = None

        # 진행 중인 강의 다운로드가 있는지 추적 (업데이트 재시작 권유 여부 판단용).
        self._current_progress_screen: ProgressScreen | None = None

        self._setup_page()

        self.file_picker = ft.FilePicker()
        self.prefs = ft.SharedPreferences()

        if demo:
            self.client = DemoClient()
        elif HAS_CLIENT:
            self.client = PickiclassClient()
        else:
            self.client = None

        self.page.run_task(self.start)

    # ------------------------------------------------------------------
    def _setup_page(self) -> None:
        self.page.title = "피키클래스 파일 저장 및 전사"
        self.page.window.width = 1100
        self.page.window.height = 780
        self.page.window.min_width = 900
        self.page.window.min_height = 600
        self.page.padding = 24
        self.page.theme_mode = ft.ThemeMode.SYSTEM

        # 화면 전환 시에도 업데이트 배너가 사라지지 않도록, 배너 자리와 화면 자리를
        # 분리된 슬롯으로 두고 show_view()/show_banner()는 각자의 슬롯만 갱신한다.
        self._banner_slot = ft.Container(visible=False)
        self._content_slot = ft.Container(expand=True)
        self.page.controls = [self._banner_slot, self._content_slot]

    def show_view(self, control: ft.Control) -> None:
        self._content_slot.content = control
        self.page.update()

    def show_banner(self, control: ft.Control) -> None:
        self._banner_slot.content = control
        self._banner_slot.visible = True
        self.page.update(self._banner_slot)

    def hide_banner(self) -> None:
        self._banner_slot.visible = False
        self.page.update(self._banner_slot)

    def show_snack_bar(self, message: str) -> None:
        self.page.show_dialog(ft.SnackBar(content=ft.Text(message), open=True))

    def is_download_active(self) -> bool:
        """강의 다운로드가 진행 중이면 True (업데이트 재시작을 미루기 위한 판단용)."""
        screen = self._current_progress_screen
        return screen is not None and screen.last_summary is None

    def get_display_version(self) -> str:
        """로그인 화면 등에 표시할 버전 문자열. 설치판이 아니면 패키지 버전으로 대신한다."""
        if self.demo:
            return demo_current_version() or get_package_version()
        return get_package_version()

    # ------------------------------------------------------------------
    # 시작 흐름: 로그인 (FFmpeg는 다운로드할 때 확인)
    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self.client is None and not self.demo:
            self.show_view(
                build_error_view(
                    "코어 모듈(pickiclass.client)을 아직 찾을 수 없습니다.\n"
                    "코어 구현이 끝난 뒤 앱을 다시 실행해주세요.",
                    on_retry=lambda e: self.page.run_task(self.start),
                )
            )
            return

        await self.show_login()

    async def _check_ffmpeg(self) -> None:
        find_fn = demo_find_ffmpeg if self.demo else find_ffmpeg
        if find_fn is None:
            return

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, find_fn)
        if result:
            self.ffmpeg, self.ffprobe = result
            return

        proceed = await self._ask_install_ffmpeg()
        if not proceed:
            return
        await self._run_install_ffmpeg()

    async def _ask_install_ffmpeg(self) -> bool:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()

        def on_yes(e: ft.ControlEvent) -> None:
            self.page.pop_dialog()
            if not future.done():
                future.set_result(True)

        def on_no(e: ft.ControlEvent) -> None:
            self.page.pop_dialog()
            if not future.done():
                future.set_result(False)

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("ffmpeg 설치 필요"),
            content=ft.Text(
                "영상 처리에 필요한 ffmpeg가 없습니다. 지금 자동으로 설치할까요? "
                # Windows(gyan.dev)와 macOS(evermeet.cx)는 받는 파일 크기가 꽤 다르다.
                f"({'약 110MB' if sys.platform.startswith('win') else '약 80MB'})"
            ),
            actions=[
                ft.TextButton("아니요", on_click=on_no),
                ft.ElevatedButton("예, 설치", on_click=on_yes),
            ],
        )
        self.page.show_dialog(dialog)
        return await future

    async def _run_install_ffmpeg(self) -> None:
        status_text = ft.Text("설치 준비 중...")
        progress_bar = ft.ProgressBar(value=0, width=320)
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("ffmpeg 설치 중"),
            content=ft.Column([status_text, progress_bar], tight=True, spacing=12),
        )
        self.page.show_dialog(dialog)

        def on_progress(fraction: float) -> None:
            # install_ffmpeg 의 콜백은 별도 스레드에서 호출되므로 run_thread 로 안전하게 넘긴다.
            self.page.run_thread(self._apply_install_progress, progress_bar, status_text, fraction)

        install_fn = demo_install_ffmpeg if self.demo else install_ffmpeg
        if install_fn is None:
            self.page.pop_dialog()
            return

        loop = asyncio.get_running_loop()
        try:
            self.ffmpeg, self.ffprobe = await loop.run_in_executor(None, install_fn, on_progress)
        except Exception as ex:  # noqa: BLE001
            status_text.value = f"설치에 실패했습니다: {ex}"
            self.page.update()
            await asyncio.sleep(2)
            self.ffmpeg = self.ffprobe = None
        self.page.pop_dialog()

    def _apply_install_progress(self, bar: ft.ProgressBar, text: ft.Text, fraction: float) -> None:
        bar.value = fraction
        text.value = f"설치 중... {int(fraction * 100)}%"
        self.page.update(bar, text)

    # ------------------------------------------------------------------
    # 로그인
    # ------------------------------------------------------------------
    async def show_login(self) -> None:
        remember = False
        email = os.environ.get("pickiclass_id", os.environ.get("PICKICLASS_ID", ""))
        password = os.environ.get("pickiclass_pw", os.environ.get("PICKICLASS_PW", ""))
        try:
            await self.prefs.remove(_PREF_PASSWORD)
            remember = bool(await self.prefs.get(_PREF_REMEMBER))
            if remember:
                email = (await self.prefs.get(_PREF_EMAIL)) or email
        except Exception:  # noqa: BLE001, S110 - 저장소를 못 읽어도 로그인 화면은 떠야 한다
            pass

        self.show_view(build_login_view(self, email, password, remember))

    async def save_login_prefs(self, email: str, remember: bool) -> None:
        try:
            await self.prefs.remove(_PREF_PASSWORD)
            await self.prefs.set(_PREF_REMEMBER, remember)
            if remember:
                await self.prefs.set(_PREF_EMAIL, email)
            else:
                await self.prefs.remove(_PREF_EMAIL)
        except Exception:  # noqa: BLE001, S110 - 저장 실패는 로그인 흐름을 막지 않는다
            pass

    # ------------------------------------------------------------------
    # 강의/목차
    # ------------------------------------------------------------------
    async def show_course_list(self) -> None:
        self.show_view(build_loading_view("강의 목록을 불러오는 중..."))
        try:
            loop = asyncio.get_running_loop()
            courses = await loop.run_in_executor(None, self.client.list_my_courses)
        except Exception as ex:  # noqa: BLE001
            self.show_view(
                build_error_view(
                    f"강의 목록을 불러오지 못했습니다: {ex}",
                    on_retry=lambda e: self.page.run_task(self.show_course_list),
                )
            )
            return

        self.show_view(build_course_list_view(self, courses))

    async def show_lesson_select(self, course: Course) -> None:
        self.show_view(build_loading_view("강의 목차를 불러오는 중..."))
        try:
            loop = asyncio.get_running_loop()
            lessons = await loop.run_in_executor(None, self.client.list_lessons, course)
        except Exception as ex:  # noqa: BLE001
            self.show_view(
                build_error_view(
                    f"강의 목차를 불러오지 못했습니다: {ex}",
                    on_retry=lambda e: self.page.run_task(self.show_lesson_select, course),
                )
            )
            return

        LessonSelectScreen(self, course, lessons)

    async def pick_output_directory(self) -> str | None:
        try:
            path = await self.file_picker.get_directory_path(
                dialog_title="저장 폴더 선택",
                initial_directory=str(self.output_dir),
            )
        except Exception:  # noqa: BLE001
            return None
        if path:
            self.output_dir = Path(path)
            return path
        return None

    # ------------------------------------------------------------------
    # 다운로드
    # ------------------------------------------------------------------
    def show_transcription(self) -> None:
        TranscriptionScreen(self, self._content_slot.content)

    async def open_embedded_browser(self, course: Course) -> None:
        from pickiclass.embedded_browser import launch_browser

        process = getattr(self, "_browser_process", None)
        if process is not None and process.poll() is None:
            self.show_snack_bar("앱 내장 재생 창이 이미 열려 있습니다.")
            return
        try:
            self._browser_process = await asyncio.to_thread(
                launch_browser, self.client, course.course_id
            )
            await asyncio.sleep(2)
            if self._browser_process.poll() is not None:
                raise RuntimeError("WebView2 재생 창을 시작하지 못했습니다.")
        except Exception:
            self.show_snack_bar("내장 브라우저를 열지 못했습니다. WebView2 설치를 확인하세요.")

    def begin_download(
        self, course: Course, lessons: list[Lesson], previous_view: ft.Control | None = None
    ) -> None:
        self.page.run_task(self._prepare_download, course, lessons, previous_view)

    async def _prepare_download(
        self, course: Course, lessons: list[Lesson], previous_view: ft.Control | None = None
    ) -> None:
        if self.ffmpeg is None or self.ffprobe is None:
            await self._check_ffmpeg()
        if self.ffmpeg is None or self.ffprobe is None:
            self.show_snack_bar("영상 저장에는 FFmpeg가 필요합니다.")
            return
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as ex:
            self.show_snack_bar(f"저장 폴더를 만들 수 없습니다: {ex}")
            return

        if self.demo:
            recorder = DemoRecorder(
                self.client,
                self.ffmpeg,
                self.ffprobe,
                self.output_dir,
                speed=self.speed,
                keep_original=self.keep_original,
                workers=self.workers,
                capture_device_index=self.capture_device_index,
            )
        elif HAS_RECORDER and self.ffmpeg is not None and self.ffprobe is not None:
            recorder = Recorder(
                self.client,
                self.ffmpeg,
                self.ffprobe,
                self.output_dir,
                speed=self.speed,
                keep_original=self.keep_original,
                workers=self.workers,
                capture_device_index=self.capture_device_index,
            )
        else:
            self.show_snack_bar("코어 모듈(pickiclass.recorder)을 아직 찾을 수 없습니다.")
            return

        return_view = previous_view if previous_view is not None else self._content_slot.content
        if return_view is None:
            return
        self._current_progress_screen = ProgressScreen(
            self, course, lessons, recorder, self.output_dir, return_view
        )

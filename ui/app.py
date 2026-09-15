"""화면 전환과 공용 상태(로그인 세션, 설정)를 관리하는 앱 컨트롤러."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import flet as ft

from ui.core_bridge import (
    HAS_CLIENT,
    HAS_RECORDER,
    Course,
    Lesson,
    PickiclassClient,
    Recorder,
    get_package_version,
)
from ui.courses_view import build_course_list_view, build_error_view, build_loading_view
from ui.demo_data import DemoClient, DemoRecorder, demo_current_version
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
        self.keep_original: bool = True
        self.workers: int = 2
        self.capture_device_index: int | None = None

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
    # 시작 흐름: 로그인
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
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as ex:
            self.show_snack_bar(f"저장 폴더를 만들 수 없습니다: {ex}")
            return

        if self.demo:
            recorder = DemoRecorder(
                self.client,
                self.output_dir,
                speed=self.speed,
                keep_original=self.keep_original,
                workers=self.workers,
                capture_device_index=self.capture_device_index,
            )
        elif HAS_RECORDER:
            recorder = Recorder(
                self.client,
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

"""다운로드 진행 화면."""

from __future__ import annotations

import platform
import subprocess
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import flet as ft

from ui.core_bridge import Course, Lesson, Progress, Summary
from ui.format import format_hours_minutes

if TYPE_CHECKING:
    from ui.app import App

_STAGE_LABELS = {
    "pending": "대기",
    "fetching": "정보 확인",
    "downloading": "내려받는 중",
    "recording": "재생 소리 저장 중",
    "streaming": "내려받으며 변환 중",
    "converting": "배속 변환 중",
    "transcribing": "전사 중",
    "done": "완료",
    "skipped": "건너뜀",
    "error": "실패",
}

_STAGE_COLORS = {
    "pending": ft.Colors.SURFACE_CONTAINER_HIGHEST,
    "fetching": ft.Colors.PRIMARY_CONTAINER,
    "downloading": ft.Colors.PRIMARY_CONTAINER,
    "recording": ft.Colors.PRIMARY_CONTAINER,
    "streaming": ft.Colors.PRIMARY_CONTAINER,
    "converting": ft.Colors.PRIMARY_CONTAINER,
    "transcribing": ft.Colors.PRIMARY_CONTAINER,
    "done": ft.Colors.TERTIARY_CONTAINER,
    "skipped": ft.Colors.SURFACE_CONTAINER_HIGHEST,
    "error": ft.Colors.ERROR_CONTAINER,
}

_STAGE_TEXT_COLORS = {
    "done": ft.Colors.ON_TERTIARY_CONTAINER,
    "error": ft.Colors.ON_ERROR_CONTAINER,
}

_TERMINAL_STAGES = {"done", "skipped", "error"}


def _open_folder(path: Path) -> None:
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.Popen(["open", str(path)])
        elif system == "Windows":
            subprocess.Popen(["explorer", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:  # noqa: BLE001, S110 - 폴더 열기 실패는 치명적이지 않음
        pass


class ProgressScreen:
    """다운로드 진행 상황을 보여주고, 행 단위로만 갱신하는 화면."""

    def __init__(
        self,
        app: App,
        course: Course,
        lessons: list[Lesson],
        recorder,
        output_dir: Path,
        previous_view: ft.Control,
    ) -> None:
        self.app = app
        self.page = app.page
        self.course = course
        self.lessons = lessons
        self.recorder = recorder
        self.output_dir = output_dir
        self.previous_view = previous_view
        self.cancel_event = threading.Event()
        self.finished_ids: set[str] = set()
        self.total = len(lessons)
        self.start_time = time.monotonic()
        self.last_summary: Summary | None = None
        self.return_after_cancel = False
        self.row_controls: dict[str, dict[str, ft.Control]] = {}

        self._build()
        self.app.show_view(self.root)
        threading.Thread(target=self._run, daemon=True).start()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        header = ft.Text(self.course.title, size=18, weight=ft.FontWeight.BOLD)
        self.overall_bar = ft.ProgressBar(value=0, expand=True)
        self.overall_text = ft.Text(f"0/{self.total} 완료")
        self.eta_text = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self.cancel_button = ft.ElevatedButton(
            "중단하고 돌아가기", icon=ft.Icons.ARROW_BACK, on_click=self._on_cancel_click
        )

        rows: list[ft.Control] = []
        for lesson in self.lessons:
            badge_text = ft.Text("대기", size=12)
            badge = ft.Container(
                content=badge_text,
                padding=ft.Padding(left=10, right=10, top=2, bottom=2),
                border_radius=12,
                bgcolor=_STAGE_COLORS["pending"],
            )
            bar = ft.ProgressBar(value=0, width=140)
            message = ft.Text(
                "", size=11, color=ft.Colors.ON_SURFACE_VARIANT, visible=False, max_lines=2
            )
            row = ft.Row(
                [
                    ft.Text(
                        lesson.title, expand=True, overflow=ft.TextOverflow.ELLIPSIS, max_lines=1
                    ),
                    badge,
                    bar,
                ],
                spacing=12,
            )
            self.row_controls[lesson.lesson_id] = {
                "badge": badge,
                "badge_text": badge_text,
                "bar": bar,
                "message": message,
            }
            rows.append(ft.Column([row, message], spacing=2))

        self.list_view = ft.ListView(controls=rows, expand=True, spacing=6)
        self.summary_area = ft.Column([], visible=False)

        self.root = ft.Column(
            [
                header,
                ft.Row([self.overall_bar]),
                ft.Row(
                    [self.overall_text, self.eta_text], alignment=ft.MainAxisAlignment.SPACE_BETWEEN
                ),
                ft.Row([self.cancel_button]),
                ft.Divider(),
                self.list_view,
                self.summary_area,
            ],
            expand=True,
            spacing=10,
        )

    # ------------------------------------------------------------------
    # 백그라운드 스레드에서 실행
    # ------------------------------------------------------------------
    def _run(self) -> None:
        summary = self.recorder.run(self.course, self.lessons, self._on_progress, self.cancel_event)
        self.page.run_thread(self._on_finished, summary)

    def _on_progress(self, progress: Progress) -> None:
        # Recorder 내부의 여러 워커 스레드에서 동시에 호출될 수 있으므로
        # page.run_thread 를 통해 안전하게 UI 갱신 작업을 넘긴다.
        self.page.run_thread(self._apply_progress, progress)

    def _apply_progress(self, progress: Progress) -> None:
        row = self.row_controls.get(progress.lesson.lesson_id)
        if row is None:
            return

        label = _STAGE_LABELS.get(progress.stage, progress.stage)
        row["badge_text"].value = label
        row["badge_text"].color = _STAGE_TEXT_COLORS.get(progress.stage)
        row["badge"].bgcolor = _STAGE_COLORS.get(
            progress.stage, ft.Colors.SURFACE_CONTAINER_HIGHEST
        )
        row["bar"].value = max(0.0, min(1.0, progress.percent / 100))

        changed: list[ft.Control] = [row["badge"], row["badge_text"], row["bar"]]
        if progress.message:
            row["message"].value = progress.message
            row["message"].visible = True
            changed.append(row["message"])

        if progress.stage in _TERMINAL_STAGES:
            self.finished_ids.add(progress.lesson.lesson_id)
            done = len(self.finished_ids)
            self.overall_text.value = f"{done}/{self.total} 완료"
            self.overall_bar.value = done / self.total if self.total else 0
            elapsed = time.monotonic() - self.start_time
            if done > 0 and done < self.total:
                remain = elapsed / done * (self.total - done)
                self.eta_text.value = f"예상 남은 시간 {format_hours_minutes(remain)}"
            elif done >= self.total:
                self.eta_text.value = ""
            changed.extend([self.overall_text, self.overall_bar, self.eta_text])

        self.page.update(*changed)

    def _on_cancel_click(self, e: ft.ControlEvent) -> None:
        self.return_after_cancel = True
        self.cancel_event.set()
        self.cancel_button.disabled = True
        self.cancel_button.text = "중단 후 돌아가는 중..."
        self.page.update(self.cancel_button)

    # ------------------------------------------------------------------
    def _on_finished(self, summary: Summary) -> None:
        self.last_summary = summary
        if self.return_after_cancel:
            self._return_to_previous_view()
            return

        self.cancel_button.visible = False

        was_cancelled = (
            self.cancel_event.is_set()
            and (summary.done + summary.skipped + summary.failed) < self.total
        )
        title = "중단됨" if was_cancelled else (
            "처리 종료 · 실패 있음" if summary.failed else "저장·전사 완료"
        )

        content: list[ft.Control] = [
            ft.Text(title, size=16, weight=ft.FontWeight.BOLD),
            ft.Text(
                f"완료 {summary.done}개 · 건너뜀 {summary.skipped}개 · 실패 {summary.failed}개"
            ),
            ft.Row(
                [
                    ft.ElevatedButton(
                        "폴더 열기",
                        icon=ft.Icons.FOLDER_OPEN,
                        on_click=lambda e: _open_folder(self.output_dir),
                    ),
                    ft.OutlinedButton(
                        "강의 선택으로 돌아가기",
                        icon=ft.Icons.ARROW_BACK,
                        on_click=self._on_back_click,
                    ),
                ]
            ),
        ]

        if summary.errors:
            error_list = ft.Column(
                [
                    ft.Text(f"· {title_}: {message}", size=12, color=ft.Colors.ERROR)
                    for title_, message in summary.errors
                ],
                spacing=4,
            )
            content.append(ft.Text("실패한 항목", size=14, weight=ft.FontWeight.BOLD))
            content.append(error_list)
            content.append(
                ft.ElevatedButton(
                    "실패한 항목만 다시 시도",
                    icon=ft.Icons.REPLAY,
                    on_click=self._on_retry_failed,
                )
            )

        self.summary_area.controls = content
        self.summary_area.visible = True
        self.page.update()

    def _on_back_click(self, e: ft.ControlEvent) -> None:
        self._return_to_previous_view()

    def _return_to_previous_view(self) -> None:
        self.app.show_view(self.previous_view)

    def _on_retry_failed(self, e: ft.ControlEvent) -> None:
        if not self.last_summary or not self.last_summary.errors:
            return
        failed_titles = {title for title, _ in self.last_summary.errors}
        retry_lessons = [item for item in self.lessons if item.title in failed_titles]
        if not retry_lessons:
            return
        self.app.begin_download(self.course, retry_lessons, previous_view=self.previous_view)

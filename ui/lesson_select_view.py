"""강의 목차 선택 + 다운로드 설정 화면."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import flet as ft

from ui.core_bridge import Course, Lesson
from ui.format import format_duration, format_hours_minutes

if TYPE_CHECKING:
    from ui.app import App

SPEED_OPTIONS = ["1.25", "1.5", "1.75", "2.0"]
WORKER_OPTIONS = ["1", "2", "3", "4"]


class LessonSelectScreen:
    """섹션별로 묶인 강의 목차 선택 화면과 다운로드 설정 패널."""

    def __init__(self, app: App, course: Course, lessons: list[Lesson]) -> None:
        self.app = app
        self.course = course
        self.lessons = lessons
        # duration_sec 은 표시/예상시간 계산용 부가 정보일 뿐, 영상 유무와 무관하다.
        # 재생시간 정보가 없는 항목도 다운로드 대상이 될 수 있으므로 기본적으로 전부 선택한다.
        self.selected: set[str] = {item.lesson_id for item in lessons}
        self.lesson_checkboxes: dict[str, ft.Checkbox] = {}
        self.section_checkboxes: dict[int, ft.Checkbox] = {}
        self.summary_text = ft.Text(size=13, color=ft.Colors.ON_SURFACE_VARIANT)
        self.select_all_checkbox = ft.Checkbox(
            label="전체 선택", value=True, on_change=self._on_select_all
        )

        self.speed_dropdown = ft.Dropdown(
            label="배속",
            value=str(app.speed),
            options=[ft.DropdownOption(key=v, text=f"{v}배") for v in SPEED_OPTIONS],
            width=140,
            on_select=self._on_speed_change,
        )
        self.output_dir_text = ft.Text(str(app.output_dir), size=13, selectable=True, expand=True)
        self.keep_original_switch = ft.Switch(
            label="원본(1배속)도 함께 보관",
            value=app.keep_original,
            on_change=self._on_keep_original_change,
        )
        self.workers_dropdown = ft.Dropdown(
            label="동시 처리 개수",
            value=str(app.workers),
            options=[ft.DropdownOption(key=v, text=v) for v in WORKER_OPTIONS],
            width=160,
            on_select=self._on_workers_change,
        )
        self.start_button = ft.ElevatedButton(
            "저장·전사 시작",
            icon=ft.Icons.CLOUD_DOWNLOAD,
            height=48,
            on_click=self._on_start_click,
        )
        self.device_dropdown = ft.Dropdown(
            label="녹음할 출력 장치",
            width=360,
            options=[],
            on_select=self._on_device_change,
        )

        self._refresh_summary()
        self.root = self._build_root()
        self.app.show_view(self.root)
        self.app.page.run_task(self._load_devices)

    # ------------------------------------------------------------------
    # 빌드
    # ------------------------------------------------------------------
    def _build_root(self) -> ft.Control:
        header = ft.Row(
            [
                ft.IconButton(
                    icon=ft.Icons.ARROW_BACK,
                    on_click=lambda e: self.app.page.run_task(self.app.show_course_list),
                ),
                ft.Text(self.course.title, size=18, weight=ft.FontWeight.BOLD, expand=True),
            ]
        )

        sections: list[ft.Control] = []
        by_section: dict[int, list[Lesson]] = {}
        for lesson in self.lessons:
            by_section.setdefault(lesson.section_index, []).append(lesson)

        for section_index in sorted(by_section):
            section_lessons = by_section[section_index]
            sections.append(self._build_section(section_index, section_lessons))

        lesson_list = ft.ListView(controls=sections, expand=True, spacing=16)

        settings_panel = ft.Container(
            content=ft.Column(
                [
                    ft.Text("저장 설정", size=14, weight=ft.FontWeight.BOLD),
                    ft.Row([self.speed_dropdown, self.workers_dropdown], spacing=16),
                    ft.Row(
                        [
                            ft.Text("저장 폴더:", size=13),
                            self.output_dir_text,
                            ft.TextButton(
                                "변경", icon=ft.Icons.FOLDER_OPEN, on_click=self._on_change_folder
                            ),
                        ],
                        spacing=8,
                    ),
                    self.device_dropdown,
                    self.keep_original_switch,
                    ft.Text(
                        "암호화된 강의는 MP4를 받을 수 없습니다. 저장을 시작하면 앱 창에서 "
                        "재생하고, 선택한 출력 장치 소리를 WAV로 저장한 뒤 원래 속도로 전사합니다. "
                        "Chrome Remote Desktop 같은 원격 제어·화면 녹화 프로그램이 켜져 있으면 "
                        "플레이어가 재생을 중단합니다.",
                        size=12,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                ],
                spacing=8,
            ),
            padding=16,
            border_radius=10,
            bgcolor=ft.Colors.SURFACE_CONTAINER,
        )

        bottom_bar = ft.Row(
            [self.summary_text, self.start_button],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        return ft.Column(
            [
                header,
                ft.TextButton(
                    "앱 내장 브라우저에서 강의 미리 열기",
                    icon=ft.Icons.PLAY_CIRCLE_OUTLINE,
                    on_click=lambda e: self.app.page.run_task(
                        self.app.open_embedded_browser, self.course
                    ),
                    disabled=self.app.demo,
                ),
                ft.Row([self.select_all_checkbox]),
                ft.Divider(),
                lesson_list,
                settings_panel,
                bottom_bar,
            ],
            expand=True,
            spacing=10,
        )

    def _build_section(self, section_index: int, lessons: list[Lesson]) -> ft.Control:
        section_checkbox = ft.Checkbox(
            label=lessons[0].section_title,
            value=all(item.lesson_id in self.selected for item in lessons),
            on_change=lambda e, si=section_index: self._on_section_toggle(si, e.control.value),
        )
        self.section_checkboxes[section_index] = section_checkbox

        rows = [self._build_lesson_row(lesson) for lesson in lessons]

        return ft.Column(
            [section_checkbox, ft.Column(rows, spacing=2)],
            spacing=4,
        )

    def _build_lesson_row(self, lesson: Lesson) -> ft.Control:
        checkbox = ft.Checkbox(
            value=lesson.lesson_id in self.selected,
            on_change=lambda e, item=lesson: self._on_lesson_toggle(item, e.control.value),
        )
        self.lesson_checkboxes[lesson.lesson_id] = checkbox

        return ft.Container(
            content=ft.Row(
                [
                    checkbox,
                    ft.Text(lesson.title, expand=True),
                    ft.Text(
                        format_duration(lesson.duration_sec),
                        size=12,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                ],
            ),
            padding=ft.Padding(left=24, top=0, right=8, bottom=0),
        )

    # ------------------------------------------------------------------
    # 이벤트
    # ------------------------------------------------------------------
    def _on_lesson_toggle(self, lesson: Lesson, value: bool) -> None:
        if value:
            self.selected.add(lesson.lesson_id)
        else:
            self.selected.discard(lesson.lesson_id)

        section_lessons = [
            item for item in self.lessons if item.section_index == lesson.section_index
        ]
        section_checkbox = self.section_checkboxes.get(lesson.section_index)
        if section_checkbox is not None:
            section_checkbox.value = all(
                item.lesson_id in self.selected for item in section_lessons
            )
        self.select_all_checkbox.value = len(self.selected) == len(self.lessons)

        self._refresh_summary()
        self.app.page.update()

    def _on_section_toggle(self, section_index: int, value: bool) -> None:
        for lesson in self.lessons:
            if lesson.section_index != section_index:
                continue
            if value:
                self.selected.add(lesson.lesson_id)
            else:
                self.selected.discard(lesson.lesson_id)
            self.lesson_checkboxes[lesson.lesson_id].value = value

        self.select_all_checkbox.value = len(self.selected) == len(self.lessons)
        self._refresh_summary()
        self.app.page.update()

    def _on_select_all(self, e: ft.ControlEvent) -> None:
        value = e.control.value
        for lesson in self.lessons:
            if value:
                self.selected.add(lesson.lesson_id)
            else:
                self.selected.discard(lesson.lesson_id)
            self.lesson_checkboxes[lesson.lesson_id].value = value
        for section_checkbox in self.section_checkboxes.values():
            section_checkbox.value = value
        self._refresh_summary()
        self.app.page.update()

    def _on_speed_change(self, e: ft.ControlEvent) -> None:
        self.app.speed = float(self.speed_dropdown.value)
        self._refresh_summary()
        self.summary_text.update()

    def _on_workers_change(self, e: ft.ControlEvent) -> None:
        self.app.workers = int(self.workers_dropdown.value)

    def _on_keep_original_change(self, e: ft.ControlEvent) -> None:
        self.app.keep_original = self.keep_original_switch.value

    def _on_device_change(self, e: ft.ControlEvent) -> None:
        if self.device_dropdown.value is None:
            self.app.capture_device_index = None
            return
        self.app.capture_device_index = int(self.device_dropdown.value)

    async def _load_devices(self) -> None:
        if self.app.demo:
            return
        try:
            from pickiclass.audio_capture import default_loopback_device, list_loopback_devices

            devices = await asyncio.to_thread(list_loopback_devices)
            default = await asyncio.to_thread(default_loopback_device)
            self.device_dropdown.options = [
                ft.DropdownOption(key=str(item["index"]), text=item["name"]) for item in devices
            ]
            chosen = default or (devices[0] if devices else None)
            if chosen and self.device_dropdown.value is None:
                self.device_dropdown.value = str(chosen["index"])
                self.app.capture_device_index = chosen["index"]
            from pickiclass.playback import installed_capture_programs

            blockers = await asyncio.to_thread(installed_capture_programs)
            if blockers:
                self.app.show_snack_bar(
                    f"{', '.join(blockers)}가 설치되어 있습니다. "
                    "원격 접속 중이 아니어도 Chrome이 켜져 있으면 Kollus가 재생을 중단하니 "
                    "Chrome을 완전히 종료한 뒤 저장하세요."
                )
            self.app.page.update()
        except Exception as exc:  # noqa: BLE001 - 장치 오류는 시작 전에 안내한다.
            self.app.show_snack_bar(f"출력 장치 조회 실패: {exc}")

    async def _on_change_folder(self, e: ft.ControlEvent) -> None:
        picked = await self.app.pick_output_directory()
        if picked:
            self.output_dir_text.value = picked
            self.output_dir_text.update()

    def _on_start_click(self, e: ft.ControlEvent) -> None:
        chosen = [item for item in self.lessons if item.lesson_id in self.selected]
        if not chosen:
            self.app.show_snack_bar("저장할 강의를 하나 이상 선택해주세요.")
            return
        if not self.app.demo and self.app.capture_device_index is None:
            self.app.show_snack_bar("암호화 강의 저장을 위해 출력 장치를 선택하세요.")
            return
        self.app.begin_download(self.course, chosen)

    # ------------------------------------------------------------------
    def _refresh_summary(self) -> None:
        selected_lessons = [item for item in self.lessons if item.lesson_id in self.selected]
        count = len(selected_lessons)
        known = [item for item in selected_lessons if item.duration_sec is not None]
        unknown_count = count - len(known)

        if count == 0:
            self.summary_text.value = "선택된 강의가 없습니다."
            return

        if not known:
            # 선택한 항목 전부 재생시간 정보가 없는 경우 (예: 목차 전체가 정보 없음인 강의)
            self.summary_text.value = f"선택 {count}개 · 재생시간 정보 없음"
            return

        total_sec = sum(item.duration_sec for item in known)
        sped_sec = total_sec / self.app.speed if self.app.speed else total_sec
        if unknown_count:
            # 일부만 알 수 있으면 합산값이 실제보다 적을 수 있다는 것을 알려준다.
            self.summary_text.value = (
                f"선택 {count}개 · 총 재생시간 {format_hours_minutes(total_sec)} 이상"
                f"(일부 미표시) · {self.app.speed}배속 예상 "
                f"{format_hours_minutes(sped_sec)} 이상"
            )
        else:
            self.summary_text.value = (
                f"선택 {count}개 · 총 재생시간 {format_hours_minutes(total_sec)} · "
                f"{self.app.speed}배속 예상 {format_hours_minutes(sped_sec)}"
            )

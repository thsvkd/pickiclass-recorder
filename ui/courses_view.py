"""강의 목록(선택) 화면."""

from __future__ import annotations

from typing import TYPE_CHECKING

import flet as ft

from ui.core_bridge import Course

if TYPE_CHECKING:
    from ui.app import App


def build_loading_view(message: str) -> ft.Control:
    return ft.Container(
        content=ft.Column(
            [ft.ProgressRing(), ft.Text(message, color=ft.Colors.ON_SURFACE_VARIANT)],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=16,
        ),
        alignment=ft.Alignment.CENTER,
        expand=True,
    )


def build_error_view(message: str, on_retry) -> ft.Control:
    return ft.Container(
        content=ft.Column(
            [
                ft.Icon(ft.Icons.ERROR_OUTLINE, color=ft.Colors.ERROR, size=40),
                ft.Text(message, color=ft.Colors.ERROR, text_align=ft.TextAlign.CENTER),
                ft.ElevatedButton("다시 시도", on_click=on_retry),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=16,
        ),
        alignment=ft.Alignment.CENTER,
        expand=True,
        padding=32,
    )


def build_course_list_view(app: App, courses: list[Course]) -> ft.Control:
    list_view = ft.ListView(expand=True, spacing=8)

    def render(items: list[Course]) -> None:
        list_view.controls = [_build_course_card(app, c) for c in items]

    def on_search_change(e: ft.ControlEvent) -> None:
        keyword = (search_field.value or "").strip().lower()
        filtered = [c for c in courses if keyword in c.title.lower()] if keyword else courses
        render(filtered)
        list_view.update()

    search_field = ft.TextField(
        label="강의 검색",
        prefix_icon=ft.Icons.SEARCH,
        on_change=on_search_change,
        width=360,
    )

    render(courses)

    if not courses:
        empty = ft.Text("내 강의가 없습니다.", color=ft.Colors.ON_SURFACE_VARIANT)
        body: ft.Control = ft.Container(content=empty, alignment=ft.Alignment.CENTER, expand=True)
    else:
        body = list_view

    return ft.Column(
        [
            ft.Row(
                [
                    ft.Text("내 강의", size=20, weight=ft.FontWeight.BOLD),
                    ft.TextButton(
                        "로그아웃",
                        icon=ft.Icons.LOGOUT,
                        on_click=lambda e: app.page.run_task(app.show_login),
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            ft.ElevatedButton(
                "로컬 영상·음성 파일 전사", icon=ft.Icons.TRANSCRIBE,
                on_click=lambda e: app.show_transcription(),
            ),
            search_field,
            ft.Divider(),
            body,
        ],
        expand=True,
        spacing=12,
    )


def _build_course_card(app: App, course: Course) -> ft.Control:
    return ft.Container(
        content=ft.Row(
            [
                ft.Icon(ft.Icons.PLAY_CIRCLE_OUTLINE, color=ft.Colors.PRIMARY),
                ft.Text(course.title, expand=True, size=15),
                ft.Icon(ft.Icons.CHEVRON_RIGHT, color=ft.Colors.ON_SURFACE_VARIANT),
            ],
            spacing=12,
        ),
        padding=16,
        border_radius=10,
        bgcolor=ft.Colors.SURFACE_CONTAINER,
        ink=True,
        on_click=lambda e, c=course: app.page.run_task(app.show_lesson_select, c),
    )

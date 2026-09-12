"""로그인 화면."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import flet as ft

from ui.core_bridge import LoginError

if TYPE_CHECKING:
    from ui.app import App


def build_login_view(
    app: App, initial_email: str, initial_password: str, remember: bool
) -> ft.Control:
    email_field = ft.TextField(
        label="아이디",
        value=initial_email,
        width=340,
        autofocus=not initial_email,
    )
    password_field = ft.TextField(
        label="비밀번호",
        value=initial_password,
        password=True,
        can_reveal_password=True,
        width=340,
    )
    error_text = ft.Text("", color=ft.Colors.ERROR, size=13, visible=False)
    remember_checkbox = ft.Checkbox(label="아이디 기억하기", value=remember)
    remember_hint = ft.Text(
        "체크하면 이 기기에 아이디만 저장됩니다. 비밀번호는 저장하지 않습니다.",
        size=12,
        color=ft.Colors.ON_SURFACE_VARIANT,
    )
    progress_ring = ft.ProgressRing(width=16, height=16, visible=False)
    login_button = ft.ElevatedButton("로그인", width=340, height=44)

    def set_busy(busy: bool) -> None:
        login_button.disabled = busy
        email_field.disabled = busy
        password_field.disabled = busy
        progress_ring.visible = busy
        login_button.content = ft.Row(
            [progress_ring, ft.Text("로그인 중..." if busy else "로그인")],
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=8,
        )

    async def on_submit(e: ft.ControlEvent) -> None:
        email = (email_field.value or "").strip()
        password = password_field.value or ""
        error_text.visible = False
        set_busy(True)
        app.page.update()

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, app.client.login, email, password)
        except LoginError:
            error_text.value = "아이디 또는 비밀번호가 올바르지 않습니다."
            error_text.visible = True
        except Exception as ex:  # noqa: BLE001 - 사용자에게 원인을 그대로 알려준다
            error_text.value = f"로그인 중 문제가 발생했습니다: {ex}"
            error_text.visible = True
        else:
            await app.save_login_prefs(email, remember_checkbox.value)
            await app.show_course_list()
            return

        set_busy(False)
        app.page.update()

    login_button.on_click = on_submit
    password_field.on_submit = on_submit

    set_busy(False)

    card = ft.Container(
        content=ft.Column(
            [
                ft.Text("피키클래스 파일 저장 및 전사", size=22, weight=ft.FontWeight.BOLD),
                ft.Text(
                    "피키클래스 계정으로 로그인하면 내 강의 목록을 불러옵니다.",
                    size=13,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
                ft.Container(height=16),
                email_field,
                password_field,
                error_text,
                ft.Row([remember_checkbox]),
                remember_hint,
                ft.Container(height=8),
                login_button,
                ft.Divider(),
                ft.ElevatedButton(
                    "로컬 영상·음성 파일 전사",
                    icon=ft.Icons.TRANSCRIBE,
                    on_click=lambda e: app.show_transcription(),
                ),
                ft.Container(height=4),
                ft.Text(
                    f"v{app.get_display_version()}",
                    size=11,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                    text_align=ft.TextAlign.CENTER,
                    width=340,
                ),
            ],
            spacing=8,
            horizontal_alignment=ft.CrossAxisAlignment.START,
            tight=True,
        ),
        padding=32,
        border_radius=12,
        bgcolor=ft.Colors.SURFACE_CONTAINER,
        width=420,
    )

    return ft.Container(
        content=card,
        alignment=ft.Alignment.CENTER,
        expand=True,
    )

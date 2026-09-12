"""ffmpeg 를 사용할 수 없을 때 보여주는 안내 화면."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import flet as ft

if TYPE_CHECKING:
    from ui.app import App


def _manual_install_command() -> str:
    """자동 설치가 실패했을 때 안내할 수동 설치 명령어."""
    if sys.platform.startswith("win"):
        return "winget install Gyan.FFmpeg"
    if sys.platform == "darwin":
        return "brew install ffmpeg"
    return "sudo apt install ffmpeg"


def build_ffmpeg_missing_view(app: App) -> ft.Control:
    command = _manual_install_command()
    # 받침이 달라 조사도 함께 바꾼다 ("명령 프롬프트를" / "터미널을").
    prompt = "명령 프롬프트를" if sys.platform.startswith("win") else "터미널을"
    return ft.Container(
        content=ft.Column(
            [
                ft.Icon(ft.Icons.WARNING_AMBER, size=40, color=ft.Colors.ERROR),
                ft.Text("ffmpeg를 사용할 수 없습니다", size=18, weight=ft.FontWeight.BOLD),
                ft.Text(
                    "영상을 1.5배속으로 변환하려면 ffmpeg가 필요합니다.\n"
                    f"{prompt} 열고 아래 명령어를 실행해 설치한 뒤 앱을 다시 시작해주세요.",
                    text_align=ft.TextAlign.CENTER,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
                ft.Container(
                    content=ft.Text(command, selectable=True, font_family="monospace"),
                    padding=12,
                    border_radius=8,
                    bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
                ),
                ft.ElevatedButton(
                    "다시 확인",
                    icon=ft.Icons.REFRESH,
                    on_click=lambda e: app.page.run_task(app.start),
                ),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=16,
        ),
        alignment=ft.Alignment.CENTER,
        expand=True,
        padding=32,
    )

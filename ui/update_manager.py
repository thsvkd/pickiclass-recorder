"""Velopack 자동 업데이트 UI 배선.

pickiclass.velopack_update 가 없거나(HAS_VELOPACK=False) 소스로 직접 실행 중이면
(is_installed()==False) 업데이트 관련 UI를 아예 노출하지 않는다. 네트워크 호출은
전부 스레드로 넘기고, 실패는 조용히 무시한다 (사용자에게 에러 팝업을 띄우지 않음).

PICKICLASS_UI_DEMO=1 데모 모드에서는 실제 velopack 대신 ui.demo_data 의 가짜 구현을 써서
"새 버전 있음 -> 다운로드 -> 재시작 확인" 시나리오를 그대로 태워볼 수 있게 한다.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

import flet as ft

from ui.core_bridge import HAS_VELOPACK
from ui.core_bridge import apply_and_restart as core_apply_and_restart
from ui.core_bridge import check_update as core_check_update
from ui.core_bridge import download_update as core_download_update
from ui.core_bridge import is_installed as core_is_installed
from ui.core_bridge import run_startup_maintenance as core_run_startup_maintenance
from ui.core_bridge import target_version as core_target_version
from ui.demo_data import (
    demo_apply_and_restart,
    demo_check_update,
    demo_download_update,
    demo_is_installed,
    demo_run_startup_maintenance,
    demo_target_version,
)

if TYPE_CHECKING:
    from ui.app import App


class UpdateManager:
    """앱 시작 시 유지보수/업데이트 확인을 수행하고, 상단 배너로 진행 상황을 보여준다."""

    def __init__(self, app: App) -> None:
        self.app = app
        self.update_info: Any = None
        self._checked = False

        # 데모 모드에서는 ui.demo_data 의 가짜 구현으로 전체 시나리오를 태워본다.
        if app.demo:
            self._run_startup_maintenance = demo_run_startup_maintenance
            self._is_installed = demo_is_installed
            self._check_update = demo_check_update
            self._target_version = demo_target_version
            self._download_update = demo_download_update
            self._apply_and_restart = demo_apply_and_restart
        else:
            self._run_startup_maintenance = core_run_startup_maintenance
            self._is_installed = core_is_installed
            self._check_update = core_check_update
            self._target_version = core_target_version
            self._download_update = core_download_update
            self._apply_and_restart = core_apply_and_restart

    def _enabled(self) -> bool:
        return self.app.demo or HAS_VELOPACK

    async def run_startup_maintenance(self) -> None:
        """앱 시작 시 1회: 대기 중인 업데이트 적용 + 오래된 패키지 정리."""
        if not self._enabled():
            return
        loop = asyncio.get_running_loop()
        # 유지보수 실패는 앱 흐름을 막지 않는다.
        with contextlib.suppress(Exception):
            await loop.run_in_executor(None, self._run_startup_maintenance)

    async def check_in_background(self) -> None:
        """새 버전이 있으면 상단 배너로 조용히 알린다. 실패 시 그냥 아무 것도 하지 않는다."""
        if not self._enabled() or self._checked:
            return
        self._checked = True

        loop = asyncio.get_running_loop()
        try:
            installed = await loop.run_in_executor(None, self._is_installed)
            if not installed:
                return
            info = await loop.run_in_executor(None, self._check_update)
        except Exception:  # noqa: BLE001
            return

        if info is None:
            return
        self.update_info = info
        self._show_available_banner(info)

    # ------------------------------------------------------------------
    def _show_available_banner(self, info: Any) -> None:
        try:
            version = self._target_version(info)
        except Exception:  # noqa: BLE001
            version = ""

        label = f"새 버전{f' ({version})' if version else ''}이 있습니다."
        row = ft.Row(
            [
                ft.Icon(ft.Icons.SYSTEM_UPDATE, size=18, color=ft.Colors.ON_PRIMARY_CONTAINER),
                ft.Text(label, color=ft.Colors.ON_PRIMARY_CONTAINER, expand=True),
                ft.TextButton(
                    "업데이트",
                    on_click=lambda e: self.app.page.run_task(self._start_download, info),
                ),
                ft.IconButton(
                    icon=ft.Icons.CLOSE,
                    icon_color=ft.Colors.ON_PRIMARY_CONTAINER,
                    on_click=lambda e: self.app.hide_banner(),
                ),
            ],
            spacing=8,
        )
        self.app.show_banner(self._wrap_banner(row))

    async def _start_download(self, info: Any) -> None:
        progress_text = ft.Text("업데이트를 받는 중... 0%", color=ft.Colors.ON_PRIMARY_CONTAINER)
        progress_bar = ft.ProgressBar(value=0, expand=True)
        column = ft.Column([progress_text, progress_bar], spacing=4)
        self.app.show_banner(self._wrap_banner(column))

        def on_progress(fraction: float) -> None:
            # download() 의 진행률 콜백은 별도 스레드에서 호출되므로 run_thread 로 안전하게 넘긴다.
            self.app.page.run_thread(
                self._apply_download_progress, progress_text, progress_bar, fraction
            )

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._download_update, info, on_progress)
        except Exception:  # noqa: BLE001
            self.app.hide_banner()
            return

        self._show_ready_banner(info)

    def _apply_download_progress(
        self, text_ctl: ft.Text, bar_ctl: ft.ProgressBar, fraction: float
    ) -> None:
        text_ctl.value = f"업데이트를 받는 중... {int(fraction * 100)}%"
        bar_ctl.value = fraction
        self.app.page.update(text_ctl, bar_ctl)

    def _show_ready_banner(self, info: Any) -> None:
        if self.app.is_download_active():
            row = ft.Row(
                [
                    ft.Icon(ft.Icons.CHECK_CIRCLE, size=18, color=ft.Colors.ON_PRIMARY_CONTAINER),
                    ft.Text(
                        "업데이트 준비가 끝났습니다. 진행 중인 강의 다운로드가 끝난 뒤 "
                        "앱을 다시 켜면 적용됩니다.",
                        color=ft.Colors.ON_PRIMARY_CONTAINER,
                        expand=True,
                    ),
                    ft.IconButton(
                        icon=ft.Icons.CLOSE,
                        icon_color=ft.Colors.ON_PRIMARY_CONTAINER,
                        on_click=lambda e: self.app.hide_banner(),
                    ),
                ],
                spacing=8,
            )
        else:
            row = ft.Row(
                [
                    ft.Icon(ft.Icons.CHECK_CIRCLE, size=18, color=ft.Colors.ON_PRIMARY_CONTAINER),
                    ft.Text(
                        "업데이트 준비가 끝났습니다. 지금 재시작할까요?",
                        color=ft.Colors.ON_PRIMARY_CONTAINER,
                        expand=True,
                    ),
                    ft.TextButton("나중에", on_click=lambda e: self.app.hide_banner()),
                    ft.ElevatedButton("지금 재시작", on_click=lambda e: self._restart_now(info)),
                ],
                spacing=8,
            )
        self.app.show_banner(self._wrap_banner(row))

    def _restart_now(self, info: Any) -> None:
        # 배너를 띄운 뒤 사용자가 버튼을 누르기 전에 강의 다운로드가 시작됐을 수 있으므로
        # 실제로 재시작하기 직전에 한 번 더 확인한다.
        if self.app.is_download_active():
            self._show_ready_banner(info)
            return
        # 정상적으로는 _apply_and_restart 안에서 프로세스가 종료되어 돌아오지 않는다.
        with contextlib.suppress(Exception):
            self._apply_and_restart(info)
        if self.app.demo:
            # 데모에서는 실제로 재시작하지 않으므로, 눌렀다는 사실만 알려준다.
            self.app.hide_banner()
            self.app.show_snack_bar("데모 모드: 실제 앱이었다면 지금 재시작됩니다.")

    @staticmethod
    def _wrap_banner(content: ft.Control) -> ft.Control:
        return ft.Container(content=content, padding=12, bgcolor=ft.Colors.PRIMARY_CONTAINER)

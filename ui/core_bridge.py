"""코어 패키지(pickiclass)를 안전하게 불러오는 브릿지.

코어 모듈(client.py / recorder.py)이 아직 없어도
GUI가 임포트 에러 없이 실행되도록, 없는 모듈은 None으로 대체한다.
models.py는 이미 존재하므로 항상 그대로 가져온다.
"""

from __future__ import annotations

from pickiclass.models import Course, Lesson, Progress, Summary

try:
    from pickiclass.client import LoginError, PickiclassClient

    HAS_CLIENT = True
except ImportError:
    HAS_CLIENT = False

    class LoginError(Exception):
        """코어 미구현 시 사용하는 자리표시자."""

    PickiclassClient = None  # type: ignore[assignment,misc]

try:
    from pickiclass.recorder import Recorder

    HAS_RECORDER = True
except ImportError:
    HAS_RECORDER = False
    Recorder = None  # type: ignore[assignment,misc]

try:
    from pickiclass.velopack_update import (
        REPO_URL,
        apply_and_restart,
        current_version,
        is_installed,
        run_startup_maintenance,
        target_version,
    )
    from pickiclass.velopack_update import check as check_update
    from pickiclass.velopack_update import download as download_update

    HAS_VELOPACK = True
except ImportError:
    HAS_VELOPACK = False
    REPO_URL = None  # type: ignore[assignment]

    def run_startup_maintenance() -> None:
        """코어 미구현 시 사용하는 자리표시자. 아무 것도 하지 않는다."""

    def is_installed() -> bool:
        return False

    def current_version() -> str | None:
        return None

    def check_update():  # type: ignore[no-untyped-def]
        return None

    def target_version(info) -> str:  # type: ignore[no-untyped-def]
        return ""

    def download_update(info, progress_cb=None) -> None:  # type: ignore[no-untyped-def]
        """코어 미구현 시 사용하는 자리표시자."""

    def apply_and_restart(info) -> None:  # type: ignore[no-untyped-def]
        """코어 미구현 시 사용하는 자리표시자."""


def get_package_version() -> str:
    """비설치 실행 등 current_version()이 없을 때 표시할 패키지 버전 폴백."""
    import pickiclass

    return getattr(pickiclass, "__version__", "dev")


__all__ = [
    "HAS_CLIENT",
    "HAS_RECORDER",
    "HAS_VELOPACK",
    "REPO_URL",
    "Course",
    "Lesson",
    "LoginError",
    "PickiclassClient",
    "Progress",
    "Recorder",
    "Summary",
    "apply_and_restart",
    "check_update",
    "current_version",
    "download_update",
    "get_package_version",
    "is_installed",
    "run_startup_maintenance",
    "target_version",
]

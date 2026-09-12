"""ffmpeg/ffprobe 탐색 및 자동 설치.

PATH에 없으면 앱 데이터 폴더에 다운로드해서 사용한다.
자동 다운로드는 macOS(evermeet.cx 빌드)와 Windows(gyan.dev 빌드)를 지원한다.
"""

from __future__ import annotations

import io
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path

import requests

# evermeet.cx의 배포 경로는 ``/ffmpeg/getrelease/<도구>/zip`` 형태다. 도구 이름을 맨 앞에 두는
# ``/ffprobe/getrelease/zip``은 404가 아니라 **ffmpeg zip으로 302 리다이렉트**되므로, 받아 놓고
# 압축을 풀면 안에 ffprobe가 없어 "압축 해제 실패"로 보인다. 경로를 바꿀 때 주의할 것.
FFMPEG_ZIP_URL = "https://evermeet.cx/ffmpeg/getrelease/ffmpeg/zip"
FFPROBE_ZIP_URL = "https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip"

# Windows용 gyan.dev essentials 빌드는 **하나의 zip 안에 ffmpeg.exe와 ffprobe.exe가 함께**
# 들어 있다(``ffmpeg-<버전>-essentials_build/bin/``). macOS처럼 도구별로 따로 받으면 같은
# 80MB짜리 파일을 두 번 받게 되므로 한 번만 받아서 두 실행 파일을 꺼낸다.
WINDOWS_ZIP_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

# GUI(Flet) 앱에서 ffmpeg/ffprobe를 실행하면 Windows는 콘솔 창을 새로 띄운다. 설치 직후 실행
# 확인은 물론 recorder의 다운로드·변환 호출에서도 검은 창이 뜨는 것을 막는다(강의 수만큼
# 반복되므로 특히 거슬린다). Windows가 아니면 0이라 아무 영향이 없다.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class FFmpegNotFound(Exception):
    """ffmpeg/ffprobe를 찾거나 설치할 수 없을 때 발생한다."""


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def binary_name(tool: str) -> str:
    """플랫폼에 맞는 실행 파일 이름. Windows에서는 ``.exe``가 붙는다."""
    return f"{tool}.exe" if _is_windows() else tool


def _app_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PickiclassRecorder" / "bin"
    if _is_windows():
        base = os.environ.get("APPDATA", str(Path.home()))
        return Path(base) / "PickiclassRecorder" / "bin"
    base = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    return Path(base) / "PickiclassRecorder" / "bin"


def find_ffmpeg() -> tuple[Path, Path] | None:
    """(ffmpeg, ffprobe) 경로를 찾는다. 없으면 None."""
    app_dir = _app_data_dir()
    local_ffmpeg = app_dir / binary_name("ffmpeg")
    local_ffprobe = app_dir / binary_name("ffprobe")
    if local_ffmpeg.exists() and local_ffprobe.exists():
        return local_ffmpeg, local_ffprobe

    path_ffmpeg = shutil.which("ffmpeg")
    path_ffprobe = shutil.which("ffprobe")
    if path_ffmpeg and path_ffprobe:
        return Path(path_ffmpeg), Path(path_ffprobe)

    return None


def install_ffmpeg(
    on_progress: Callable[[float], None] | None = None,
) -> tuple[Path, Path]:
    """ffmpeg/ffprobe를 앱 데이터 폴더에 다운로드한다. (macOS / Windows)"""
    if sys.platform == "darwin":
        return _install_macos(on_progress)
    if _is_windows():
        return _install_windows(on_progress)
    raise FFmpegNotFound(
        "ffmpeg 자동 설치는 macOS와 Windows에서만 지원됩니다. "
        "패키지 매니저(apt, dnf 등)로 ffmpeg를 직접 설치한 뒤 PATH에 추가해주세요."
    )


def _scaled(
    on_progress: Callable[[float], None] | None, start: float, end: float
) -> Callable[[float], None] | None:
    if on_progress is None:
        return None
    return lambda ratio: on_progress(start + (end - start) * ratio)


def _install_macos(on_progress: Callable[[float], None] | None) -> tuple[Path, Path]:
    """evermeet.cx는 도구마다 zip이 따로라 두 번 받는다."""
    app_dir = _app_data_dir()
    app_dir.mkdir(parents=True, exist_ok=True)

    ffmpeg_path = _install_from_url(
        FFMPEG_ZIP_URL, app_dir, ["ffmpeg"], _scaled(on_progress, 0.0, 0.5)
    )[0]
    ffprobe_path = _install_from_url(
        FFPROBE_ZIP_URL, app_dir, ["ffprobe"], _scaled(on_progress, 0.5, 1.0)
    )[0]

    if on_progress is not None:
        on_progress(1.0)

    return ffmpeg_path, ffprobe_path


def _install_windows(on_progress: Callable[[float], None] | None) -> tuple[Path, Path]:
    """gyan.dev essentials zip 하나에서 ffmpeg.exe와 ffprobe.exe를 함께 꺼낸다."""
    app_dir = _app_data_dir()
    app_dir.mkdir(parents=True, exist_ok=True)

    # 압축 해제·실행 확인에도 시간이 걸리므로 다운로드는 0.9까지만 차지하게 둔다.
    ffmpeg_path, ffprobe_path = _install_from_url(
        WINDOWS_ZIP_URL, app_dir, ["ffmpeg", "ffprobe"], _scaled(on_progress, 0.0, 0.9)
    )

    if on_progress is not None:
        on_progress(1.0)

    return ffmpeg_path, ffprobe_path


def _install_from_url(
    url: str,
    dest_dir: Path,
    tools: list[str],
    on_progress: Callable[[float], None] | None,
) -> list[Path]:
    """``url``의 zip을 받아 ``tools``에 적힌 실행 파일들을 ``dest_dir``에 설치한다."""
    with requests.get(url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length", 0))
        buf = io.BytesIO()
        downloaded = 0
        for chunk in resp.iter_content(chunk_size=65536):
            buf.write(chunk)
            downloaded += len(chunk)
            if on_progress is not None and total:
                on_progress(min(1.0, downloaded / total))

    installed = []
    for tool in tools:
        binary_path = _extract_binary(buf, dest_dir, binary_name(tool))

        mode = binary_path.stat().st_mode
        binary_path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        _verify_runnable(binary_path)
        installed.append(binary_path)

    return installed


def _extract_binary(archive: io.BytesIO, dest_dir: Path, filename: str) -> Path:
    """압축 안에서 ``filename`` 파일 하나만 꺼내 ``dest_dir``에 놓는다.

    통째로 풀지 않고 이름으로 찾는 이유는, 배포처가 엉뚱한 압축 파일을 돌려줬을 때
    "압축 해제 실패"라는 뭉뚱그린 메시지 대신 **안에 뭐가 들어 있었는지**를 남기기
    위해서다. 실제로 ffprobe URL이 ffmpeg zip으로 리다이렉트되던 버그가 그렇게 숨었다.
    """
    with zipfile.ZipFile(archive) as zf:
        member = next(
            (
                info
                for info in zf.infolist()
                if not info.is_dir() and Path(info.filename).name == filename
            ),
            None,
        )
        if member is None:
            contents = ", ".join(zf.namelist()[:10]) or "(빈 압축 파일)"
            raise FFmpegNotFound(
                f"내려받은 압축 파일에 {filename}이(가) 없습니다. 들어 있던 파일: {contents}"
            )
        # 중간에 끊겨 반쪽짜리 파일이 남으면 다음 실행 때 find_ffmpeg가 그걸 정상으로 보고
        # 집어 든다. 임시 이름으로 다 받은 뒤 한 번에 갈아 끼운다.
        binary_path = dest_dir / filename
        tmp_path = dest_dir / f".{filename}.part"
        with zf.open(member) as src, tmp_path.open("wb") as out:
            shutil.copyfileobj(src, out)
        tmp_path.replace(binary_path)

    return binary_path


def _verify_runnable(binary_path: Path) -> None:
    """받은 실행 파일이 이 기기에서 실제로 실행되는지 확인한다.

    evermeet.cx 빌드는 x86_64 전용이라 Apple Silicon에서는 Rosetta 2가 있어야 돈다.
    없으면 exec 단계에서 "Bad CPU type" (OSError)로 죽는데, 그 시점이 다운로드가 아니라
    한참 뒤 변환 중이라 원인을 짚기 어렵다. 여기서 미리 걸러 낸다.
    """
    try:
        result = subprocess.run(
            [str(binary_path), "-version"],
            capture_output=True,
            timeout=30,
            check=False,
            creationflags=NO_WINDOW,
        )
    except OSError as exc:
        hint = (
            "백신·SmartScreen이 실행 파일을 막았는지 확인한 뒤 다시 시도하세요."
            if _is_windows()
            else "Apple Silicon에서는 Rosetta 2가 필요합니다. 터미널에서 "
            "`softwareupdate --install-rosetta`를 실행한 뒤 다시 시도하세요."
        )
        raise FFmpegNotFound(
            f"{binary_path.name}을(를) 실행할 수 없습니다 ({exc}). {hint}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise FFmpegNotFound(f"{binary_path.name} 실행 확인이 시간 초과됐습니다.") from exc
    if result.returncode != 0:
        detail = (result.stderr or b"").decode("utf-8", "replace").strip()
        raise FFmpegNotFound(
            f"{binary_path.name} 실행 확인에 실패했습니다 (종료 코드 {result.returncode}). {detail}"
        )

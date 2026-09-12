"""앱 소유 WebView2 재생 창. 인증 정보는 자식 프로세스의 stdin으로만 전달한다."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from pickiclass.client import SourceUnavailableError


def browser_payload(
    client,
    course_id: str,
    lesson_id: str | None = None,
    lesson_index: int | None = None,
) -> dict:
    url = client._course_urls.get(course_id)
    if not url:
        raise SourceUnavailableError("먼저 내 강의 목록을 불러오세요.")
    cookies = [
        {"name": c.name, "value": c.value, "domain": c.domain,
         "path": c.path or "/", "secure": c.secure,
         "http_only": c.has_nonstandard_attr("HttpOnly")}
        for c in client.session.cookies
        if c.domain.lstrip(".") == "pickiclass.com"
    ]
    payload = {"url": url, "cookies": cookies}
    if lesson_id is not None:
        payload["lesson_id"] = lesson_id
    if lesson_index is not None:
        payload["lesson_index"] = lesson_index
    return payload


def launch_browser(client, course_id: str) -> subprocess.Popen:
    return start_browser_process(browser_payload(client, course_id), capture_stdout=False)


def start_browser_process(payload: dict, *, capture_stdout: bool) -> subprocess.Popen:
    validate_payload(payload)
    executable = Path(sys.executable).with_name("python.exe")
    options = {
        "args": [str(executable), "-m", "pickiclass.browser_host"],
        "cwd": Path(__file__).resolve().parents[1],
        "stdin": subprocess.PIPE,
        "stderr": subprocess.DEVNULL,
        "creationflags": subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    }
    if capture_stdout:
        options.update(stdout=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1)
    else:
        options["stdout"] = subprocess.DEVNULL
    process = subprocess.Popen(**options)
    body = json.dumps(payload)
    try:
        assert process.stdin is not None
        process.stdin.write(body if capture_stdout else body.encode("utf-8"))
        process.stdin.close()
    except OSError:
        process.terminate()
        raise RuntimeError("내장 브라우저를 시작하지 못했습니다.") from None
    return process


def validate_payload(payload: dict) -> None:
    import re
    from urllib.parse import urlparse

    url = urlparse(payload.get("url", ""))
    if (url.scheme != "https" or url.netloc != "pickiclass.com"
            or url.path != "/order/lectureRoom/"):
        raise ValueError("허용되지 않은 강의 페이지입니다.")
    for cookie in payload.get("cookies", []):
        if cookie.get("domain", "").lstrip(".") != "pickiclass.com":
            raise ValueError("허용되지 않은 세션 도메인입니다.")
    lesson_id = payload.get("lesson_id")
    if lesson_id is not None and not re.fullmatch(r"\d+:\d+", str(lesson_id)):
        raise ValueError("허용되지 않은 회차입니다.")
    index = payload.get("lesson_index")
    if index is not None and (isinstance(index, bool) or not isinstance(index, int) or index < 0):
        raise ValueError("허용되지 않은 회차 위치입니다.")

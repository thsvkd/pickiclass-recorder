"""독립 UI 스레드를 갖는 앱 내부 브라우저 프로세스."""

from __future__ import annotations

import json
import logging
import sys
import threading
from pathlib import Path

from pickiclass.embedded_browser import validate_payload


def _emit(value) -> None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return
    if not isinstance(value, dict):
        return
    allowed = {}
    for key in (
        "status", "position", "duration", "error_code", "error_kind",
        "login_form", "player_frame", "lesson_rows",
    ):
        if key in value:
            allowed[key] = value[key]
    if allowed:
        print(json.dumps(allowed, ensure_ascii=True), flush=True)


def run(payload: dict, smoke_seconds: int = 0) -> None:
    validate_payload(payload)
    import webview

    logging.getLogger("pywebview").disabled = True
    webview.settings["ALLOW_FILE_URLS"] = False
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    window = webview.create_window(
        "피키클래스 · 앱 내장 재생", html="<h2>강의 페이지를 준비하고 있습니다.</h2>",
        width=1100, height=780,
    )
    initialized = False
    closed = threading.Event()
    window.events.closed += closed.set

    def loaded():
        nonlocal initialized
        from System import Action
        if not initialized:
            initialized = True

            def transfer_session():
                manager = window.native.webview.CoreWebView2.CookieManager
                for item in payload.pop("cookies", []):
                    cookie = manager.CreateCookie(
                        item["name"], item["value"], item["domain"], item["path"]
                    )
                    cookie.IsSecure = item["secure"]
                    cookie.IsHttpOnly = item["http_only"]
                    manager.AddOrUpdateCookie(cookie)

            window.native.Invoke(Action(transfer_session))
            window.load_url(payload["url"])
            return
        # 서명 URL, 쿠키, 본문은 출력하지 않는다. 페이지 로딩은 재생 성공과 별개다.
        _emit(window.evaluate_js("""JSON.stringify({
            login_form: !!document.querySelector('input[type="password"]'),
            player_frame: !!document.querySelector('iframe#myClass'),
            lesson_rows: document.querySelectorAll('a.video_row').length
        })"""))
        assigns = []
        if payload.get("lesson_id"):
            assigns.append(f"window.__pickiclassLesson = {json.dumps(payload['lesson_id'])};")
        if isinstance(payload.get("lesson_index"), int) and not isinstance(payload.get("lesson_index"), bool):
            assigns.append(f"window.__pickiclassLessonIndex = {payload['lesson_index']};")
        if assigns:
            window.evaluate_js("".join(assigns))
        window.evaluate_js(Path(__file__).with_name("player_controller.js").read_text("utf-8"))

    def monitor():
        previous = None
        while not closed.wait(0.5):
            try:
                state = window.evaluate_js("JSON.stringify(window.__pickiclassPlayback || null)")
                if state and state != "null" and state != previous:
                    _emit(state)
                    previous = state
            except Exception:
                return

    window.events.loaded += loaded
    if smoke_seconds:
        timer = threading.Timer(smoke_seconds, window.destroy)
        timer.daemon = True
        timer.start()
    webview.start(monitor, gui="edgechromium", private_mode=True, debug=False)


if __name__ == "__main__":
    try:
        run(json.load(sys.stdin))
    except Exception:
        print("내장 브라우저 초기화 실패", file=sys.stderr)
        sys.exit(1)

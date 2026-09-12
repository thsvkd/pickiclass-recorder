"""피키클래스 파일 저장 및 전사 - GUI 엔트리 포인트.

실행: .venv/bin/python main.py
데모 모드(코어 없이 화면만 확인): PICKICLASS_UI_DEMO=1 .venv/bin/python main.py
"""

from __future__ import annotations

import contextlib
import os

import flet as ft
from dotenv import load_dotenv

from ui.app import App

# 패키징된 앱에는 .env가 없다. 인자 없는 load_dotenv()는 호출 스택을 거슬러
# 파일을 찾는데, .pyc로 묶인 환경에서는 그 과정이 실패할 수 있어 경로를 명시한다.
# .env가 없거나 읽을 수 없어도 앱은 떠야 한다.
with contextlib.suppress(Exception):
    load_dotenv(".env")


def main(page: ft.Page) -> None:
    demo = os.environ.get("PICKICLASS_UI_DEMO") == "1"
    App(page, demo=demo)


# flet build로 패키징하면 이 모듈이 __main__으로 실행되지 않는다.
# 가드를 두면 UI가 시작되지 않아 빈 창만 뜬다.
ft.run(main)

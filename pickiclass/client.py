"""피키클래스의 정상 로그인·수강 목록·목차 조회. 인증값을 로그에 남기지 않는다."""

from __future__ import annotations

import json
import re
from html import unescape
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from pickiclass.models import Course, Lesson, VideoSource

BASE_URL = "https://pickiclass.com"
COURSES_URL = BASE_URL + "/order/lectureRoom/?type=onList"


class LoginError(RuntimeError):
    pass


class SourceUnavailableError(RuntimeError):
    pass


class ProtectedMediaError(SourceUnavailableError):
    pass


class PickiclassClient:
    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0",
                "Referer": BASE_URL + "/member/?type=login",
                "Origin": BASE_URL,
            }
        )
        self._course_urls: dict[str, str] = {}

    def _get(self, url: str) -> str:
        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            return response.content.decode("utf-8", errors="replace")
        except requests.RequestException:
            raise SourceUnavailableError(
                "페이지를 불러오지 못했습니다. 연결과 로그인을 확인하세요."
            ) from None

    def login(self, email: str, password: str) -> None:
        self._course_urls.clear()
        self.session.cookies.clear()
        if not email.strip() or not password:
            raise LoginError("아이디와 비밀번호를 입력하세요.")
        try:
            self._get(BASE_URL + "/member/?type=login")
            response = self.session.post(
                BASE_URL + "/ajaxProc.php",
                files={
                    key: (None, value)
                    for key, value in {
                        "type": "userCheck",
                        "userID": email.strip(),
                        "userPasswd": password,
                    }.items()
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
            if str(data.get("code")) != "200" or not isinstance(data.get("msg"), str):
                raise LoginError("로그인에 실패했습니다. 아이디와 비밀번호를 확인하세요.")
            result = self.session.post(
                BASE_URL + "/member/login_ok.php",
                data={"etc": data["msg"], "login": "5", "auto_login": "2", "login_go": "/"},
                timeout=self.timeout,
            )
            result.raise_for_status()
            html = self._get(COURSES_URL)
            if "logout=1" not in html:
                raise LoginError(
                    "로그인 세션을 확인하지 못했습니다. 웹사이트의 추가 인증을 확인하세요."
                )
        except (requests.RequestException, ValueError, SourceUnavailableError):
            self.session.cookies.clear()
            raise LoginError(
                "로그인 요청을 완료하지 못했습니다. 연결 또는 추가 인증을 확인하세요."
            ) from None

    @staticmethod
    def parse_courses(html: str) -> list[Course]:
        soup = BeautifulSoup(html, "html.parser")
        courses = []
        seen = set()
        for link in soup.select("a[href]"):
            query = parse_qs(urlparse(link["href"]).query)
            lecture = query.get("lecture_idx", [""])[0]
            enrollment = query.get("trade_goods_idx", [""])[0]
            if not lecture.isdigit() or not enrollment.isdigit():
                continue
            course_id = f"{lecture}:{enrollment}"
            if course_id in seen:
                continue
            title = None
            for parent in link.parents:
                title = parent.select_one("h3")
                if title:
                    break
            courses.append(
                Course(course_id, title.get_text(" ", strip=True) if title else "강의", "")
            )
            seen.add(course_id)
        return courses

    def list_my_courses(self) -> list[Course]:
        html = self._get(COURSES_URL)
        if "logout=1" not in html:
            self._course_urls.clear()
            raise LoginError("로그인이 만료되었습니다. 다시 로그인하세요.")
        courses = self.parse_courses(html)
        self._course_urls = {
            c.course_id: BASE_URL
            + "/order/lectureRoom/?type=detail&lecture_idx="
            + c.course_id.split(":")[0]
            + "&trade_goods_idx="
            + c.course_id.split(":")[1]
            for c in courses
        }
        return courses

    @staticmethod
    def _video_map(html: str) -> dict:
        match = re.search(r"\bvar\s+video_url\s*=\s*", html)
        if not match:
            return {}
        try:
            result, _ = json.JSONDecoder().raw_decode(html[match.end() :])
            return result if isinstance(result, dict) else {}
        except ValueError:
            raise SourceUnavailableError("강의 목차 형식이 변경되었습니다.") from None

    @staticmethod
    def parse_lessons(html: str) -> list[Lesson]:
        soup = BeautifulSoup(html, "html.parser")
        durations = []
        for row in soup.select("a.video_row"):
            duration = re.search(r"\b(\d{1,3}):(\d{2}):(\d{2})\b", row.get_text(" "))
            durations.append(
                sum(int(v) * multiplier for v, multiplier in zip(duration.groups(), (3600, 60, 1), strict=True))
                if duration
                else None
            )
        lessons = []
        for group_id, rows in PickiclassClient._video_map(html).items():
            if not isinstance(rows, dict):
                continue
            for row_id, item in rows.items():
                if not isinstance(item, dict) or not item.get("0"):
                    continue
                index = len(lessons)
                lessons.append(
                    Lesson(
                        lesson_id=f"{group_id}:{row_id}",
                        title=unescape(str(item["0"])),
                        duration_sec=durations[index] if index < len(durations) else None,
                        section_index=0,
                        section_title="강의",
                        index_in_section=index,
                        global_index=index,
                    )
                )
        return lessons

    def _course_html(self, course_id: str) -> str:
        url = self._course_urls.get(course_id)
        if not url:
            raise SourceUnavailableError("먼저 로그인하고 내 강의 목록을 불러오세요.")
        html = self._get(url)
        if 'id="userid"' in html or "id='userid'" in html:
            raise LoginError("로그인이 만료되었습니다. 다시 로그인하세요.")
        return html

    def list_lessons(self, course: Course) -> list[Lesson]:
        return self.parse_lessons(self._course_html(course.course_id))

    @staticmethod
    def parse_video_source(html: str, page_url: str) -> VideoSource:
        soup = BeautifulSoup(html, "html.parser")
        launcher = soup.find("kollus-player-launcher")
        if launcher:
            try:
                policy = json.loads(launcher.get(":player-policy", "{}"))
            except ValueError:
                policy = {}
            if policy.get("is_encrypted") is True:
                raise ProtectedMediaError(
                    "암호화된 Kollus 영상입니다. 일반 파일 다운로드·변환 입력으로 사용할 수 없습니다."
                )
            # 정책이 알려지지 않은 플레이어의 내부 URL을 추측하지 않는다.
            raise SourceUnavailableError(
                "Kollus 플레이어의 일반 미디어 파일 주소가 확인되지 않았습니다."
            )
        for element in soup.select("video[src], video source[src], audio[src], audio source[src]"):
            source = urljoin(page_url, element["src"])
            parsed = urlparse(source)
            if parsed.scheme != "https":
                continue
            extension = parsed.path.lower()
            if extension.endswith((".mp4", ".m4a", ".mp3", ".wav")):
                return VideoSource("direct", source, None)
            if extension.endswith(".m3u8"):
                return VideoSource("direct", None, source)
        raise SourceUnavailableError("처리 가능한 일반 영상·음성 주소를 찾지 못했습니다.")

    def get_video_source(self, course_id: str, lesson_id: str) -> VideoSource:
        html = self._course_html(course_id)
        try:
            group_id, row_id = lesson_id.split(":", 1)
            player_url = self._video_map(html)[group_id][row_id]["1"]
        except (ValueError, KeyError, TypeError):
            raise SourceUnavailableError("선택한 회차의 재생 정보를 찾지 못했습니다.") from None
        parsed = urlparse(player_url)
        if parsed.scheme != "https" or parsed.hostname not in {"pickiclass.com", "v.kr.kollus.com"}:
            raise SourceUnavailableError("지원하지 않는 플레이어 주소입니다.")
        return self.parse_video_source(self._get(player_url), player_url)

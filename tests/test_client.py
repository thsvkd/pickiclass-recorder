import json

import pytest

from pickiclass.client import PickiclassClient, ProtectedMediaError, SourceUnavailableError


def test_courses_keep_distinct_enrollments_and_titles():
    html = """<ul><li><a href="?type=detail&lecture_idx=34&trade_goods_idx=1">입장</a>
    <h3>강의 하나</h3></li><li><a href="?type=detail&lecture_idx=34&trade_goods_idx=2">입장</a>
    <h3>강의 둘</h3></li></ul>"""
    courses = PickiclassClient.parse_courses(html)
    assert [c.course_id for c in courses] == ["34:1", "34:2"]
    assert [c.title for c in courses] == ["강의 하나", "강의 둘"]


def test_lessons_read_site_video_map_without_leaking_signed_urls():
    videos = {
        "448": {
            "235": {
                "0": "첫 회차",
                "1": "https://v.kr.kollus.com/s?jwt=SECRET",
                "2": 448,
                "view_flag": True,
            }
        }
    }
    html = "<script>var video_url = " + json.dumps(videos) + ";</script>"
    html += '<a class="video_row">첫 회차 <span>영상강의</span><span>00:11:05</span></a>'
    lessons = PickiclassClient.parse_lessons(html)
    assert len(lessons) == 1
    assert lessons[0].lesson_id == "448:235"
    assert lessons[0].duration_sec == 665
    assert "SECRET" not in repr(lessons)


def test_encrypted_player_is_not_a_downloadable_source():
    html = """<kollus-player-launcher :player-policy='{"is_encrypted":true}'></kollus-player-launcher>"""
    with pytest.raises(ProtectedMediaError, match="암호화"):
        PickiclassClient.parse_video_source(html, "https://v.kr.kollus.com/s?jwt=SECRET")


def test_unknown_player_fails_without_exposing_url():
    with pytest.raises(SourceUnavailableError) as caught:
        PickiclassClient.parse_video_source(
            "<div>초기화 실패</div>", "https://v.kr.kollus.com/s?jwt=SECRET"
        )
    assert "SECRET" not in str(caught.value)


def test_plain_video_element_recognized_without_guessing_urls():
    source = PickiclassClient.parse_video_source(
        '<video><source src="https://cdn.example.com/lecture.mp4" type="video/mp4"></video>',
        "https://example.com/watch",
    )
    assert source.mp4_url == "https://cdn.example.com/lecture.mp4"
    assert source.hls_url is None


def test_source_requires_known_enrollment():
    client = PickiclassClient()
    with pytest.raises(SourceUnavailableError, match="강의 목록"):
        client.get_video_source("34:1", "448:235")


def test_lesson_titles_decode_html_entities():
    data = {"1": {"2": {"0": "유머는 &#039;모드&#039;다", "1": "https://example.com"}}}
    lessons = PickiclassClient.parse_lessons("<script>var video_url = " + json.dumps(data) + ";</script>")
    assert lessons[0].title == "유머는 '모드'다"

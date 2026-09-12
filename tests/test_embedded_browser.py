import pytest

from pickiclass.client import PickiclassClient
from pickiclass.embedded_browser import browser_payload, validate_payload


def test_session_transfer_excludes_other_domains():
    client = PickiclassClient()
    client._course_urls["34:1"] = "https://pickiclass.com/order/lectureRoom/?type=detail"
    client.session.cookies.set("session", "secret", domain="pickiclass.com")
    client.session.cookies.set("unrelated", "private", domain="example.com")
    payload = browser_payload(client, "34:1", "448:235", 0)
    assert [c["name"] for c in payload["cookies"]] == ["session"]
    assert payload["lesson_id"] == "448:235"
    assert payload["lesson_index"] == 0
    validate_payload(payload)


def test_browser_rejects_untrusted_lesson_selector():
    with pytest.raises(ValueError):
        validate_payload({
            "url": "https://pickiclass.com/order/lectureRoom/",
            "lesson_id": "../secret",
        })


@pytest.mark.parametrize("url", [
    "http://pickiclass.com/order/lectureRoom/",
    "https://pickiclass.com.evil.example/order/lectureRoom/",
    "file:///C:/secret", "https://pickiclass.com/member/",
])
def test_browser_rejects_untrusted_navigation_payload(url):
    with pytest.raises(ValueError):
        validate_payload({"url": url})

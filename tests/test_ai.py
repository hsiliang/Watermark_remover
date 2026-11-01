import io
import json
import os
import threading
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from docx import Document

from wmr import ai, comments, web
from tests.test_web import _post, server  # noqa: F401  共用 fixture

anthropic = pytest.importorskip("anthropic")
KEY = "sk-ant-test-0123456789"


@pytest.fixture
def fake_api():
    """假的 Messages API：記錄請求，依 state["status"] 回應。"""
    state = {"requests": [], "status": 200, "stop_reason": "end_turn", "text": "改寫後的 2024 段落"}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append({"path": self.path, "headers": dict(self.headers), "body": body})
            if state["status"] != 200:
                out = {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}
            else:
                out = {"id": "msg_1", "type": "message", "role": "assistant", "model": ai.MODEL,
                       "content": [{"type": "text", "text": state["text"]}], "stop_reason": state["stop_reason"],
                       "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}
            raw = json.dumps(out).encode()
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    state["client"] = anthropic.Anthropic(api_key=KEY, base_url="http://127.0.0.1:%d" % srv.server_address[1], max_retries=0)
    yield state
    srv.shutdown()


ITEM = {"idx": 3, "text": "原文 2024", "flags": ["否定排比"], "weight": 2}


def test_sdk_request_shape(fake_api):
    r = ai.suggest([ITEM], KEY, client=fake_api["client"])
    assert r["suggestions"] == {3: "改寫後的 2024 段落"} and r["done"] == 1 and r["error"] == ""
    q = fake_api["requests"][0]
    assert q["body"]["model"] == "claude-opus-5-5" and q["body"]["fallbacks"] == "default"
    assert q["headers"]["anthropic-beta"] == ai.FALLBACK_BETA and q["headers"]["X-Api-Key"] == KEY
    assert q["body"]["output_config"] == {"effort": "medium"}
    assert "<paragraph>\n原文 2024\n</paragraph>" in q["body"]["messages"][0]["content"]


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
def test_incomplete_answer_gives_no_suggestion(fake_api, stop):
    fake_api["stop_reason"] = stop
    r = ai.suggest([ITEM], KEY, client=fake_api["client"])
    assert r["suggestions"] == {} and r["done"] == 0


def test_bad_key_stops_and_hides_details(fake_api):
    fake_api["status"] = 401
    items = [dict(ITEM, idx=i) for i in range(40)]
    r = ai.suggest(items, KEY, client=fake_api["client"])
    assert r["done"] == 0 and "金鑰" in r["error"] and "invalid" not in r["error"]
    assert len(fake_api["requests"]) < 40


def test_deadline_returns_partial_without_blocking():
    gate = threading.Event()

    class Slow:
        class beta:
            class messages:
                @staticmethod
                def create(**kw):
                    gate.wait(5)
                    raise anthropic.APIError("x", None, body=None)
    r = ai.suggest([ITEM], KEY, deadline=0.05, client=Slow)
    gate.set()
    assert r["done"] == 0 and "時間到" in r["error"]


def test_empty_items_and_key_format():
    assert ai.suggest([], KEY)["requested"] == 0
    assert ai.check_key("  ") == "" and ai.check_key(" %s " % KEY) == KEY
    with pytest.raises(ValueError):
        ai.check_key("中文金鑰1234567890")


def test_mismatches_detects_changed_facts():
    gone, added = ai.mismatches("2024 年 Smith 調查 35%。", "2025 年 Smith 調查。")
    assert gone == ["2024", "35%"] and added == ["2025"]


def _doc(tmp_path):
    d = Document()
    for _ in range(6):
        d.add_paragraph("不是甲，而是乙。這件事很重要。" * 3)
    p = str(tmp_path / "a.docx")
    d.save(p)
    return p


def _comment_xml(path):
    return zipfile.ZipFile(path).read("word/comments.xml").decode()


def test_suggestion_only_in_comment_with_warning(tmp_path):
    src, dst = _doc(tmp_path), str(tmp_path / "r.docx")
    seen = []

    def fake(items):
        seen.extend(items)
        return {"suggestions": {items[0]["idx"]: "新增 2099 的改寫"}, "requested": len(items), "done": 1, "error": ""}
    r = comments.add_comments(src, dst, min_conf="低", suggest=fake)
    x = _comment_xml(dst)
    assert seen and seen[0]["flags"] and seen[0]["weight"] >= 1
    assert r["ai"] == {"requested": len(seen), "done": 1, "error": ""}
    assert x.count("Claude 改寫建議") == 1 and "新增 2099 的改寫" in x and "多出：2099" in x
    assert "新增 2099" not in zipfile.ZipFile(dst).read("word/document.xml").decode()


def test_web_without_key_never_calls_ai(server, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ai, "suggest", lambda *a, **k: pytest.fail("不該呼叫 AI"))
    st, j = _post(server + "/api/upload?name=a.docx", open(_doc(tmp_path), "rb").read())
    _post(server + "/api/clean?id=%s&style=0&keep_hidden=0" % j["id"])
    st, r = _post(server + "/api/review?id=%s&min_conf=%%E4%%BD%%8E" % j["id"])
    assert st == 200 and r["comments"] >= 1 and "ai" not in r


def test_web_key_from_header_not_persisted_and_comment_removed(server, tmp_path, monkeypatch):  # noqa: F811
    got = {}

    def fake(items, key):
        got["key"] = key
        return {"suggestions": {items[0]["idx"]: "建議文字"}, "requested": len(items), "done": 1, "error": ""}
    monkeypatch.setattr(ai, "suggest", fake)
    st, j = _post(server + "/api/upload?name=a.docx", open(_doc(tmp_path), "rb").read())
    _post(server + "/api/clean?id=%s&style=0&keep_hidden=0" % j["id"])
    req = urllib.request.Request(server + "/api/review?id=%s&min_conf=%%E4%%BD%%8E" % j["id"], data=b"",
                                 method="POST", headers={"X-Claude-Api-Key": KEY})
    r = json.loads(urllib.request.urlopen(req).read())
    assert got["key"] == KEY and r["ai"]["done"] == 1
    for root, _, files in os.walk(web.ROOT):
        for f in files:
            assert KEY.encode() not in open(os.path.join(root, f), "rb").read()
    rev = urllib.request.urlopen(server + "/download/%s/review" % j["id"]).read()
    assert "建議文字" in zipfile.ZipFile(io.BytesIO(rev)).read("word/comments.xml").decode()
    st, f = _post(server + "/api/final?id=%s&style=0&keep_hidden=0" % j["id"], rev)
    fin = urllib.request.urlopen(server + "/download/%s/final" % j["id"]).read()
    names = zipfile.ZipFile(io.BytesIO(fin)).namelist()
    assert st == 200 and f["removed_comments"] == r["comments"] and "word/comments.xml" not in names


def test_table_paragraphs_keep_comment_but_skip_ai(tmp_path):
    d = Document()
    d.add_table(rows=1, cols=1).cell(0, 0).text = "不是甲，而是乙。" * 3
    src, dst = str(tmp_path / "a.docx"), str(tmp_path / "r.docx")
    d.save(src)
    seen = []

    def fake(items):
        seen.extend(items)
        return {"suggestions": {}, "requested": len(items), "done": 0, "error": ""}
    r = comments.add_comments(src, dst, min_conf="低", suggest=fake)
    assert r["comments"] == 1 and seen == []

"""Ask Gemma on the dashboard: the log, and the guards on the one write endpoint."""

import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from scanner.web import app as web
from scanner.web import asks

LOCAL = {"origin": "http://127.0.0.1:8000", "content-type": "application/json"}


def test_the_log_merges_answers_and_marks_old_unanswered_questions_failed(tmp_path):
    log = tmp_path / "asks.jsonl"
    now = datetime(2026, 10, 19, 12, 0).astimezone()
    asks.append(log, {"id": "a", "at": (now - timedelta(hours=1)).isoformat(), "question": "q1"})
    asks.append(log, {"id": "a", "answered_at": now.isoformat(), "answer": "a1"})
    asks.append(log, {"id": "b", "at": (now - timedelta(hours=1)).isoformat(), "question": "q2"})
    asks.append(log, {"id": "c", "at": (now - timedelta(minutes=1)).isoformat(), "question": "q3"})
    rows = {r["id"]: r for r in asks.load(log, now=now)}
    assert rows["a"]["status"] == "answered" and rows["a"]["answer"] == "a1"
    assert rows["b"]["status"] == "failed"
    assert rows["c"]["status"] == "pending"
    assert asks.pending(list(rows.values()))


def test_a_missing_log_is_empty(tmp_path):
    assert asks.load(tmp_path / "none.jsonl") == []


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "ASKS", tmp_path / "asks.jsonl")
    started = []
    monkeypatch.setattr(asks, "submit", lambda path, q, **kw: started.append(q) or "id1")
    c = TestClient(web.app, base_url="http://127.0.0.1:8000")
    c.started = started
    return c


def test_a_question_from_this_pc_is_accepted(client):
    r = client.post("/api/ask", content=json.dumps({"question": "Why VLO?"}), headers=LOCAL)
    assert r.status_code == 200 and r.json()["ok"]
    assert client.started == ["Why VLO?"]


def test_another_website_cannot_ask(client):
    r = client.post("/api/ask", content=json.dumps({"question": "x"}),
                    headers={**LOCAL, "origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.post("/api/ask", content=json.dumps({"question": "x"}),
                    headers={"content-type": "application/json"})
    assert r.status_code == 403
    assert client.started == []


def test_only_json_is_accepted(client):
    r = client.post("/api/ask", content="question=x",
                    headers={**LOCAL, "content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 415


def test_empty_or_long_questions_are_refused(client):
    for q in ("  ", "x" * 501):
        r = client.post("/api/ask", content=json.dumps({"question": q}), headers=LOCAL)
        assert r.status_code == 400
    assert client.started == []


def test_one_question_at_a_time(client, tmp_path):
    asks.append(tmp_path / "asks.jsonl", {"id": "p", "at": datetime.now().astimezone().isoformat(),
                                          "question": "still thinking"})
    r = client.post("/api/ask", content=json.dumps({"question": "next"}), headers=LOCAL)
    assert r.status_code == 429


def test_review_endpoint_reads_the_latest(client, tmp_path, monkeypatch):
    monkeypatch.setattr(web, "REVIEWS", tmp_path)
    (tmp_path / "latest.json").write_text(json.dumps({"day": "2026-10-19", "summary": "ok"}))
    (tmp_path / "2026-10-19.json").write_text("{}")
    r = client.get("/api/review").json()
    assert r["review"]["summary"] == "ok" and r["days"] == ["2026-10-19"]

"""Smoke tests: HTTP routes that do not invoke the LLM (shape + status codes)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.book_storage import create_book, write_chapter


def test_no_llm_api_routes_smoke(temp_data_dir: Path) -> None:
    """Covers analytics, kb, prompts, library, teardown helpers, memory rollup, book shell."""
    with patch("app.main.ROOT", temp_data_dir):
        from app.main import app

        client = TestClient(app)

        h = client.get("/api/health")
        assert h.status_code == 200
        assert h.json().get("ok") is True

        t = client.get("/api/themes")
        assert t.status_code == 200
        assert isinstance(t.json().get("themes"), list)

        k = client.get("/api/kb")
        assert k.status_code == 200
        assert k.json().get("files") == []

        kw = client.post(
            "/api/kb/write",
            json={"filename": "smoke-test.md", "content": "# hi\n"},
        )
        assert kw.status_code == 200
        assert kw.json().get("ok") is True

        pr = client.get("/api/prompts")
        assert pr.status_code == 200
        assert "files" in pr.json()

        libf = client.get("/api/library/files")
        assert libf.status_code == 200
        assert "files" in libf.json()

        libs = client.get("/api/library/series")
        assert libs.status_code == 200
        assert "series" in libs.json()

        b = client.get("/api/books")
        assert b.status_code == 200

        tg = client.post("/api/teardown/match-tags", json={"tags": ["都市"]})
        assert tg.status_code == 200
        jd = tg.json()
        assert "matched_themes" in jd

        au = client.get("/api/teardown/distill-authors")
        assert au.status_code == 200
        assert au.json().get("authors") == []

        hist = client.get("/api/teardown/distill-history", params={"author_name": "Smoke"})
        assert hist.status_code == 200
        assert hist.json().get("records") == []

        det = client.get(
            "/api/teardown/distill-detail",
            params={"author_name": "Smoke", "record_id": "nope"},
        )
        assert det.status_code == 404

        rep = client.post("/api/teardown/repair-distill-quotes")
        assert rep.status_code == 200
        assert rep.json().get("ok") is True

        wm = client.post(
            "/api/teardown/write-memory",
            json={
                "room": "风格",
                "title": "smoke teardown",
                "body": "line",
                "chapter_label": None,
                "book_id": None,
            },
        )
        assert wm.status_code == 200
        assert wm.json().get("ok") is True

        ss = client.post(
            "/api/teardown/save-skill",
            json={"filename": "skill-smoke.md", "content": "BODY"},
        )
        assert ss.status_code == 200
        assert ss.json().get("ok") is True

        mg = client.post(
            "/api/teardown/merge-distill",
            json={
                "author_name": "NoOne",
                "record_ids": ["id1", "id2"],
            },
        )
        assert mg.status_code == 404

        ru = client.get("/api/memory/rollup")
        assert ru.status_code == 200

        pu = client.put("/api/memory/rollup", json={"text": "rollup smoke"})
        assert pu.status_code == 200

        bad_book = client.get("/api/books/" + ("a" * 12))
        assert bad_book.status_code == 404

        plan_min = {
            "book_title": "Smoke",
            "premise": "p",
            "chapters": [{"idx": 1, "title": "C1"}],
        }
        ck = create_book(
            temp_data_dir,
            title="Smoke",
            premise="p",
            plan=plan_min,
        )
        bid = ck["book_id"]
        write_chapter(temp_data_dir, bid, 1, "## One\n\nok")

        det_b = client.get(f"/api/books/{bid}")
        assert det_b.status_code == 200
        meta = det_b.json().get("meta") or {}
        assert meta.get("title") == "Smoke"

        ns = client.get(f"/api/books/{bid}/chapter-ns")
        assert ns.status_code == 200
        assert 1 in ns.json().get("ns", [])

        sup = client.get(f"/api/books/{bid}/supervisor/report")
        assert sup.status_code == 200
        assert "integrity_ok" in sup.json()

        toc = client.get(f"/api/books/{bid}/toc")
        assert toc.status_code == 200

        ch1 = client.get(f"/api/books/{bid}/chapters/1")
        assert ch1.status_code == 200
        assert "content" in ch1.json()

        ex = client.get(f"/api/books/{bid}/export.txt")
        assert ex.status_code == 200
        assert len(ex.text or "") >= 2

        msum_get = client.get(f"/api/books/{bid}/memory/summary")
        assert msum_get.status_code == 200

        msum_put = client.put(f"/api/books/{bid}/memory/summary", json={"text": "s"})
        assert msum_put.status_code == 200

        mlist = client.get(f"/api/books/{bid}/memory/entries")
        assert mlist.status_code == 200
        assert mlist.json().get("entries") == []

        madd = client.post(
            f"/api/books/{bid}/memory/entries",
            json={
                "room": "情节",
                "title": "e",
                "body": "b",
                "chapter_label": "1",
            },
        )
        assert madd.status_code == 200
        entry_id = madd.json()["entry"]["id"]

        mdel = client.delete(f"/api/books/{bid}/memory/entries/{entry_id}")
        assert mdel.status_code == 200

        tr = client.get("/api/trash/books")
        assert tr.status_code == 200

        ain = client.get("/api/analytics/info")
        assert ain.status_code == 200

        lst = client.get("/api/analytics/list")
        assert lst.status_code == 200

        nab = client.get("/api/analytics/file", params={"rel": "reviews/no-such-file.txt"})
        assert nab.status_code == 404

        nar = client.get("/api/analytics/raw", params={"rel": "reviews/missing.bin"})
        assert nar.status_code == 404

        met = client.post("/api/analytics/metrics/append", json={"test": "smoke", "n": 1})
        assert met.status_code == 200
        assert met.json().get("ok") is True

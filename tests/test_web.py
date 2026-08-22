"""API tests for the read-only dashboard service."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from src.web import create_app


def make_output(tmp_path: Path) -> Path:
    (tmp_path / "judgments").mkdir(parents=True)
    (tmp_path / "2026-08-15.md").write_text("# 日报\n", encoding="utf-8")
    (tmp_path / "focus_list.json").write_text(
        json.dumps({"updated": "2026-08-15", "regime_system": "震荡"}),
        encoding="utf-8",
    )
    return tmp_path


def test_dashboard_and_report_routes(tmp_path: Path):
    client = TestClient(create_app(output_dir=make_output(tmp_path), static_dir=tmp_path / "missing"))

    assert client.get("/api/v1/health").json() == {"status": "ok", "read_only": True}
    dashboard = client.get("/api/v1/dashboard")
    assert dashboard.status_code == 200
    assert dashboard.json()["market"]["regime"] == "震荡"
    reports = client.get("/api/v1/reports").json()
    assert reports[0]["date"] == "2026-08-15"
    document = client.get("/api/v1/reports/2026-08-15/report")
    assert document.json()["content"] == "# 日报\n"


def test_report_route_rejects_invalid_or_missing_artifact(tmp_path: Path):
    client = TestClient(create_app(output_dir=make_output(tmp_path), static_dir=tmp_path / "missing"))

    assert client.get("/api/v1/reports/not-a-date/report").status_code == 400
    assert client.get("/api/v1/reports/2026-08-15/secret").status_code == 400
    assert client.get("/api/v1/reports/2026-08-14/report").status_code == 404


def test_spa_is_served_when_built(tmp_path: Path):
    output_dir = make_output(tmp_path / "output")
    static_dir = tmp_path / "web"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<main>workbench</main>", encoding="utf-8")

    client = TestClient(create_app(output_dir=output_dir, static_dir=static_dir))

    assert client.get("/").text == "<main>workbench</main>"
    assert client.get("/reports/2026-08-15").text == "<main>workbench</main>"

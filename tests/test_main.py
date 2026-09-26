"""Tests for top-level pipeline safety semantics."""

from src.main import _cleanup_generated_reports


def test_dry_run_never_deletes_old_reports(monkeypatch):
    def fail_cleanup(*args, **kwargs):
        raise AssertionError("cleanup must not run in dry-run mode")

    monkeypatch.setattr("src.main.cleanup_old_reports", fail_cleanup)

    assert _cleanup_generated_reports("output", 30, dry_run=True) == 0


def test_live_run_keeps_configured_cleanup(monkeypatch):
    monkeypatch.setattr("src.main.cleanup_old_reports", lambda *_: 2)

    assert _cleanup_generated_reports("output", 30, dry_run=False) == 2

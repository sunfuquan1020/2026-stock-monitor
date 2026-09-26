"""上游数据源仓库周检的单元测试（不打网络，GitHub 响应用假数据）。"""

import json

from src import upstream_watch as uw

BASELINE = {
    "simonlin1212/a-stock-data": {"synced_sha": "aaa111", "synced_on": "2026-09-26",
                                  "local_skill_paths": ["~/.agents/skills/a-stock-data/SKILL.md"]},
}


def _compare_payload(files, commits=None):
    return {
        "status": "ahead",
        "commits": commits or [{"sha": "bbb222", "commit": {"message": "feat: v3.11.0 新端点\n正文",
                                                            "committer": {"date": "2026-09-30T01:00:00Z"}}}],
        "files": [{"filename": f, "status": "modified"} for f in files],
    }


class FakeGitHub:
    def __init__(self, head, compare=None):
        self.head, self.compare_payload = head, compare
        self.calls = []

    def __call__(self, path):
        self.calls.append(path)
        if path.endswith("/commits/HEAD"):
            return {"sha": self.head}
        return self.compare_payload


def test_no_change_when_head_equals_baseline():
    result = uw.check_repo("simonlin1212/a-stock-data", BASELINE["simonlin1212/a-stock-data"],
                           FakeGitHub("aaa111"))

    assert result["changed"] is False and result["needs_sync"] is False


def test_skill_change_requires_sync():
    gh = FakeGitHub("bbb222", _compare_payload(["SKILL.md", "CHANGELOG.md"]))
    result = uw.check_repo("simonlin1212/a-stock-data", BASELINE["simonlin1212/a-stock-data"], gh)

    assert result["needs_sync"] is True
    assert result["relevant_files"] == ["SKILL.md", "CHANGELOG.md"]
    assert "compare/aaa111...bbb222" in gh.calls[-1]


def test_readme_only_change_does_not_require_sync():
    gh = FakeGitHub("bbb222", _compare_payload(["README.md", "README_en.md", "assets/logo.png"]))
    result = uw.check_repo("simonlin1212/a-stock-data", BASELINE["simonlin1212/a-stock-data"], gh)

    assert result["changed"] is True and result["needs_sync"] is False


def test_commit_summaries_keep_first_line_and_date():
    gh = FakeGitHub("bbb222", _compare_payload(["SKILL.md"]))
    result = uw.check_repo("simonlin1212/a-stock-data", BASELINE["simonlin1212/a-stock-data"], gh)

    assert result["commits"] == [{"sha": "bbb222", "date": "2026-09-30", "message": "feat: v3.11.0 新端点"}]


def test_render_report_flags_repos_needing_sync():
    results = [{"repo": "simonlin1212/a-stock-data", "changed": True, "needs_sync": True,
                "base": "aaa111", "head": "bbb222", "relevant_files": ["SKILL.md"],
                "commits": [{"sha": "bbb222", "date": "2026-09-30", "message": "feat: v3.11.0"}],
                "local_skill_paths": ["~/.agents/skills/a-stock-data/SKILL.md"]}]

    text = uw.render_report(results, today="2026-10-05")

    assert "需要同步" in text and "SKILL.md" in text and "v3.11.0" in text
    assert "--mark-synced simonlin1212/a-stock-data bbb222" in text


def test_render_report_all_clear():
    results = [{"repo": "simonlin1212/a-stock-data", "changed": False, "needs_sync": False,
                "base": "aaa111", "head": "aaa111", "relevant_files": [], "commits": [],
                "local_skill_paths": []}]

    assert "无需同步" in uw.render_report(results, today="2026-10-05")


def test_mark_synced_updates_baseline_file(tmp_path):
    path = tmp_path / "upstream_baseline.json"
    path.write_text(json.dumps(BASELINE), encoding="utf-8")

    uw.mark_synced(str(path), "simonlin1212/a-stock-data", "bbb222", today="2026-10-05")

    saved = json.loads(path.read_text(encoding="utf-8"))["simonlin1212/a-stock-data"]
    assert saved["synced_sha"] == "bbb222" and saved["synced_on"] == "2026-10-05"
    assert saved["local_skill_paths"] == ["~/.agents/skills/a-stock-data/SKILL.md"]


def test_mark_synced_rejects_unknown_repo(tmp_path):
    path = tmp_path / "upstream_baseline.json"
    path.write_text(json.dumps(BASELINE), encoding="utf-8")

    try:
        uw.mark_synced(str(path), "someone/else", "bbb222", today="2026-10-05")
    except KeyError as e:
        assert "someone/else" in str(e)
    else:
        raise AssertionError("expected KeyError")

"""上游数据源仓库周检: a-stock-data / global-stock-data 有没有需要同步的更新。

/stock 的 A股/美股/港股取数方法来自这两个上游 skill, 接口常变。每周比较
GitHub 最新提交与 `upstream_baseline.json` 里记录的已同步提交:
- 只改 README/图片/CI → 记为"有提交但无需同步"
- 改了 SKILL.md/CHANGELOG/docs/tests 等 → "需要同步", 列出提交与文件

同步完成后用 --mark-synced 把基线推进到新提交。本模块只读 GitHub, 不改任何 skill。

用法:
    python -m src.upstream_watch                       # 检查并写 output/upstream/YYYY-MM-DD.md
    python -m src.upstream_watch --mark-synced simonlin1212/a-stock-data <sha>
"""

import argparse
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).resolve().parent.parent
BASELINE_PATH = PROJECT_DIR / "upstream_baseline.json"
REPORT_DIR = PROJECT_DIR / "output" / "upstream"
GITHUB_API = "https://api.github.com"
GITHUB_TIMEOUT = 20.0
# 这些文件变化不影响取数方法, 不触发同步
_NON_RELEVANT_PREFIXES = ("README", "assets/", ".github/", "LICENSE", ".gitignore")


def github_get(path: str) -> dict:
    """GET GitHub REST API; 有 GITHUB_TOKEN 时带上以提高限额。"""
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = httpx.get(f"{GITHUB_API}{path}", headers=headers, timeout=GITHUB_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _is_relevant(filename: str) -> bool:
    return not filename.startswith(_NON_RELEVANT_PREFIXES)


def check_repo(repo: str, entry: dict, get=github_get) -> dict:
    """比较上游 HEAD 与基线, 返回变化摘要。"""
    base = entry["synced_sha"]
    head = get(f"/repos/{repo}/commits/HEAD")["sha"]
    result = {
        "repo": repo, "base": base, "head": head, "changed": False, "needs_sync": False,
        "relevant_files": [], "commits": [], "local_skill_paths": entry.get("local_skill_paths", []),
    }
    if head.startswith(base) or base.startswith(head):
        return result
    diff = get(f"/repos/{repo}/compare/{base}...{head}")
    files = [f["filename"] for f in diff.get("files", [])]
    relevant = [f for f in files if _is_relevant(f)]
    result.update(
        changed=True,
        needs_sync=bool(relevant),
        relevant_files=relevant,
        commits=[
            {"sha": c["sha"][:12], "date": c["commit"]["committer"]["date"][:10],
             "message": c["commit"]["message"].split("\n")[0]}
            for c in diff.get("commits", [])
        ],
    )
    return result


def render_report(results: list[dict], today: str) -> str:
    lines = [f"# 上游数据源周检 — {today}", ""]
    pending = [r for r in results if r["needs_sync"]]
    lines.append(f"**结论**: {'需要同步 ' + str(len(pending)) + ' 个仓库' if pending else '无需同步'}")
    lines.append("")
    for r in results:
        if r["needs_sync"]:
            status = "🔴 需要同步"
        elif r["changed"]:
            status = "⚪ 有新提交, 仅 README/资源文件, 无需同步"
        elif r.get("error"):
            status = f"⚠️ 检查失败: {r['error']}"
        else:
            status = "✅ 无新提交"
        lines += [f"## {r['repo']}", "", f"- 状态: {status}",
                  f"- 基线 `{r['base'][:12]}` → 上游 `{r['head'][:12]}`"]
        if r["commits"]:
            lines.append("- 新提交:")
            lines += [f"  - `{c['sha']}` {c['date']} {c['message']}" for c in r["commits"]]
        if r["relevant_files"]:
            lines.append(f"- 相关文件: {', '.join(r['relevant_files'])}")
        if r["needs_sync"]:
            lines.append(f"- 本地副本: {', '.join(r['local_skill_paths'])}")
            lines.append(f"- 同步完成后: `python -m src.upstream_watch --mark-synced {r['repo']} {r['head']}`")
        lines.append("")
    return "\n".join(lines)


def load_baseline(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def mark_synced(path: str, repo: str, sha: str, today: str) -> None:
    baseline = load_baseline(path)
    if repo not in baseline:
        raise KeyError(f"基线里没有 {repo}")
    baseline[repo] = {**baseline[repo], "synced_sha": sha, "synced_on": today}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(baseline, f, ensure_ascii=False, indent=2)
        f.write("\n")


def run_check(path: str, get=github_get) -> list[dict]:
    results = []
    for repo, entry in load_baseline(path).items():
        try:
            results.append(check_repo(repo, entry, get))
        except Exception as e:
            logger.warning(f"上游检查失败 {repo}: {e}")
            results.append({"repo": repo, "base": entry["synced_sha"], "head": "?", "changed": False,
                            "needs_sync": False, "relevant_files": [], "commits": [],
                            "local_skill_paths": entry.get("local_skill_paths", []),
                            "error": str(e)[:120]})
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="上游数据源仓库周检")
    ap.add_argument("--baseline", default=str(BASELINE_PATH))
    ap.add_argument("--mark-synced", nargs=2, metavar=("REPO", "SHA"), help="同步完成后推进基线")
    args = ap.parse_args()
    today = date.today().isoformat()

    if args.mark_synced:
        repo, sha = args.mark_synced
        mark_synced(args.baseline, repo, sha, today)
        print(f"✅ 基线已更新: {repo} → {sha[:12]} ({today})")
        return 0

    results = run_check(args.baseline)
    text = render_report(results, today)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"{today}.md"
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"\n📄 已写入 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

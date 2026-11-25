#!/usr/bin/env python3
"""Validate PR title, branch name and compatibility-sensitive changes."""
import argparse
import re
import subprocess

TYPES = "feat|fix|refactor|perf|test|docs|chore|build|ci|release"
TITLE = re.compile(rf"^({TYPES})(\([a-z0-9./-]+\))?!?: .+")
BRANCH = re.compile(
    rf"^({TYPES}|hotfix|agent)/[a-z0-9._-]+(/[a-z0-9._-]+)*$"
    r"|^(dependabot|renovate)/[a-z0-9._/-]+$"
)
VERSION_LINE = re.compile(r"^[+-]version = ")


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", required=True, help="pull request title")
    parser.add_argument("--branch", required=True, help="head branch name")
    parser.add_argument("--base", required=True, help="base ref, e.g. origin/main")
    args = parser.parse_args()

    errors = []
    if not TITLE.match(args.title):
        errors.append("PR 标题必须符合 Conventional Commits，例如 `fix(upgrade): 回滚后清理状态`。")
    if not BRANCH.match(args.branch):
        errors.append(f"分支名 `{args.branch}` 必须形如 `feat/<说明>`、`fix/<说明>` 或 `agent/<名称>/<说明>`。")

    files = set(git("diff", "--name-only", f"{args.base}...HEAD").splitlines())
    if "engine/upgrade-schema.txt" in files and not any(
        path.endswith("migration.py") or "test_migration" in path for path in files
    ):
        errors.append("修改了 engine/upgrade-schema.txt，但没有同步 migration.py 或迁移测试。")
    if "engine/pyproject.toml" in files:
        diff = git("diff", f"{args.base}...HEAD", "--", "engine/pyproject.toml")
        if any(VERSION_LINE.match(line) for line in diff.splitlines()) and not args.branch.startswith("release/"):
            errors.append("版本号只由发布工作流修改，普通 PR 不要改 engine/pyproject.toml 的 version。")
    forbidden = [
        path for path in files
        if path.startswith(("Workspace/", ".aiws-releases/"))
        or path.rsplit("/", 1)[-1].startswith(".env")
        or path.endswith((".pem", ".key"))
    ]
    if forbidden:
        errors.append("PR 包含不应入库的文件：" + "、".join(sorted(forbidden)))

    if errors:
        for error in errors:
            print(f"::error::{error}")
        raise SystemExit(1)
    print("policy: ok")


if __name__ == "__main__":
    main()

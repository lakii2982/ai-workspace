#!/usr/bin/env python3
"""Derive the next release version from tags and Conventional Commits."""
import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "engine/pyproject.toml"
TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
BREAKING = re.compile(r"^[a-z]+(\([^)]*\))?!:")


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def latest():
    tags = [tag for tag in git("tag", "--list", "v*").splitlines() if TAG.match(tag)]
    if not tags:
        return None, (0, 0, 0)
    tag = max(tags, key=lambda item: tuple(int(part) for part in TAG.match(item).groups()))
    return tag, tuple(int(part) for part in TAG.match(tag).groups())


def subjects(tag):
    range_spec = f"{tag}..HEAD" if tag else "HEAD"
    return git("log", "--format=%s", range_spec).splitlines()


def next_version(version, messages):
    if any(BREAKING.match(message) for message in messages):
        return (version[0] + 1, 0, 0)
    kinds = {message.split(":", 1)[0].split("(")[0] for message in messages if ":" in message}
    if kinds & {"feat", "perf"}:
        return (version[0], version[1] + 1, 0)
    if kinds & {"fix", "refactor"}:
        return (version[0], version[1], version[2] + 1)
    return None


def write(version):
    text = PYPROJECT.read_text()
    updated, count = re.subn(
        r'^version = ".*"$', f'version = "{".".join(map(str, version))}"',
        text, count=1, flags=re.M,
    )
    if count != 1:
        raise SystemExit("engine/pyproject.toml has no version line")
    PYPROJECT.write_text(updated)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--next", action="store_true", help="print the next version, or nothing")
    parser.add_argument("--apply", metavar="X.Y.Z", help="write the version into pyproject.toml")
    args = parser.parse_args()

    tag, version = latest()
    if args.next:
        target = next_version(version, subjects(tag))
        if target:
            print(".".join(map(str, target)))
        return
    if args.apply:
        write(tuple(int(part) for part in args.apply.split(".")))
        return
    parser.error("pass --next or --apply X.Y.Z")


if __name__ == "__main__":
    main()

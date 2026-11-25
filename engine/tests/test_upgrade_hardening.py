import io
import importlib.util
from pathlib import Path
import tarfile

import pytest

engine = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('employee_upgrade_hardening', engine / 'upgrade.py')
upgrade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upgrade)

SHA = 'a' * 40


def _archive(path, entries):
    with tarfile.open(path, 'w:gz') as package:
        for name, size in entries:
            item = tarfile.TarInfo(name)
            item.size = size
            package.addfile(item, io.BytesIO(b'x' * size))
    return path


def test_unpack_rejects_too_many_members(tmp_path, monkeypatch):
    monkeypatch.setattr(upgrade, 'MAX_MEMBERS', 2)
    path = _archive(tmp_path / 'r.tar.gz', [(f'ai-workspace-{SHA}/f{i}', 1) for i in range(3)])
    with tarfile.open(path) as package:
        with pytest.raises(ValueError, match='too many entries'):
            upgrade.unpack(package, SHA, tmp_path / 'out')


def test_unpack_rejects_oversized_file(tmp_path, monkeypatch):
    monkeypatch.setattr(upgrade, 'MAX_FILE_BYTES', 4)
    path = _archive(tmp_path / 'r.tar.gz', [(f'ai-workspace-{SHA}/big', 10)])
    with tarfile.open(path) as package:
        with pytest.raises(ValueError, match='too large'):
            upgrade.unpack(package, SHA, tmp_path / 'out')


def test_unpack_rejects_parent_traversal(tmp_path):
    path = _archive(tmp_path / 'r.tar.gz', [(f'ai-workspace-{SHA}/../escape', 1)])
    with tarfile.open(path) as package:
        with pytest.raises(ValueError, match='Unexpected release archive path'):
            upgrade.unpack(package, SHA, tmp_path / 'out')


def test_unpack_rejects_a_foreign_root(tmp_path):
    path = _archive(tmp_path / 'r.tar.gz', [('other-root/file', 1)])
    with tarfile.open(path) as package:
        with pytest.raises(ValueError, match='Unexpected release archive path'):
            upgrade.unpack(package, SHA, tmp_path / 'out')


def test_resolve_main_honours_the_pinned_commit(monkeypatch):
    monkeypatch.setenv('AIWS_UPGRADE_SHA', 'b' * 40)
    assert upgrade.resolve_main() == 'b' * 40


def test_resolve_main_rejects_a_malformed_pin(monkeypatch):
    monkeypatch.setenv('AIWS_UPGRADE_SHA', 'not-a-sha')
    with pytest.raises(ValueError, match='AIWS_UPGRADE_SHA'):
        upgrade.resolve_main()

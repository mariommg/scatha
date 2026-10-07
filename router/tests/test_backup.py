"""Backups: what goes in, what never does, and how many are kept."""

import zipfile

import pytest

from jev_router import app, backup


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "data"
    monkeypatch.setenv(app.HOME_ENV, str(h))
    (h / "work" / "runs" / "r1").mkdir(parents=True)
    (h / "work" / "notes-preview").mkdir()
    (h / "config.json").write_text("{}", encoding="utf-8")
    (h / "topics.yaml").write_text("topics: []", encoding="utf-8")
    (h / "secrets.env").write_text("TYPESAFE_API_KEY=do-not-copy", encoding="utf-8")
    (h / "work" / "ingested.md").write_text("- ledger line", encoding="utf-8")
    (h / "work" / "runs" / "r1" / "routes.json").write_text("[]", encoding="utf-8")
    (h / "work" / "notes-preview" / "draft.md").write_text("preview", encoding="utf-8")
    return h


def test_off_by_default(home):
    assert backup.snapshot(app.load_config()) is None


def test_zip_has_the_data_but_never_secrets(home, tmp_path):
    z = backup.snapshot({"backup_dir": str(tmp_path / "bk")})
    names = set(zipfile.ZipFile(z).namelist())
    assert {"config.json", "topics.yaml", "work/ingested.md", "work/runs/r1/routes.json"} <= names
    assert "secrets.env" not in names
    assert not any("notes-preview" in n for n in names)
    assert all(b"do-not-copy" not in zipfile.ZipFile(z).read(n) for n in names)


def test_keeps_only_the_newest(home, tmp_path):
    dest = tmp_path / "bk"
    dest.mkdir()
    for d in ("2026-01-01_0001", "2026-01-08_0001", "2026-01-15_0001"):
        (dest / f"{app.APP_NAME}-data_{d}.zip").write_bytes(b"old")
    (dest / "unrelated.zip").write_bytes(b"not ours")
    newest = backup.snapshot({"backup_dir": str(dest), "backup_keep": 2})
    kept = sorted(p.name for p in dest.glob(f"{app.APP_NAME}-data_*.zip"))
    assert kept == [f"{app.APP_NAME}-data_2026-01-15_0001.zip", newest.name]
    assert (dest / "unrelated.zip").exists()  # only our own zips are pruned


def test_extra_files_are_copied_alongside(home, tmp_path):
    notes = tmp_path / "MY-NOTES.md"
    notes.write_text("about this install", encoding="utf-8")
    backup.snapshot({"backup_dir": str(tmp_path / "bk"), "backup_extra": [str(notes), str(tmp_path / "missing.md")]})
    assert (tmp_path / "bk" / "MY-NOTES.md").read_text(encoding="utf-8") == "about this install"

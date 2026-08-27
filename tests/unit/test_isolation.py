from pathlib import Path

from gts_agent.core.verify.isolation import compare_snapshots, save_snapshot, take_snapshot


def test_snapshot_includes_ld_so_conf_d(tmp_path, monkeypatch):
    conf_d = tmp_path / "ld.so.conf.d"
    conf_d.mkdir()
    extra = conf_d / "toolset.conf"
    extra.write_text("hidden\n", encoding="utf-8")
    monkeypatch.setattr(
        "gts_agent.core.verify.isolation.SNAPSHOT_PATHS",
        [str(tmp_path / "missing")],
    )
    monkeypatch.setattr(
        "gts_agent.core.verify.isolation.SNAPSHOT_DIRS",
        [str(conf_d)],
    )
    before = take_snapshot()
    assert str(extra) in before
    extra.write_text("changed\n", encoding="utf-8")
    after = take_snapshot()
    assert compare_snapshots(before, after) == [str(extra)]


def test_save_snapshot_accepts_string_path(tmp_path):
    target = tmp_path / "reports" / "snapshot-before.json"
    save_snapshot({"/usr/bin/gcc": "abc"}, str(target))
    assert target.exists()
    assert "abc" in target.read_text(encoding="utf-8")

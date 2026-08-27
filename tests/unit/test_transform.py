from gts_agent.core.verify.transform import transform_stage


def test_private_runtime_keeps_dsos(tmp_path):
    lib = tmp_path / "opt/rh/gcc-toolset-14/root/usr/lib64"
    lib.mkdir(parents=True)
    dso = lib / "libstdc++.so.6.0.33"
    dso.write_bytes(b"\x7fELF")
    la = lib / "libstdc++.la"
    la.write_text("libdir='/job/rpmbuild/BUILDROOT/opt/rh/gcc-toolset-14/root/usr/lib64'\n")
    actions = transform_stage(
        tmp_path, "/opt/rh/gcc-toolset-14/root/usr", "private-runtime"
    )
    assert dso.exists()
    assert not la.exists()
    assert any("保留" in item for item in actions)
    assert any(".la" in item for item in actions)


def test_system_nonshared_replaces_dsos_with_scripts(tmp_path):
    lib = tmp_path / "opt/rh/gcc-toolset-14/root/usr/lib64"
    lib.mkdir(parents=True)
    (lib / "libstdc++.so.6.0.33").write_bytes(b"\x7fELF")
    (lib / "libgcc_s.so.1").write_bytes(b"\x7fELF")
    transform_stage(
        tmp_path, "/opt/rh/gcc-toolset-14/root/usr", "system-nonshared"
    )
    assert not (lib / "libstdc++.so.6.0.33").exists()
    script = (lib / "libstdc++.so").read_text(encoding="utf-8")
    assert "/usr/lib64/libstdc++.so.6" in script
    assert "-lstdc++_nonshared" in script
    assert (lib / "libgcc_s.so").read_text(encoding="utf-8").count("libgcc_s.so.1")


def test_transform_removes_files_outside_toolset_prefix(tmp_path):
    lib = tmp_path / "opt/rh/gcc-toolset-14/root/usr/lib64"
    lib.mkdir(parents=True)
    kept = lib / "libstdc++.so.6.0.33"
    kept.write_bytes(b"\x7fELF")
    foreign = tmp_path / "usr/share/info/dir"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("DIR")
    wrapper = tmp_path / "usr/bin/gcc-toolset-14-env"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("#!/bin/bash\n")
    actions = transform_stage(
        tmp_path, "/opt/rh/gcc-toolset-14/root/usr", "private-runtime"
    )
    assert kept.exists()
    assert wrapper.exists()
    assert not foreign.exists()
    assert any("usr/share/info/dir" in item for item in actions)

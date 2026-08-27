from gts_agent.core.verify.abi import private_runtime_link_issues


def test_dynamic_cxx_must_need_libstdcxx():
    issues = private_runtime_link_issues("exceptions", ["libc.so.6"])
    assert any("libstdc++.so.6" in item for item in issues)


def test_dynamic_cxx_ok_with_libstdcxx():
    assert private_runtime_link_issues(
        "hello-cpp", ["libstdc++.so.6", "libgcc_s.so.1", "libc.so.6"]
    ) == []


def test_static_libstdcxx_must_not_need_shared():
    issues = private_runtime_link_issues(
        "static-libstdcxx", ["libstdc++.so.6", "libc.so.6"]
    )
    assert any("static-libstdc++" in item for item in issues)


def test_c_hello_does_not_require_libstdcxx():
    assert private_runtime_link_issues("hello-c", ["libc.so.6"]) == []

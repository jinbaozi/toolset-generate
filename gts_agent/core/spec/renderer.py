"""Spec 渲染器：基于 @TOKEN@ 占位符的确定性模板渲染。

规则：
- 模板中所有 @TOKEN@ 必须被替换；渲染后仍残留占位符 => 失败；
- 禁止生成宽泛通配符 %files（%files 一律使用 -f 精确清单）；
- 模板本身是本项目骨架，不是 Red Hat 原始 Spec。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict

_TOKEN_RE = re.compile(r"@([A-Z][A-Z0-9_]*)@")

FORBIDDEN_FILES_PATTERNS = (
    "%{_bindir}/*",
    "%{_libdir}/lib*.so*",
    "/opt/rh/%{tsname}/root/*",
)


class SpecRenderError(RuntimeError):
    pass


def render_template(template_text: str, tokens: Dict[str, str]) -> str:
    def _replace(match: "re.Match[str]") -> str:
        key = match.group(1)
        if key not in tokens:
            raise SpecRenderError(f"模板占位符 @{key}@ 没有对应的值")
        return str(tokens[key])

    rendered = _TOKEN_RE.sub(_replace, template_text)

    leftover = _TOKEN_RE.findall(rendered)
    if leftover:
        raise SpecRenderError(f"渲染后仍残留占位符: {sorted(set(leftover))}")

    for pattern in FORBIDDEN_FILES_PATTERNS:
        if pattern in rendered:
            raise SpecRenderError(
                f"生成的 Spec 含有被禁止的宽泛通配符: {pattern!r}（必须使用 %files -f 精确清单）"
            )
    return rendered


def render_template_file(template_path: Path, tokens: Dict[str, str]) -> str:
    return render_template(template_path.read_text(encoding="utf-8"), tokens)

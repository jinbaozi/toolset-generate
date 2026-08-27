"""tarball 源获取与校验。

规则（方案 6.1 / 13.1）：
- 网络下载只进入内容寻址缓存（cache/sources）；
- 哈希不一致立即失败（E-SOURCE-HASH），禁止自动"修复"；
- 镜像瞬态失败允许重试（E-SOURCE-MISSING，最多 max_retries 次）。
"""

from __future__ import annotations

import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional

from gts_agent.core.models.source_lock import (
    SourceHashMismatch,
    sha256_file,
    verify_source,
)


class SourceUnavailable(RuntimeError):
    code = "E-SOURCE-MISSING"


def fetch_tarball(
    uri: str,
    cache_dir: Path,
    expected_sha256: str = "",
    mirrors: Optional[List[str]] = None,
    max_retries: int = 3,
) -> Path:
    """获取 tarball 到内容寻址缓存并校验哈希，返回本地路径。

    - uri 可以是 http(s) URL 或本地文件路径；
    - 已有缓存且哈希匹配时直接复用，不重复下载。
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    filename = uri.rstrip("/").rsplit("/", 1)[-1]
    target = cache_dir / filename

    if target.exists():
        try:
            verify_source(target, expected_sha256)
            return target
        except SourceHashMismatch:
            # 缓存被污染：不静默覆盖，报告并停止
            raise

    local = Path(uri)
    if local.exists():
        shutil.copy2(local, target)
        verify_source(target, expected_sha256)
        return target

    candidates = [uri] + list(mirrors or [])
    last_error: Optional[Exception] = None
    for candidate in candidates:
        for attempt in range(1, max_retries + 1):
            try:
                tmp = target.with_suffix(target.suffix + ".part")
                with urllib.request.urlopen(candidate, timeout=120) as response, \
                        open(tmp, "wb") as fh:
                    shutil.copyfileobj(response, fh)
                tmp.rename(target)
                verify_source(target, expected_sha256)
                return target
            except SourceHashMismatch:
                target.unlink(missing_ok=True)
                raise
            except (urllib.error.URLError, OSError) as exc:
                last_error = exc
                time.sleep(min(2 ** attempt, 30))
    raise SourceUnavailable(
        f"[E-SOURCE-MISSING] 无法获取 {uri}（含镜像重试）：{last_error}"
    )


def lock_local_source(path: Path) -> str:
    """为已存在的本地源文件计算锁定哈希。"""
    if not path.exists():
        raise SourceUnavailable(f"[E-SOURCE-MISSING] 本地源不存在: {path}")
    return sha256_file(path)

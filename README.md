# gts-agent

GCC 多版本 Toolset 自动改造、构建、打包和验证智能体——第一阶段（MVP）原型。

目标：对输入的基础 GCC、目标 GCC、binutils、glibc、发行版和架构组合进行**资格判定**；只有通过兼容性分析、构建探测和 ABI 验证的组合，才生成可并行安装的 Toolset RPM。生成的 Toolset：

- 安装到 `/opt/rh/gcc-toolset-<id>/root`，**绝不**覆盖 `/usr/bin/gcc`、系统 binutils 或系统 `libstdc++.so.6`；
- 支持两种运行时策略：`system-nonshared`（复刻 Red Hat 兼容逻辑，仅限已验证 profile）和 `private-runtime`（Toolset 前缀内私有运行时，需人工审批）；
- 一律禁止伪造符号（`objcopy --add-symbol`、复制基础 GCC 的 nonshared 归档、篡改版本脚本等）。

## 安装

```bash
pip install -e ".[dev]"
```

依赖：Python >= 3.9、PyYAML。运行时探测依赖宿主的 `readelf`、`nm`、`gcc`、`rpm` 等命令（缺失时记录 warning，不静默忽略）。

## CLI 用法

```bash
# 1. 探测宿主/构建根
gts-agent discover --config examples/cs9-gts14.yaml

# 2. 锁定源码输入（SRPM/tarball/patch 哈希）
gts-agent resolve-sources --config examples/cs9-gts14.yaml

# 3. 兼容性资格判定（bootstrap、triple、glibc 基线、binutils 实测探测）
gts-agent analyze --config examples/cs9-gts14.yaml

# 4. 生成 build plan、包图与三份 Spec（runtime/binutils/gcc）
gts-agent plan --config examples/cs9-gts14.yaml

# 5. 人工审批（绑定 plan 的 SHA-256；plan 变更必须重新审批）
gts-agent approve --config examples/cs9-gts14.yaml \
  --plan-sha256 <SHA256> --decision approve --approver release-engineer

# 6. 经过审批门后执行隔离构建（RHEL 9 / GCC 14.3.0 最小工具集）
gts-agent plan --config examples/rhel9-gts14.3.0.yaml --skip-probes
gts-agent approve --config examples/rhel9-gts14.3.0.yaml \
  --plan-sha256 <SHA256> --decision approve --approver release-engineer
gts-agent approve --config examples/rhel9-gts14.3.0.yaml \
  --plan-sha256 <SHA256> --decision approve --approver release-engineer \
  --scope private-runtime
gts-agent build --config examples/rhel9-gts14.3.0.yaml --execute

构建镜像：

```bash
sudo podman build --network=host -t gts-rhel9-builder:latest containers/rhel9-builder
```

# 查看状态机进度 / 解释失败
gts-agent status --config examples/cs9-gts14.yaml
gts-agent explain-failure --config examples/cs9-gts14.yaml
```

## 架构概览

```text
gts_agent/
├── cli.py                     # CLI 入口
├── agent/
│   ├── orchestrator.py        # Discover -> Lock -> Analyze -> Plan -> Approval 流程
│   ├── state_machine.py       # 可恢复状态机（只追加、幂等复用、冻结）
│   ├── policy_engine.py       # 策略引擎（快速失败、安装路径/Provides 边界）
│   ├── approvals.py           # 审批记录（绑定 plan 哈希，拒绝即冻结）
│   └── diagnostics.py         # 错误分类与自动修复预算（E-* 错误码表）
├── core/
│   ├── models/                # JobConfig、Inventory、SourceLock、CompatibilityReport
│   ├── probe.py               # 宿主探测（gcc -dumpmachine、rpm -E、getconf 等）
│   ├── compatibility/         # GCC bootstrap/triple、glibc 基线、binutils 实测探测
│   ├── abi/                   # readelf/nm 驱动的 ELF、版本符号与 nonshared 差集分析
│   ├── manifest/              # staged 文件发现、子包分类、精确 %files 生成
│   └── spec/                  # @TOKEN@ 模板渲染器（禁止宽泛通配符）
├── adapters/distro.py         # RHEL 9（SCL/rpm-4.16/nonshared 110）与 RHEL 10（env/rpm-4.19/140）
├── executors/mock.py          # Mock 隔离构建命令计划与执行
├── templates/                 # Spec 骨架与 enable/env-wrapper 脚本（非 Red Hat 原始 Spec）
├── schemas/                   # job-config JSON Schema
├── policies/                  # default / production 策略（policy-as-data）
└── reference_profiles/        # 黄金参考 profile：cs9-gts14、cs10-gts15
```

## 关键设计约束（硬性）

| 约束 | 实现位置 |
| --- | --- |
| Toolset 只能安装到 `/opt` 前缀，禁止写系统路径 | `policies/default.yaml` + `policy_engine.check_install_path` |
| 禁止提供裸 `gcc`/`c++-compiler` capability | `policy_engine.check_provides` |
| glibc 版本需求不得超过基线，禁止自动修复 | `compatibility/glibc.py`（`E-GLIBC-BASELINE`） |
| nonshared 差集必须被归档完整覆盖 | `abi/symbols.check_nonshared_coverage`（`E-NONSHARED-INCOMPLETE`） |
| system-nonshared 与 private-runtime 文件互斥 | `manifest/discover.classify_path` |
| 符号链接不得逃逸 Toolset 根 | `manifest/discover.py`（`E-ISOLATION`） |
| `%files` 一律精确清单，禁止宽泛通配符 | `spec/renderer.py` + `manifest.write_files_lists` |
| patch 零 fuzz、私有运行时/发布必须人工审批 | `policies/*.yaml` + `approvals.py` |
| 自动修复有全局与逐错误码预算 | `diagnostics.RepairLedger` |
| 状态输出只追加、幂等复用、拒绝即冻结 | `state_machine.JobStateMachine` |

## 状态机

```text
Discover -> ResolveSources -> AnalyzeCompatibility -> GeneratePlan -> Approval
 -> PatchTransform -> Build -> StageInstall -> GenerateRPM -> InstallTest
 -> CompileLinkTest -> AbiSymbolTest -> IsolationTest -> PublishReport
```

Approval 之后由 `gts-agent build --execute` 在 Podman（RHEL 9 兼容构建根）中继续执行到 PublishReport。

## 测试

```bash
python3 -m pytest tests -q
```

## 支持边界（MVP）

- 发行版：RHEL/CentOS Stream 9（rpm-4.16、SCL 激活）与 10（rpm-4.19、`gcc-toolset-N-env` 激活）；
- 架构：x86_64、aarch64；native 构建；语言 C/C++；multilib 禁用；
- 黄金参考组合：CS9/GTS14（nonshared 基线 110）、CS10/GTS15（nonshared 基线 140）；
- 本项目适配：RHEL 9 / GCC 14.3.0 最小工具集（`private-runtime`），见 `examples/rhel9-gts14.3.0.yaml`。

其他 `system-nonshared` 组合若没有已验证 compat patch，资格判定会快速失败。

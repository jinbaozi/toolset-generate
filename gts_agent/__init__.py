"""gts-agent：GCC 多版本 Toolset 自动改造、构建、打包和验证智能体。

第一阶段（MVP）原型。所有关键行为由 schema、策略文件和参考 profile 驱动；
LLM（若接入）只承担分析和生成待审批 diff 的职责，不直接修改宿主机。
"""

__version__ = "0.1.0"

# 实验 A · 复核材料

主报告在[仓库根目录复盘](../../../retrospective.md#实验-a--规划深度对照)。

- `baseline.patch` 从主仓库 `ba663c2` 的代码中去掉两个目标动作及依赖这些动作的综合测试，复原共享运行基线。
- `x-first.patch`、`y-first.patch` 是两个会话在独立评审前留下的首轮实现和测试改动。可以在不同副本依次应用基线补丁和一个分组补丁。
- `x-run.json`、`y-run.json` 记录运行编号、时间、完成轮次和补丁 SHA-256。
- `checks.py` 是评审者在两组实现结束后运行的独立检查器。两份 `*-review.txt` 是实际运行输出。

复核时准备两个独立目录。各自仅导出 `ba663c2` 中的 `accounts/`、`repairs/`、`lab_repair/`、`manage.py`、`pyproject.toml` 和 `uv.lock`。进入任一副本，按顺序执行：

1. `git apply <证据目录>/baseline.patch`。
2. 应用 `x-first.patch` 或 `y-first.patch`，将同目录下的 `checks.py` 复制到副本根目录。
3. 设置 PowerShell 的 `$env:EXPERIMENT_ARM` 为对应的 `x` 或 `y`。运行 `uv run --locked python checks.py`。

两组补丁用于比较原始实现结果。主项目使用独立的交付版实现。

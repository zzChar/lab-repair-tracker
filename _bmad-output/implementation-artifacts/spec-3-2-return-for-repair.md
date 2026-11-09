---
title: '故事 3.2 · 试打失败后退回原负责人'
type: feature
created: '2026-11-09'
status: done
route: oneshot
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent">

## Intent

报修人提供非空失败原因，沿用原单和原负责人退回处理中。继续处理、再次提交验收后仍可关闭。

</frozen-after-approval>

## Implementation Notes

- 补充原负责人在退回阶段能看到失败原因及先前处理结果的断言。实际 HTTP 演练完成退回、复测和关闭。

- RepairWorkflowTests 验证原因必填、非报修人拒绝、原单及负责人保留、两轮结果和失败原因可查。

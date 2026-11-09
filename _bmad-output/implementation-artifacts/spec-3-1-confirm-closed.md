---
title: '故事 3.1 · 试打成功后关闭'
type: feature
created: '2026-11-09'
status: done
route: oneshot
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent">

## Intent

报修人用当前版本确认待确认工单修好，关闭原工单并保留历史。

</frozen-after-approval>

## Implementation Notes

- 临时数据库上的实际 HTTP 演练使用演示账号和 CSRF，通过一次试打并关闭。

- RepairWorkflowTests 验证关闭成功、处理结果与进度可查、跟进人越权和重复关闭拒绝。

---
title: '故事 2.3 · 追加处理进度'
type: feature
created: '2026-11-09'
status: done
route: oneshot
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent">

## Intent

当前跟进人在处理中追加带时间的进度事件。工单保持处理中，详情按版本显示历史，并独立显示最新进度。

</frozen-after-approval>

## Implementation Notes

- 详情按读取到的工单版本截断历史。测试核对最新进度区的独立时间及历史顺序。

- RepairWorkflowTests 覆盖两次追加、操作者、内容和时间、报修人可见以及其他负责人越权不写入。

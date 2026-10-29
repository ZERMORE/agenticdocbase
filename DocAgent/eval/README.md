# 评测题集

dev.jsonl仅适用于默认合成示例库，不能代表论文问答效果。冻结论文题集需先人工读论文并标注，尚未生成或测量质量指标。

每行JSON至少包含id、question、answerability（answerable/unanswerable）。可答题应补充answer_points、文档revision、页码和连续原文短引文。20道调参题与40道冻结题按方案分离。

evaluate会真实调用DeepSeek并产生API消耗；报告保留逐题答案路径、耗时、usage及待人工标注字段。程序只校验引用结构，不自动宣称语义正确。

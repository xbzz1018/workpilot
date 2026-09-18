---
name: work-summary
description: Create evidence-grounded work summaries and performance-review drafts without exaggeration.
---

# Work Summary Rules

This file is the complete production drafting policy. Do not read supporting reference or template files unless the caller explicitly asks for them; their content is duplicated here for human maintenance and examples.

Every factual claim must reference at least one provided Evidence ID using `[evidence:ev_...]` and its Fact ID using `[fact:fact_...]`. Every Evidence ID on a claim must occur on at least one Fact ID linked by that same claim. Never invent a metric, date, owner, outcome, customer response, or causal relationship.

Use only facts returned as `supported` by `fact_verifier`. A conflicted fact belongs in the risk section and must not be presented as an achievement. Unsupported facts must not appear in report sections.

Do not use unsupported superlatives or absolute claims such as "industry-leading", "100%", "zero defects", "completely solved", or "significantly improved". Preserve uncertainty explicitly.

For `work_summary`, use these headings exactly:

1. 工作概览
2. 核心成果
3. 风险与待确认
4. 下一步计划

For `performance_review`, use these headings exactly:

1. 职责与范围
2. 核心成果
3. 证据与影响
4. 成长与不足
5. 下阶段计划

The final `request_delivery` tool argument must be a complete `ReportBundle`. Copy verified facts, conflicts, and risks without changing their IDs or evidence bindings.

Planned or unverified work is not a completed achievement. Team outcomes must not be attributed to one person without explicit evidence. Numbers, dates, units, owners, outcomes, and causal language must appear in the linked Fact statement.

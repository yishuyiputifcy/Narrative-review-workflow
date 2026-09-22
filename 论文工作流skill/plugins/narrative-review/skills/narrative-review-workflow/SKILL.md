---
name: narrative-review-workflow
description: Coordinate reusable undergraduate narrative-review projects when the current user message explicitly invokes $narrative-review:narrative-review-workflow and asks to use it.
---

# Narrative review workflow

## Invocation gate

Use this skill only when the current user message explicitly contains `$narrative-review:narrative-review-workflow` and asks to use it. A natural-language command such as `启动论文项目`, `继续`, `审核`, `定点修改`, `润色`, or `生成Word`, a discussion or example of the skill, and an invocation in message history do not authorize the current turn.

Run a thin, disk-backed workflow. Reuse the bundled `$narrative-review:academic-research-suite` and `$narrative-review:humanizer` skills plus the external `$documents:documents` skill; do not reproduce their research, prose, or DOCX logic.

Read [references/execution-contract.md](references/execution-contract.md) before acting. Use `scripts/workflow_state.py` for deterministic hashes, dependency invalidation, work-unit selection, review recording, and humanization candidate commits.

## Project contract

Each paper uses these paths unless `project.json` overrides them:

```text
project.json
literature_matrix.md
manuscript.md
state.json
inputs/template.docx       optional
reviews/                   created when needed
output/paper.docx          created when requested
output/render/             created when rendering succeeds
```

`manuscript.md` is the only content authority. A template controls formatting only. Chat history and old DOCX files never reconstruct or override the manuscript.

Recommended defaults:

- `project.json` stores requirements, paths, project-level prohibitions, review-affecting configuration, and Word-affecting configuration.
- A bare `继续` runs one complete work unit. Drafting defaults to one top-level chapter unless the project specifies a batch.
- Default review covers citation integrity, source support, scope boundaries, and project requirements. It does not run a full simulated peer-review panel.
- `$documents:documents` is the default Word path. Project-specific converters are fallback only when the standard path cannot preserve the required template.

## Commands

### 启动论文项目

Create the minimum project files from supplied requirements, record current-turn limits separately from project-level prohibitions, then execute the first authorized work unit. `本轮只检索` permits research only for that invocation; it does not become a persistent drafting ban.

### 继续

Read project files, run `workflow_state.py inspect <project> --write`, then run `workflow_state.py next <project>`. Execute the returned work unit, including all internal reads, searches, checks, and saves required to finish that unit. Stop only when the unit completes, a persistent prohibition is reached, a required author decision is missing, or the unit fails.

For a status question or the final next-unit check, use `inspect <project>` and `next <project> --read-only`. See the contract's [Read-only status and recovery](references/execution-contract.md#read-only-status-and-recovery) for command side effects, chapter selection, and independent-test boundaries.

If evidence or review configuration changed, `继续` runs the necessary review and saves its report in the same turn. It does not stop merely to ask the user to send `审核`.

The scheduler follows content prerequisites before stale Word work. A passing review skips revision; a blocked review selects revision; a limited review selects evidence work only when its report identifies that route, otherwise it returns a visible block.

### 审核

Run citation and content review without changing `manuscript.md`. Save a report bound to the manuscript, evidence, and review-configuration versions. Record review execution separately from its verdict:

- `passed`: no blocking issues.
- `passed_with_advice`: no blocking issues; advice is optional.
- `blocked`: one or more blocking issues.
- `limited`: missing evidence prevents a complete verdict.

Only `passed` and `passed_with_advice` authorize review inheritance or final-content progression.

### 定点修改

Resolve the specified issues and every necessary associated location needed to keep the abstract, body, tables, and conclusion consistent. Record associated edits and reasons. Do not rewrite unrelated text.

After editing, rerun affected evidence checks and necessary association checks, then save a new review conclusion bound to the new manuscript and evidence versions. Mark the new version passed only when affected issues are resolved, association checks pass, and no other blocking items remain.

### 润色

Use `workflow_state.py prepare-humanize`, edit only the returned candidate with `$narrative-review:humanizer`, and preserve the returned snapshot. Check numbers, units, citations, tables, facts, qualifications, and conclusion strength.

Before replacement, use `workflow_state.py commit-humanize` with the recorded starting hash. A changed master aborts the commit without overwriting it. Carry review forward only from a currently bound `passed` or `passed_with_advice` conclusion and only with a distinct saved integrity report for every humanization round; every report in that inheritance chain remains required and is hash-checked. Early humanization remains unreviewed. Do not implement or simulate reverse-diff recovery.

If the candidate is rejected, its checks fail, or the master changed, run `workflow_state.py cancel-humanize <project> --reason rejected|failed|conflict`. This clears pending state and removes only the candidate; it preserves the current master and pre-humanization snapshot so a new candidate can be prepared without editing `state.json`.

### 生成Word

Use `$documents:documents` with the current manuscript, template, and output-affecting configuration. A preview may be generated despite incomplete content or visual review, but must remain explicitly unaccepted.

For an explicit preview request, use `workflow_state.py next <project> --request word --preview`. A bare `继续` reaches Word only after research, outline, the entire manuscript, and a current passing content review satisfy their prerequisites.

Record content synchronization, structure checks, visual review, and author confirmation against the actual DOCX hash. If that hash changes, invalidate all four. Final delivery requires the latest DOCX to pass content synchronization, structure checks, and page-by-page visual review.

Save one Word acceptance report describing the checks actually performed, then bind it with `workflow_state.py record-word <project> --report <report-path>` and the applicable check flags. The report record stores its path, hash, and the checked DOCX hash. A report makes the checks traceable; it does not prove an unperformed check or authorize `--visual` without a completed page-by-page review. Missing or changed evidence, a DOCX mismatch, and every `--preview` result remain blocked from final completion.

## Boundaries

- Record bibliographic metadata checks, abstract reading, and full-text verification separately.
- Source support and applicability require reading; numbering scripts cannot decide them.
- Do not generalize one device, test platform, topology, dataset, or operating condition into a universal conclusion.
- Never invent literature, data, search records, metadata, or verification results.
- Missing full text, author metadata, template, renderer, or another required tool must remain visible in state and delivery claims.
- Internal ARS role files are prompt guidance, not callable tools. Invoke ARS modes through `$narrative-review:academic-research-suite` and use available browsing/file tools inline.

## Progress receipts

After a research, outline, or drafting unit succeeds, call `workflow_state.py record-progress`:

```text
record-progress <project> research
record-progress <project> outline
record-progress <project> draft --batch <batch-id>
record-progress <project> draft --batch <final-batch-id> --manuscript-complete
```

A completed chapter batch means only that batch is complete. Drafting remains `in_progress` until `--manuscript-complete` is recorded after checking the whole configured manuscript scope.

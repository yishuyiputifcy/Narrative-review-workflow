# Execution contract

## Minimum project configuration

Use this shape unless the user supplies different paths or requirements:

```json
{
  "requirements": {},
  "review_config": {},
  "word_config": {},
  "workflow": {
    "stage_order": ["research", "outline", "draft", "review", "revision", "humanize", "word"],
    "prohibited_stages": [],
    "require_humanize": true
  },
  "paths": {
    "manuscript": "manuscript.md",
    "evidence": ["literature_matrix.md"],
    "template": "inputs/template.docx",
    "docx": "output/paper.docx"
  }
}
```

Create `manuscript.md` and `literature_matrix.md` only with content justified by the user's materials. `state.json` may be absent initially; `workflow_state.py inspect <project> --write` creates the default state and binds current files.

Successful phase work writes these minimum receipts through `record-progress`:

```text
progress.research.status = completed
progress.research.artifact = {path, sha256}

progress.outline.status = completed
progress.outline.artifact = {path, sha256}

progress.draft.status = pending | in_progress | completed
progress.draft.completed_batches = [batch ids]
progress.draft.manuscript_complete = true only after the full configured manuscript is complete
progress.draft.artifact = {path, sha256}
```

Use `record-progress <project> draft --batch <id>` after each chapter batch. Add `--manuscript-complete` only after all required batches and whole-manuscript structure checks pass.

## Authorization and stopping

Classify limits before selecting work:

- A phrase explicitly scoped to `本轮`, `这次`, or the current response is a turn limit. It expires when that work unit ends.
- `在我明确允许前`, `暂不`, `先不要`, or an explicit project prohibition persists in `project.json`. A bare `继续` does not remove it.
- Ambiguous limits take the narrower interpretation.

One invocation completes one user-visible work unit, not one internal tool call:

| Unit | Completion point |
|---|---|
| research | Current search, verification, and matrix update are saved |
| outline | Complete outline, word allocation, and evidence map are saved |
| draft | Configured chapter batch is saved and checked |
| review | One review report and verdict are saved |
| revision | Requested issue set and necessary associated locations are rechecked |
| humanize | Candidate is checked and either committed or rejected |
| word | DOCX and every currently possible check are recorded |

Stop at unit completion, a persistent prohibition, an author decision, an irreplaceable missing input, or a visible failure. Do not pause between internal reads, searches, edits, and checks.

## Read-only status and recovery

Use the public CLI for status and final next-unit checks:

```text
python -B scripts/workflow_state.py inspect <project>
python -B scripts/workflow_state.py next <project> --read-only
```

`inspect` without `--write` and `next --read-only` reconcile current disk versions in memory without creating or updating project files. `next --read-only` still applies dependency invalidation, prohibitions, turn limits, and preview rules. Its `state_written=false` describes persistence, not completion or acceptance; `action=execute` recommends a unit and does not run it. Plain `next` retains its state-saving behavior and reports `state_written=true`.

Ordinary continuation recovers requirements, content, evidence, and progress from disk even when conversation history is present. Strict context isolation is an additional test condition only when the user explicitly requires it; if that condition fails, stop that test without claiming workflow failure or an isolation pass. Name discovery and explicit-path execution are separate checks.

The scheduler selects a stage, not a chapter. For `draft`, use the user-authorized batch; otherwise select the first unfinished top-level chapter from the disk outline and batch receipts after checking the manuscript. If those records conflict and the intended batch cannot be established, report the conflict. Check saved content before recording progress: a batch receipt and hash do not independently prove source support or chapter completeness.

## Version dependencies

The helper computes these bindings:

```text
review = manuscript + evidence + review-affecting configuration
word = manuscript + template + Word-affecting configuration
visual acceptance = actual DOCX bytes
```

Evidence includes the literature matrix and every configured evidence file. Review configuration includes requirements that affect scope, structure, citations, or mandatory content. Word configuration includes metadata, abstract choices, citation rendering, page setup, headings, headers, footers, and table choices.

Configured evidence must resolve inside the current project root. Its binding uses the normalized project-relative path plus file hash, so renaming, replacing, changing, or deleting a configured evidence object remains detectable.

- A project-relative evidence reference moves with an unchanged project and preserves its review binding.
- Resolution normalizes `.` and `..` and resolves symbolic links before deriving the identifier. Equivalent spellings of the same internal file therefore share one identifier; an internal symbolic link is identified by its resolved internal target, while a link resolving outside the root is rejected.
- An absolute reference that currently points inside the project is accepted and normalized relative to that current root. If the configuration is copied unchanged, the absolute value still points to the old root; it is not rewritten and does not receive the relative-reference migration guarantee.
- An external absolute reference or a relative reference that escapes the project root is unsupported and rejected. Do not add implicit copying, rebasing, or external-evidence support.

States written by the earlier absolute-path evidence algorithm remain JSON-readable, but their stored evidence hash does not equal the relative-path hash. Reconciliation must therefore mark an existing review stale and require renewed review; it must not rewrite the old dependency or restore a passing verdict automatically.

Invalidation rules:

- A review-dependency change makes the current review binding stale.
- A missing or changed review report makes its review binding stale.
- A missing or changed humanization integrity report anywhere in the complete inheritance chain makes the inherited review binding stale.
- A Word-dependency change invalidates content synchronization, structure, visual review, and author confirmation.
- Any actual DOCX hash change invalidates those same four checks, even when manuscript and template hashes did not change.
- Word acceptance requires a saved report or check artifact bound by path and hash to the checked DOCX hash. Missing, changed, or DOCX-mismatched evidence invalidates the recorded checks. Legacy Word records without this evidence remain readable but become blocked pending renewed acceptance.
- A state boolean is a record, not independent proof. Require its bound hashes and report or check artifact.

## Review and revision

`review_execution=completed` means only that the review ran. The content verdict is separate.

Blocking issues include nonexistent or incorrect sources, unsupported central claims, citation-object mismatches, factual errors, overgeneralized conclusions, hard requirement failures, and missing required sections or references. Non-blocking advice covers optional expression, transitions, or minor balance improvements that do not affect truth or compliance.

After targeted revision, save a recheck report against the new versions. A `passed` conclusion requires all of:

1. Every affected blocking issue is resolved.
2. Necessary associated locations are consistent.
3. No other blocking issue remains.

Otherwise record `blocked` or `limited`; completing the recheck does not imply a pass.

Scheduling after review is deterministic:

- `passed` or `passed_with_advice`: skip revision and continue to the next required content unit.
- `blocked`: select revision before Word, even when an old Word file is stale.
- `limited` with `limited_action=research`: return to evidence work.
- `limited` without an executable evidence route: stop with an explicit evidence or author-decision block.

A stale Word never outranks incomplete research, outline, manuscript, review, revision, or required humanization. An explicit `生成Word` request may select Word with `--request word --preview`; that exception produces an unaccepted preview only. A preview never completes the Word unit, even when every recorded check is true.

## Humanization

Prepare a candidate and a full pre-humanization snapshot. Humanize the candidate, not the master. Before commit, recompute the master hash and abort on mismatch.

Check numbers, units, citations, tables, factual relationships, qualifications, applicability boundaries, and conclusion strength. A valid pre-humanization `passed` or `passed_with_advice` review may be carried forward only when this integrity check passes and a distinct report is saved for that round. Preserve the full inheritance chain from the originally reviewed manuscript to the current manuscript; every link binds its old/new manuscript hashes and report hash, and every report remains required.

Without a valid passing review, commit the prose as unreviewed. After rejection, failed checks, or a start-hash conflict, run `cancel-humanize --reason rejected|failed|conflict`; it clears pending state and removes the candidate while preserving both the current master and the pre-humanization snapshot. Then `prepare-humanize` may create a fresh candidate without manual state edits.

## Tool handoffs

ARS agent files named below are internal role prompts. They guide inline work; they are not callable tools or independent agents.

### Literature research

```text
Invoke: $narrative-review:academic-research-suite -> ars-lit-review
Read: project.json, literature_matrix.md, state.json, supplied sources
Do: search and screen; separate metadata, abstract, and full-text status; record claim evidence and scope
Save: literature_matrix.md and evidence version
Verify: traceable entries, honest read levels, unsupported claims marked
Progress: record-progress <project> research
Stop: research unit complete, scope decision required, or essential evidence unavailable
```

### Outline

```text
Invoke: $narrative-review:academic-research-suite -> ars-outline
Read: project.json, literature_matrix.md, state.json, existing manuscript skeleton
Do: build chapters, word allocation, table plan, and evidence map; do not draft prose
Save: outline in manuscript.md or configured outline location; update state
Verify: project requirements and major evidence needs are covered
Progress: record-progress <project> outline
Stop: outline unit complete or an author structure decision is required
```

### Chapter drafting

```text
Invoke: $narrative-review:academic-research-suite -> academic-paper Phase 4
Read: project.json, literature_matrix.md, manuscript.md, state.json, authorized chapter batch
Do: draft that batch with claim-source mapping and explicit applicability limits
Save: manuscript.md; update hash, chapter progress, and derived-artifact staleness
Verify: batch completeness, citations, configured structure, tables, and source boundaries
Progress: record-progress <project> draft --batch <id>; add --manuscript-complete only for the checked final batch
Stop: batch complete, persistent writing prohibition, missing evidence, or author decision
```

### Citation and content review

```text
Invoke: $narrative-review:academic-research-suite -> ars-citation-check, then inline content-boundary review
Read: project.json, literature_matrix.md and evidence files, manuscript.md, state.json
Do: check citation pairs, source support, read level, applicability, conclusion strength, and requirements; do not edit
Save: reviews/<review-version>.md and separate execution/verdict fields
Verify: every finding has location, evidence, severity, and return stage
Stop: report complete; use limited when evidence prevents a complete verdict
```

### Targeted revision

```text
Invoke: $narrative-review:academic-research-suite -> ars-revision
Read: current manuscript and hash, evidence, state, selected findings
Do: fix selected problems and necessary associated locations; record reasons; avoid unrelated rewriting
Save: manuscript.md and a recheck report bound to new manuscript/evidence/config versions
Verify: affected issues resolved, associations consistent, global structure/citations intact, remaining blockers counted
Stop: issue set complete, author choice required, scope expansion detected, or verification fails
```

### Humanizer

```text
Invoke: $narrative-review:humanizer file mode on the prepared candidate
Read: candidate, snapshot, project voice/range, current valid review, state
Do: change expression only
Save: candidate, integrity report, then conditionally commit to manuscript.md
Verify: numbers, citations, tables, facts, qualifications, boundaries, and conclusion strength
Stop: commit succeeds, or run cancel-humanize after rejection, failed checks, or a start-hash conflict
```

### Word

```text
Invoke: $documents:documents create/edit and verify/render workflows
Read: manuscript.md, project.json Word config, template, state; old DOCX is format reference only
Do: generate DOCX, reopen it, check content/structure, render the actual file, inspect every page
Save: output DOCX, render output, and one Word acceptance report; record it with `record-word --report <report-path>` so its path and hash bind to the actual DOCX hash
Verify: source binding, content synchronization, structure, openability, and every rendered page
Stop: accepted final, explicitly unaccepted preview, missing template/metadata, or visible render failure
```

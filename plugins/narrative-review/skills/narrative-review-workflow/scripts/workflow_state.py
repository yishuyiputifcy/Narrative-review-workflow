#!/usr/bin/env python3
"""Deterministic state and version guards for narrative-review projects."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


DEFAULT_STAGES = ["research", "outline", "draft", "review", "revision", "humanize", "word"]
PASSING_VERDICTS = {"passed", "passed_with_advice"}


class WorkflowError(RuntimeError):
    pass


def file_hash(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def value_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def load_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        if default is not None:
            return copy.deepcopy(default)
        raise WorkflowError(f"missing required file: {path}")
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def new_state() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "units": {stage: "pending" for stage in DEFAULT_STAGES},
        "progress": {
            "research": {"status": "pending", "artifact": None},
            "outline": {"status": "pending", "artifact": None},
            "draft": {
                "status": "pending",
                "completed_batches": [],
                "manuscript_complete": False,
                "artifact": None,
            },
        },
        "versions": {},
        "review": {
            "execution": "not_run",
            "verdict": "unreviewed",
            "binding_valid": False,
            "dependencies": {},
            "blocking_count": None,
            "report": None,
            "stale_reasons": [],
        },
        "word": {
            "generated": False,
            "preview": False,
            "dependencies": {},
            "acceptance_evidence": None,
            "checked_docx_sha256": None,
            "observed_docx_sha256": None,
            "content_sync_checked": False,
            "structure_checked": False,
            "visual_checked": False,
            "author_confirmed": False,
            "stale_reasons": [],
        },
        "humanize": {"pending": None},
    }


def project_path(root: Path, project: dict[str, Any], key: str, fallback: str) -> Path:
    return root / project.get("paths", {}).get(key, fallback)


def evidence_paths(root: Path, project: dict[str, Any]) -> list[Path]:
    configured = project.get("paths", {}).get("evidence", ["literature_matrix.md"])
    if isinstance(configured, str):
        configured = [configured]
    return [root / item for item in configured]


def evidence_hash(root: Path, paths: list[Path]) -> str:
    resolved_root = root.resolve()
    entries = []
    for path in paths:
        try:
            identifier = path.resolve().relative_to(resolved_root).as_posix()
        except ValueError as error:
            raise WorkflowError(f"configured evidence must stay inside the project root: {path}") from error
        entries.append({"path": identifier, "sha256": file_hash(path)})
    return value_hash(entries)


def current_versions(root: Path, project: dict[str, Any]) -> dict[str, Any]:
    manuscript = project_path(root, project, "manuscript", "manuscript.md")
    template = project_path(root, project, "template", "inputs/template.docx")
    docx = project_path(root, project, "docx", "output/paper.docx")
    return {
        "manuscript_sha256": file_hash(manuscript),
        "evidence_sha256": evidence_hash(root, evidence_paths(root, project)),
        "review_config_sha256": value_hash(
            {"requirements": project.get("requirements", {}), "review": project.get("review_config", {})}
        ),
        "template_sha256": file_hash(template),
        "word_config_sha256": value_hash(project.get("word_config", {})),
        "docx_sha256": file_hash(docx),
    }


def review_dependencies(versions: dict[str, Any]) -> dict[str, Any]:
    return {
        "manuscript_sha256": versions["manuscript_sha256"],
        "evidence_sha256": versions["evidence_sha256"],
        "review_config_sha256": versions["review_config_sha256"],
    }


def word_dependencies(versions: dict[str, Any]) -> dict[str, Any]:
    return {
        "manuscript_sha256": versions["manuscript_sha256"],
        "template_sha256": versions["template_sha256"],
        "word_config_sha256": versions["word_config_sha256"],
    }


def changed_keys(recorded: dict[str, Any], current: dict[str, Any]) -> list[str]:
    return sorted(key for key in current if recorded.get(key) != current.get(key))


def invalidate_word(word: dict[str, Any], reasons: list[str]) -> None:
    word["preview"] = True
    word["content_sync_checked"] = False
    word["structure_checked"] = False
    word["visual_checked"] = False
    word["author_confirmed"] = False
    word["stale_reasons"] = sorted(set(word.get("stale_reasons", []) + reasons))


def artifact_problem(root: Path, record: dict[str, Any] | None, label: str) -> str | None:
    if not record or not record.get("path") or not record.get("sha256"):
        return f"{label}_record_missing"
    path = Path(record["path"])
    if not path.is_absolute():
        path = root / path
    actual = file_hash(path)
    if actual is None:
        return f"{label}_missing"
    if actual != record["sha256"]:
        return f"{label}_hash_changed"
    return None


def invalidate_review(review: dict[str, Any], reasons: list[str]) -> None:
    review["binding_valid"] = False
    review["execution"] = "stale"
    review["stale_reasons"] = sorted(set(review.get("stale_reasons", []) + reasons))


def reconcile(root: Path, project: dict[str, Any], state: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    state = copy.deepcopy(state)
    changes: list[str] = []
    versions = current_versions(root, project)
    state["versions"] = versions

    review = state.setdefault("review", new_state()["review"])
    expected_review = review_dependencies(versions)
    if review.get("binding_valid"):
        keys = changed_keys(review.get("dependencies", {}), expected_review)
        if keys:
            reasons = [f"review_dependency_changed:{key}" for key in keys]
            invalidate_review(review, reasons)
            changes.extend(reasons)

    if review.get("execution") in {"completed", "stale"} or review.get("binding_valid"):
        problem = artifact_problem(root, review.get("report"), "review_report")
        if problem:
            invalidate_review(review, [problem])
            changes.append(problem)
        chain = review.get("inheritance_chain", [])
        if not chain and review.get("inheritance"):
            chain = [review["inheritance"]]
        if chain:
            chain_reasons: list[str] = []
            for index, inheritance in enumerate(chain):
                label = "humanize_integrity_report" if index == 0 else f"humanize_integrity_report_{index + 1}"
                problem = artifact_problem(root, inheritance.get("integrity_report"), label)
                if problem:
                    chain_reasons.append(problem)
                if index and chain[index - 1].get("to_manuscript_sha256") != inheritance.get(
                    "from_manuscript_sha256"
                ):
                    chain_reasons.append("humanize_inheritance_chain_broken")

            base_dependencies = review.get("base_dependencies", {})
            if base_dependencies:
                if base_dependencies.get("manuscript_sha256") != chain[0].get("from_manuscript_sha256"):
                    chain_reasons.append("humanize_inheritance_base_mismatch")
                for key in ("evidence_sha256", "review_config_sha256"):
                    if base_dependencies.get(key) != review.get("dependencies", {}).get(key):
                        chain_reasons.append(f"humanize_inheritance_{key}_changed")
            if chain[-1].get("to_manuscript_sha256") != review.get("dependencies", {}).get(
                "manuscript_sha256"
            ):
                chain_reasons.append("humanize_inheritance_tip_mismatch")
            if chain_reasons:
                invalidate_review(review, chain_reasons)
                changes.extend(chain_reasons)

    word = state.setdefault("word", new_state()["word"])
    expected_word = word_dependencies(versions)
    if word.get("generated"):
        keys = changed_keys(word.get("dependencies", {}), expected_word)
        if keys:
            reasons = [f"word_dependency_changed:{key}" for key in keys]
            invalidate_word(word, reasons)
            changes.extend(reasons)

        actual_docx = versions["docx_sha256"]
        checked_docx = word.get("checked_docx_sha256")
        word["observed_docx_sha256"] = actual_docx
        if checked_docx and actual_docx != checked_docx:
            reason = "docx_hash_changed"
            invalidate_word(word, [reason])
            changes.append(reason)

        acceptance_evidence = word.get("acceptance_evidence")
        problem = artifact_problem(root, acceptance_evidence, "word_acceptance_evidence")
        if problem:
            invalidate_word(word, [problem])
            changes.append(problem)
        elif acceptance_evidence.get("docx_sha256") != actual_docx:
            reason = "word_acceptance_evidence_docx_mismatch"
            invalidate_word(word, [reason])
            changes.append(reason)

        if word.get("stale_reasons"):
            state.setdefault("units", {})["word"] = "blocked"

    return state, sorted(set(changes))


def load_project_state(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    project = load_json(root / "project.json")
    state = load_json(root / "state.json", new_state())
    return project, state


def save_state(root: Path, state: dict[str, Any]) -> None:
    save_json(root / "state.json", state)


def phase_complete(state: dict[str, Any], phase: str) -> bool:
    if state.get("units", {}).get(phase) == "completed":
        return True
    progress = state.get("progress", {}).get(phase, {})
    if phase == "draft":
        return progress.get("status") == "completed" and progress.get("manuscript_complete") is True
    return progress.get("status") == "completed"


def select_work_unit(
    project: dict[str, Any],
    state: dict[str, Any],
    *,
    turn_limit: str | None = None,
    requested_unit: str | None = None,
    preview: bool = False,
) -> dict[str, Any]:
    stages = project.get("workflow", {}).get("stage_order", DEFAULT_STAGES)
    prohibited = set(project.get("workflow", {}).get("prohibited_stages", []))

    review = state.get("review", {})
    content_passed = review.get("binding_valid") and review.get("verdict") in PASSING_VERDICTS

    if requested_unit is not None:
        if requested_unit not in stages:
            raise WorkflowError(f"unknown requested unit: {requested_unit}")
        if requested_unit == "word" and not content_passed and not preview:
            return {"action": "blocked", "unit": "word", "reason": "content_not_passed"}
        candidate = requested_unit
        reason = "explicit_preview" if requested_unit == "word" and preview else "explicit_request"
    elif not phase_complete(state, "research"):
        candidate, reason = "research", "research_incomplete"
    elif not phase_complete(state, "outline"):
        candidate, reason = "outline", "outline_incomplete"
    elif not phase_complete(state, "draft"):
        candidate, reason = "draft", "manuscript_incomplete"
    elif not review.get("binding_valid") or review.get("execution") in {"not_run", "stale"}:
        candidate, reason = "review", "review_required"
    elif review.get("verdict") == "blocked":
        candidate, reason = "revision", "blocking_review_findings"
    elif review.get("verdict") == "limited":
        if review.get("limited_action") == "research":
            candidate, reason = "research", "limited_review_needs_evidence"
        else:
            return {"action": "blocked", "unit": "review", "reason": "limited_review_requires_evidence_or_decision"}
    elif not content_passed:
        candidate, reason = "review", "content_not_passed"
    elif project.get("workflow", {}).get("require_humanize", True) and not phase_complete(state, "humanize"):
        candidate, reason = "humanize", "content_passed_revision_skipped"
    else:
        word = state.get("word", {})
        final_word = (
            word.get("generated")
            and not word.get("preview")
            and not word.get("stale_reasons")
            and (word.get("acceptance_evidence") or {}).get("docx_sha256") == word.get("checked_docx_sha256")
            and word.get("content_sync_checked")
            and word.get("structure_checked")
            and word.get("visual_checked")
        )
        if final_word:
            candidate, reason = "done", "workflow_complete"
        else:
            candidate, reason = "word", "content_prerequisites_satisfied"

    if candidate == "done":
        return {"action": "stop", "reason": "workflow_complete"}
    if candidate in prohibited:
        return {"action": "blocked", "unit": candidate, "reason": "project_prohibition"}
    if turn_limit is not None:
        if turn_limit not in stages:
            raise WorkflowError(f"unknown turn limit: {turn_limit}")
        if stages.index(candidate) > stages.index(turn_limit):
            return {"action": "stop", "unit": candidate, "reason": "turn_limit_reached"}
    result: dict[str, Any] = {"action": "execute", "unit": candidate, "reason": reason}
    if candidate == "word":
        result["preview"] = bool(preview or not content_passed)
    return result


def report_record(root: Path, report_path: str) -> dict[str, str]:
    path = Path(report_path)
    if not path.is_absolute():
        path = root / path
    digest = file_hash(path)
    if digest is None:
        raise WorkflowError(f"missing report or check artifact: {path}")
    try:
        saved_path = str(path.relative_to(root))
    except ValueError:
        saved_path = str(path)
    return {"path": saved_path, "sha256": digest}


def record_progress(
    root: Path,
    project: dict[str, Any],
    state: dict[str, Any],
    *,
    unit: str,
    artifact_path: str | None = None,
    batch: str | None = None,
    manuscript_complete: bool = False,
) -> dict[str, Any]:
    if unit not in {"research", "outline", "draft"}:
        raise WorkflowError(f"unsupported progress unit: {unit}")
    state, _ = reconcile(root, project, state)
    configured_evidence = project.get("paths", {}).get("evidence", ["literature_matrix.md"])
    if isinstance(configured_evidence, str):
        configured_evidence = [configured_evidence]
    defaults = {
        "research": configured_evidence[0],
        "outline": project.get("paths", {}).get("manuscript", "manuscript.md"),
        "draft": project.get("paths", {}).get("manuscript", "manuscript.md"),
    }
    artifact = report_record(root, artifact_path or defaults[unit])
    progress = state.setdefault("progress", new_state()["progress"])

    if unit in {"research", "outline"}:
        progress[unit] = {"status": "completed", "artifact": artifact}
        state.setdefault("units", {})[unit] = "completed"
        return state

    if not batch:
        raise WorkflowError("draft progress requires a chapter batch name")
    draft = progress.setdefault("draft", new_state()["progress"]["draft"])
    completed_batches = draft.setdefault("completed_batches", [])
    if batch not in completed_batches:
        completed_batches.append(batch)
    draft["artifact"] = artifact
    draft["manuscript_complete"] = manuscript_complete
    draft["status"] = "completed" if manuscript_complete else "in_progress"
    state.setdefault("units", {})["draft"] = "completed" if manuscript_complete else "in_progress"
    return state


def record_review(
    root: Path,
    project: dict[str, Any],
    state: dict[str, Any],
    *,
    verdict: str,
    blocking_count: int,
    report_path: str,
    kind: str = "review",
    limited_action: str | None = None,
) -> dict[str, Any]:
    if verdict not in {"passed", "passed_with_advice", "blocked", "limited"}:
        raise WorkflowError(f"invalid review verdict: {verdict}")
    if verdict in PASSING_VERDICTS and blocking_count != 0:
        raise WorkflowError("a passing verdict requires zero blocking issues")
    if limited_action not in {None, "research", "blocked"}:
        raise WorkflowError(f"invalid limited action: {limited_action}")
    if verdict != "limited" and limited_action is not None:
        raise WorkflowError("limited action is valid only for a limited review")

    state, _ = reconcile(root, project, state)
    versions = state["versions"]
    state["review"] = {
        "execution": "completed",
        "verdict": verdict,
        "binding_valid": True,
        "dependencies": review_dependencies(versions),
        "blocking_count": blocking_count,
        "report": report_record(root, report_path),
        "kind": kind,
        "limited_action": limited_action,
        "stale_reasons": [],
    }
    state.setdefault("units", {})["review"] = "completed"
    return state


def record_revision_recheck(
    root: Path,
    project: dict[str, Any],
    state: dict[str, Any],
    *,
    affected_resolved: bool,
    associations_pass: bool,
    remaining_blockers: int,
    report_path: str,
) -> dict[str, Any]:
    passed = affected_resolved and associations_pass and remaining_blockers == 0
    verdict = "passed" if passed else "blocked"
    state = record_review(
        root,
        project,
        state,
        verdict=verdict,
        blocking_count=remaining_blockers if remaining_blockers > 0 else (0 if passed else 1),
        report_path=report_path,
        kind="revision_recheck",
    )
    state["review"]["recheck"] = {
        "affected_issues_resolved": affected_resolved,
        "associations_pass": associations_pass,
        "remaining_blockers": remaining_blockers,
    }
    state.setdefault("units", {})["revision"] = "completed" if passed else "blocked"
    return state


def record_word(
    root: Path,
    project: dict[str, Any],
    state: dict[str, Any],
    *,
    content_sync: bool,
    structure: bool,
    visual: bool,
    author_confirmed: bool,
    preview: bool,
    report_path: str | None = None,
) -> dict[str, Any]:
    state, _ = reconcile(root, project, state)
    versions = state["versions"]
    if versions["docx_sha256"] is None:
        raise WorkflowError("cannot record Word checks: DOCX does not exist")
    review = state.get("review", {})
    content_passed = review.get("binding_valid") and review.get("verdict") in PASSING_VERDICTS
    acceptance_evidence = report_record(root, report_path) if report_path else None
    if acceptance_evidence:
        acceptance_evidence["docx_sha256"] = versions["docx_sha256"]
    final_ready = bool(
        content_passed and content_sync and structure and visual and acceptance_evidence and not preview
    )
    if author_confirmed and not final_ready:
        raise WorkflowError("author confirmation requires a current passing review plus sync, structure, and visual checks")
    state["word"] = {
        "generated": True,
        "preview": preview or not final_ready,
        "dependencies": word_dependencies(versions),
        "acceptance_evidence": acceptance_evidence,
        "checked_docx_sha256": versions["docx_sha256"],
        "observed_docx_sha256": versions["docx_sha256"],
        "content_sync_checked": content_sync,
        "structure_checked": structure,
        "visual_checked": visual,
        "author_confirmed": author_confirmed,
        "stale_reasons": [] if acceptance_evidence else ["word_acceptance_evidence_record_missing"],
    }
    state.setdefault("units", {})["word"] = "completed" if final_ready else "blocked"
    return state


def prepare_humanize(root: Path, project: dict[str, Any], state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    state, _ = reconcile(root, project, state)
    manuscript = project_path(root, project, "manuscript", "manuscript.md")
    start_hash = file_hash(manuscript)
    if start_hash is None:
        raise WorkflowError("cannot humanize: manuscript does not exist")

    pending = state.setdefault("humanize", {}).get("pending")
    if pending:
        pending_candidate = root / pending["candidate"]
        pending_snapshot = root / pending["snapshot"]
        if pending.get("start_sha256") == start_hash and pending_candidate.is_file() and pending_snapshot.is_file():
            return state, pending
        raise WorkflowError("another humanization candidate is pending")

    reviews = root / "reviews"
    reviews.mkdir(parents=True, exist_ok=True)
    snapshot = reviews / f"humanize-before-{start_hash}.md"
    candidate = reviews / f"humanize-candidate-{start_hash}.md"
    if snapshot.exists() and file_hash(snapshot) != start_hash:
        raise WorkflowError("existing pre-humanization snapshot does not match the current manuscript")
    if not snapshot.exists():
        shutil.copy2(manuscript, snapshot)
    shutil.copy2(manuscript, candidate)
    pending = {
        "start_sha256": start_hash,
        "snapshot": str(snapshot.relative_to(root)),
        "candidate": str(candidate.relative_to(root)),
    }
    state["humanize"]["pending"] = pending
    return state, pending


def commit_humanize(
    root: Path,
    project: dict[str, Any],
    state: dict[str, Any],
    *,
    expected_sha256: str,
    integrity_report: str,
    carry_forward: bool,
) -> dict[str, Any]:
    state, _ = reconcile(root, project, state)
    pending = state.get("humanize", {}).get("pending")
    if not pending:
        raise WorkflowError("no pending humanization candidate")
    manuscript = project_path(root, project, "manuscript", "manuscript.md")
    actual_start = file_hash(manuscript)
    if actual_start != expected_sha256 or pending.get("start_sha256") != expected_sha256:
        raise WorkflowError("manuscript changed after humanization started; refusing to overwrite")

    snapshot = root / pending["snapshot"]
    candidate = root / pending["candidate"]
    if file_hash(snapshot) != expected_sha256:
        raise WorkflowError("pre-humanization snapshot does not match the starting manuscript")
    if not candidate.is_file():
        raise WorkflowError("humanization candidate is missing")
    integrity = report_record(root, integrity_report)

    old_review = copy.deepcopy(state.get("review", {}))
    if carry_forward and not (
        old_review.get("binding_valid") and old_review.get("verdict") in PASSING_VERDICTS
    ):
        raise WorkflowError("review carry-forward requires a current passing review")
    chain: list[dict[str, Any]] = []
    base_dependencies: dict[str, Any] = {}
    if carry_forward:
        chain = copy.deepcopy(old_review.get("inheritance_chain", []))
        if not chain and old_review.get("inheritance"):
            chain = [copy.deepcopy(old_review["inheritance"])]
        used_reports = {item.get("integrity_report", {}).get("path") for item in chain}
        if integrity["path"] in used_reports:
            raise WorkflowError("each humanization round requires a distinct integrity report")
        base_dependencies = copy.deepcopy(old_review.get("base_dependencies", old_review["dependencies"]))

    os.replace(candidate, manuscript)
    state["humanize"]["pending"] = None
    state, _ = reconcile(root, project, state)
    new_hash = state["versions"]["manuscript_sha256"]

    if carry_forward:
        old_dependencies = old_review["dependencies"]
        current_dependencies = review_dependencies(state["versions"])
        for key in ("evidence_sha256", "review_config_sha256"):
            if old_dependencies.get(key) != current_dependencies.get(key):
                state["review"] = new_state()["review"]
                state["review"]["stale_reasons"] = [f"humanize_inheritance_failed:{key}_changed"]
                state.setdefault("units", {})["review"] = "pending"
                break
        else:
            chain.append(
                {
                    "from_manuscript_sha256": expected_sha256,
                    "to_manuscript_sha256": new_hash,
                    "integrity_report": integrity,
                }
            )
            state["review"] = old_review
            state["review"]["binding_valid"] = True
            state["review"]["execution"] = "completed"
            state["review"]["dependencies"] = current_dependencies
            state["review"]["base_dependencies"] = base_dependencies
            state["review"]["inheritance_chain"] = chain
            state["review"].pop("inheritance", None)
            state["review"]["stale_reasons"] = []
    else:
        state["review"]["binding_valid"] = False
        state["review"]["execution"] = "not_run"
        state["review"]["verdict"] = "unreviewed"
        state["review"]["dependencies"] = {}
        state["review"]["blocking_count"] = None
        state["review"]["report"] = None
        state["review"]["stale_reasons"] = ["humanized_without_review_inheritance"]
        state.setdefault("units", {})["review"] = "pending"

    state.setdefault("units", {})["humanize"] = "completed"
    return state


def cancel_humanize(root: Path, state: dict[str, Any], *, reason: str) -> dict[str, Any]:
    if reason not in {"rejected", "failed", "conflict"}:
        raise WorkflowError(f"invalid humanization cancellation reason: {reason}")
    state = copy.deepcopy(state)
    humanize = state.setdefault("humanize", {"pending": None})
    pending = humanize.get("pending")
    if not pending:
        raise WorkflowError("no pending humanization candidate")

    candidate = (root / pending["candidate"]).resolve()
    reviews = (root / "reviews").resolve()
    if candidate.parent != reviews:
        raise WorkflowError("pending humanization candidate is outside the reviews directory")
    if candidate.is_file():
        candidate.unlink()
    humanize["pending"] = None
    humanize["last_cancelled"] = {"reason": reason, **pending}
    state.setdefault("units", {})["humanize"] = "pending"
    return state


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("project")
    inspect_parser.add_argument("--write", action="store_true")

    next_parser = subparsers.add_parser("next")
    next_parser.add_argument("project")
    next_parser.add_argument("--read-only", action="store_true", help="select using current files without saving state")
    next_parser.add_argument("--turn-limit", choices=DEFAULT_STAGES)
    next_parser.add_argument("--request", choices=DEFAULT_STAGES)
    next_parser.add_argument("--preview", action="store_true")

    progress_parser = subparsers.add_parser("record-progress")
    progress_parser.add_argument("project")
    progress_parser.add_argument("unit", choices=["research", "outline", "draft"])
    progress_parser.add_argument("--artifact")
    progress_parser.add_argument("--batch")
    progress_parser.add_argument("--manuscript-complete", action="store_true")

    review_parser = subparsers.add_parser("record-review")
    review_parser.add_argument("project")
    review_parser.add_argument("--verdict", required=True, choices=["passed", "passed_with_advice", "blocked", "limited"])
    review_parser.add_argument("--blocking-count", required=True, type=int)
    review_parser.add_argument("--report", required=True)
    review_parser.add_argument("--limited-action", choices=["research", "blocked"])

    recheck_parser = subparsers.add_parser("record-recheck")
    recheck_parser.add_argument("project")
    recheck_parser.add_argument("--affected-resolved", action="store_true")
    recheck_parser.add_argument("--associations-pass", action="store_true")
    recheck_parser.add_argument("--remaining-blockers", required=True, type=int)
    recheck_parser.add_argument("--report", required=True)

    word_parser = subparsers.add_parser("record-word")
    word_parser.add_argument("project")
    word_parser.add_argument("--content-sync", action="store_true")
    word_parser.add_argument("--structure", action="store_true")
    word_parser.add_argument("--visual", action="store_true")
    word_parser.add_argument("--author-confirmed", action="store_true")
    word_parser.add_argument("--preview", action="store_true")
    word_parser.add_argument("--report")

    prepare_parser = subparsers.add_parser("prepare-humanize")
    prepare_parser.add_argument("project")

    commit_parser = subparsers.add_parser("commit-humanize")
    commit_parser.add_argument("project")
    commit_parser.add_argument("--expected-sha256", required=True)
    commit_parser.add_argument("--integrity-report", required=True)
    commit_parser.add_argument("--carry-forward", action="store_true")

    cancel_parser = subparsers.add_parser("cancel-humanize")
    cancel_parser.add_argument("project")
    cancel_parser.add_argument("--reason", required=True, choices=["rejected", "failed", "conflict"])

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    root = Path(args.project).resolve()
    project, state = load_project_state(root)

    if args.command == "inspect":
        state, changes = reconcile(root, project, state)
        if args.write:
            save_state(root, state)
        result: Any = {"versions": state["versions"], "changes": changes, "state_written": args.write}
    elif args.command == "next":
        state, changes = reconcile(root, project, state)
        if not args.read_only:
            save_state(root, state)
        result = {
            "reconciliation": changes,
            "state_written": not args.read_only,
            **select_work_unit(
                project,
                state,
                turn_limit=args.turn_limit,
                requested_unit=args.request,
                preview=args.preview,
            ),
        }
    elif args.command == "record-progress":
        state = record_progress(
            root,
            project,
            state,
            unit=args.unit,
            artifact_path=args.artifact,
            batch=args.batch,
            manuscript_complete=args.manuscript_complete,
        )
        save_state(root, state)
        result = state["progress"][args.unit]
    elif args.command == "record-review":
        state = record_review(
            root,
            project,
            state,
            verdict=args.verdict,
            blocking_count=args.blocking_count,
            report_path=args.report,
            limited_action=args.limited_action,
        )
        save_state(root, state)
        result = state["review"]
    elif args.command == "record-recheck":
        state = record_revision_recheck(
            root,
            project,
            state,
            affected_resolved=args.affected_resolved,
            associations_pass=args.associations_pass,
            remaining_blockers=args.remaining_blockers,
            report_path=args.report,
        )
        save_state(root, state)
        result = state["review"]
    elif args.command == "record-word":
        state = record_word(
            root,
            project,
            state,
            content_sync=args.content_sync,
            structure=args.structure,
            visual=args.visual,
            author_confirmed=args.author_confirmed,
            preview=args.preview,
            report_path=args.report,
        )
        save_state(root, state)
        result = state["word"]
    elif args.command == "prepare-humanize":
        state, result = prepare_humanize(root, project, state)
        save_state(root, state)
    elif args.command == "commit-humanize":
        state = commit_humanize(
            root,
            project,
            state,
            expected_sha256=args.expected_sha256,
            integrity_report=args.integrity_report,
            carry_forward=args.carry_forward,
        )
        save_state(root, state)
        result = {"manuscript_sha256": state["versions"]["manuscript_sha256"], "review": state["review"]}
    elif args.command == "cancel-humanize":
        state = cancel_humanize(root, state, reason=args.reason)
        save_state(root, state)
        result = state["humanize"]
    else:
        raise AssertionError(args.command)

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except WorkflowError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)

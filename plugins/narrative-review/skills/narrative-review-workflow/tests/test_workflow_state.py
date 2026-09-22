import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "workflow_state.py"
SPEC = importlib.util.spec_from_file_location("workflow_state", SCRIPT)
workflow = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(workflow)


class WorkflowStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = {
            "requirements": {"kind": "narrative_review"},
            "review_config": {"citation_style": "configured"},
            "word_config": {"include_english_abstract": False},
            "workflow": {"stage_order": workflow.DEFAULT_STAGES, "prohibited_stages": []},
            "paths": {
                "manuscript": "manuscript.md",
                "evidence": ["literature_matrix.md"],
                "template": "inputs/template.docx",
                "docx": "output/paper.docx",
            },
        }
        (self.root / "project.json").write_text(json.dumps(self.project), encoding="utf-8")
        (self.root / "manuscript.md").write_text("# Draft\n\nSupported claim [1].\n", encoding="utf-8")
        (self.root / "literature_matrix.md").write_text("# Matrix\n\n[1] source\n", encoding="utf-8")
        self.state = workflow.new_state()

    def tearDown(self):
        self.temp.cleanup()

    def write_report(self, name="review.md"):
        path = self.root / "reviews" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("review evidence\n", encoding="utf-8")
        return str(path.relative_to(self.root))

    def complete_foundation(self, state=None):
        state = state or self.state
        state = workflow.record_progress(self.root, self.project, state, unit="research")
        state = workflow.record_progress(self.root, self.project, state, unit="outline")
        state = workflow.record_progress(
            self.root,
            self.project,
            state,
            unit="draft",
            batch="chapters-1-4",
            manuscript_complete=False,
        )
        self.assertEqual(workflow.select_work_unit(self.project, state)["unit"], "draft")
        return workflow.record_progress(
            self.root,
            self.project,
            state,
            unit="draft",
            batch="chapters-5-9",
            manuscript_complete=True,
        )

    def complete_content_review(self):
        state = self.complete_foundation()
        state = workflow.record_review(
            self.root,
            self.project,
            state,
            verdict="passed",
            blocking_count=0,
            report_path=self.write_report("content-review.md"),
        )
        state["units"]["humanize"] = "completed"
        return state

    def write_docx(self, content=b"docx version"):
        output = self.root / "output" / "paper.docx"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content)
        return output

    def run_next_cli(self, *options):
        result = subprocess.run(
            [sys.executable, "-B", "-X", "utf8", str(SCRIPT), "next", str(self.root), *options],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def project_snapshot(self):
        return {
            str(path.relative_to(self.root)): (path.read_bytes(), path.stat().st_mtime_ns)
            if path.is_file() else None
            for path in self.root.rglob("*")
        }

    def test_next_read_only_does_not_create_state(self):
        before = self.project_snapshot()
        for options, expected in (
            ([], ("execute", "research")),
            (["--request", "word"], ("blocked", "word")),
            (["--request", "word", "--preview"], ("execute", "word")),
            (["--request", "outline", "--turn-limit", "research"], ("stop", "outline")),
        ):
            with self.subTest(options=options):
                result = self.run_next_cli("--read-only", *options)
                self.assertEqual((result["action"], result["unit"]), expected)
                self.assertFalse(result["state_written"])
                self.assertEqual(self.project_snapshot(), before)

    def test_next_read_only_reconciles_without_saving(self):
        workflow.save_state(self.root, self.complete_content_review())
        (self.root / "literature_matrix.md").write_text("changed evidence\n", encoding="utf-8")
        before = self.project_snapshot()

        result = self.run_next_cli("--read-only")

        self.assertEqual(result["unit"], "review")
        self.assertIn("review_dependency_changed:evidence_sha256", result["reconciliation"])
        self.assertFalse(result["state_written"])
        self.assertEqual(self.project_snapshot(), before)

    def test_next_default_still_persists_reconciliation(self):
        result = self.run_next_cli()
        self.assertEqual(result["unit"], "research")
        self.assertTrue(result["state_written"])
        self.assertTrue((self.root / "state.json").is_file())

        workflow.save_state(self.root, self.complete_content_review())
        (self.root / "literature_matrix.md").write_text("changed evidence\n", encoding="utf-8")
        result = self.run_next_cli()
        saved = workflow.load_json(self.root / "state.json")
        self.assertEqual(result["unit"], "review")
        self.assertTrue(result["state_written"])
        self.assertFalse(saved["review"]["binding_valid"])
        self.assertEqual(saved["review"]["execution"], "stale")

    def test_turn_only_research_stops_then_bare_continue_selects_outline(self):
        action = workflow.select_work_unit(self.project, self.state, turn_limit="research")
        self.assertEqual(action["unit"], "research")
        self.state = workflow.record_progress(self.root, self.project, self.state, unit="research")
        capped = workflow.select_work_unit(self.project, self.state, turn_limit="research")
        self.assertEqual(capped, {"action": "stop", "unit": "outline", "reason": "turn_limit_reached"})
        continued = workflow.select_work_unit(self.project, self.state)
        self.assertEqual(continued["unit"], "outline")

    def test_continue_after_evidence_change_selects_review(self):
        self.state = self.complete_foundation()
        self.state = workflow.record_review(
            self.root,
            self.project,
            self.state,
            verdict="passed",
            blocking_count=0,
            report_path=self.write_report(),
        )
        (self.root / "literature_matrix.md").write_text("# Matrix\n\n[1] changed evidence\n", encoding="utf-8")
        reconciled, changes = workflow.reconcile(self.root, self.project, self.state)
        self.assertIn("review_dependency_changed:evidence_sha256", changes)
        self.assertEqual(reconciled["review"]["execution"], "stale")
        self.assertEqual(workflow.select_work_unit(self.project, reconciled)["unit"], "review")

    def test_same_project_content_keeps_review_binding_after_root_move(self):
        state = self.complete_foundation()
        state = workflow.record_review(
            self.root,
            self.project,
            state,
            verdict="passed",
            blocking_count=0,
            report_path=self.write_report("migration-review.md"),
        )
        workflow.save_state(self.root, state)

        with tempfile.TemporaryDirectory() as destination_parent:
            destination = Path(destination_parent) / "moved-project"
            shutil.copytree(self.root, destination)
            moved_project, moved_state = workflow.load_project_state(destination)
            reconciled, changes = workflow.reconcile(destination, moved_project, moved_state)

        self.assertNotIn("review_dependency_changed:evidence_sha256", changes)
        self.assertTrue(reconciled["review"]["binding_valid"])

    def test_deleted_evidence_invalidates_review(self):
        state = self.complete_foundation()
        state = workflow.record_review(
            self.root,
            self.project,
            state,
            verdict="passed",
            blocking_count=0,
            report_path=self.write_report("deletion-review.md"),
        )
        (self.root / "literature_matrix.md").unlink()

        reconciled, changes = workflow.reconcile(self.root, self.project, state)

        self.assertIn("review_dependency_changed:evidence_sha256", changes)
        self.assertFalse(reconciled["review"]["binding_valid"])

    def test_changed_evidence_reference_invalidates_even_when_bytes_match(self):
        state = self.complete_foundation()
        state = workflow.record_review(
            self.root,
            self.project,
            state,
            verdict="passed",
            blocking_count=0,
            report_path=self.write_report("reference-review.md"),
        )
        replacement = self.root / "replacement-matrix.md"
        replacement.write_bytes((self.root / "literature_matrix.md").read_bytes())
        changed_project = json.loads(json.dumps(self.project))
        changed_project["paths"]["evidence"] = ["replacement-matrix.md"]

        reconciled, changes = workflow.reconcile(self.root, changed_project, state)

        self.assertIn("review_dependency_changed:evidence_sha256", changes)
        self.assertFalse(reconciled["review"]["binding_valid"])

    def test_equivalent_internal_evidence_path_keeps_review_binding(self):
        state = self.complete_foundation()
        state = workflow.record_review(
            self.root,
            self.project,
            state,
            verdict="passed",
            blocking_count=0,
            report_path=self.write_report("equivalent-path-review.md"),
        )
        equivalent_project = json.loads(json.dumps(self.project))
        equivalent_project["paths"]["evidence"] = ["./evidence/../literature_matrix.md"]

        reconciled, changes = workflow.reconcile(self.root, equivalent_project, state)

        self.assertNotIn("review_dependency_changed:evidence_sha256", changes)
        self.assertTrue(reconciled["review"]["binding_valid"])

    def test_legacy_absolute_path_evidence_hash_is_readable_but_stale(self):
        state = self.complete_foundation()
        state = workflow.record_review(
            self.root,
            self.project,
            state,
            verdict="passed",
            blocking_count=0,
            report_path=self.write_report("legacy-review.md"),
        )
        evidence = self.root / "literature_matrix.md"
        legacy_hash = workflow.value_hash([{"path": str(evidence), "sha256": workflow.file_hash(evidence)}])
        self.assertNotEqual(legacy_hash, state["versions"]["evidence_sha256"])
        state["versions"]["evidence_sha256"] = legacy_hash
        state["review"]["dependencies"]["evidence_sha256"] = legacy_hash
        workflow.save_state(self.root, state)

        loaded_project, loaded_state = workflow.load_project_state(self.root)
        reconciled, changes = workflow.reconcile(self.root, loaded_project, loaded_state)

        self.assertIn("review_dependency_changed:evidence_sha256", changes)
        self.assertEqual(reconciled["review"]["execution"], "stale")
        self.assertFalse(reconciled["review"]["binding_valid"])

    def test_evidence_outside_project_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / "external.md"
            external.write_text("external evidence\n", encoding="utf-8")
            changed_project = json.loads(json.dumps(self.project))
            changed_project["paths"]["evidence"] = [str(external)]

            with self.assertRaisesRegex(workflow.WorkflowError, "must stay inside the project root"):
                workflow.current_versions(self.root, changed_project)

    def test_internal_absolute_evidence_reference_is_local_only(self):
        absolute_project = json.loads(json.dumps(self.project))
        absolute_project["paths"]["evidence"] = [str(self.root / "literature_matrix.md")]
        (self.root / "project.json").write_text(json.dumps(absolute_project), encoding="utf-8")
        state = self.complete_foundation(workflow.new_state())
        state = workflow.record_review(
            self.root,
            absolute_project,
            state,
            verdict="passed",
            blocking_count=0,
            report_path=self.write_report("absolute-reference-review.md"),
        )
        workflow.save_state(self.root, state)
        self.assertTrue(state["review"]["binding_valid"])

        with tempfile.TemporaryDirectory() as destination_parent:
            destination = Path(destination_parent) / "moved-project"
            shutil.copytree(self.root, destination)
            moved_project, moved_state = workflow.load_project_state(destination)
            with self.assertRaisesRegex(workflow.WorkflowError, "must stay inside the project root"):
                workflow.reconcile(destination, moved_project, moved_state)

    def test_revision_recheck_creates_current_passing_conclusion(self):
        (self.root / "manuscript.md").write_text("# Revised\n\nBounded claim [1].\n", encoding="utf-8")
        state = workflow.record_revision_recheck(
            self.root,
            self.project,
            self.state,
            affected_resolved=True,
            associations_pass=True,
            remaining_blockers=0,
            report_path=self.write_report("recheck.md"),
        )
        self.assertTrue(state["review"]["binding_valid"])
        self.assertEqual(state["review"]["verdict"], "passed")
        self.assertEqual(state["review"]["kind"], "revision_recheck")
        self.assertEqual(state["review"]["dependencies"], workflow.review_dependencies(state["versions"]))

    def test_humanize_without_valid_review_cannot_inherit_pass(self):
        state, pending = workflow.prepare_humanize(self.root, self.project, self.state)
        (self.root / pending["candidate"]).write_text("# Draft\n\nNatural supported claim [1].\n", encoding="utf-8")
        integrity = self.write_report("humanize-integrity.md")
        with self.assertRaises(workflow.WorkflowError):
            workflow.commit_humanize(
                self.root,
                self.project,
                state,
                expected_sha256=pending["start_sha256"],
                integrity_report=integrity,
                carry_forward=True,
            )
        committed = workflow.commit_humanize(
            self.root,
            self.project,
            state,
            expected_sha256=pending["start_sha256"],
            integrity_report=integrity,
            carry_forward=False,
        )
        self.assertFalse(committed["review"]["binding_valid"])
        self.assertEqual(committed["review"]["verdict"], "unreviewed")

    def test_manual_docx_change_invalidates_every_docx_bound_check(self):
        self.state = self.complete_foundation()
        self.state = workflow.record_review(
            self.root,
            self.project,
            self.state,
            verdict="passed",
            blocking_count=0,
            report_path=self.write_report(),
        )
        output = self.root / "output" / "paper.docx"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"first docx version")
        state = workflow.record_word(
            self.root,
            self.project,
            self.state,
            content_sync=True,
            structure=True,
            visual=True,
            author_confirmed=True,
            preview=False,
            report_path=self.write_report("word-acceptance.md"),
        )
        output.write_bytes(b"manual change")
        state, changes = workflow.reconcile(self.root, self.project, state)
        self.assertIn("docx_hash_changed", changes)
        self.assertIn("word_acceptance_evidence_docx_mismatch", changes)
        self.assertEqual(state["units"]["word"], "blocked")
        self.assertTrue(state["word"]["preview"])
        for key in ("content_sync_checked", "structure_checked", "visual_checked", "author_confirmed"):
            self.assertFalse(state["word"][key])

    def test_humanize_commit_refuses_changed_master(self):
        state, pending = workflow.prepare_humanize(self.root, self.project, self.state)
        candidate = self.root / pending["candidate"]
        candidate.write_text("# Humanized candidate\n", encoding="utf-8")
        manuscript = self.root / "manuscript.md"
        manuscript.write_text("# Concurrent author edit\n", encoding="utf-8")
        before_commit = manuscript.read_text(encoding="utf-8")
        with self.assertRaises(workflow.WorkflowError):
            workflow.commit_humanize(
                self.root,
                self.project,
                state,
                expected_sha256=pending["start_sha256"],
                integrity_report=self.write_report("integrity.md"),
                carry_forward=False,
            )
        self.assertEqual(manuscript.read_text(encoding="utf-8"), before_commit)
        self.assertTrue(candidate.is_file())

    def test_word_without_current_content_pass_remains_preview(self):
        output = self.root / "output" / "paper.docx"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"unchecked content")
        state = workflow.record_word(
            self.root,
            self.project,
            self.state,
            content_sync=True,
            structure=True,
            visual=True,
            author_confirmed=False,
            preview=False,
            report_path=self.write_report("unchecked-word.md"),
        )
        self.assertTrue(state["word"]["preview"])
        self.assertEqual(state["units"]["word"], "blocked")

    def test_word_without_acceptance_evidence_cannot_complete(self):
        self.write_docx()
        state = workflow.record_word(
            self.root,
            self.project,
            self.complete_content_review(),
            content_sync=True,
            structure=True,
            visual=True,
            author_confirmed=False,
            preview=False,
            report_path=None,
        )
        self.assertEqual(state["units"]["word"], "blocked")
        self.assertTrue(state["word"]["preview"])
        self.assertIn("word_acceptance_evidence_record_missing", state["word"]["stale_reasons"])

    def test_changed_word_acceptance_evidence_invalidates_word(self):
        state = self.complete_content_review()
        self.write_docx()
        report = self.write_report("word-acceptance.md")
        state = workflow.record_word(
            self.root,
            self.project,
            state,
            content_sync=True,
            structure=True,
            visual=True,
            author_confirmed=False,
            preview=False,
            report_path=report,
        )
        (self.root / report).write_text("changed acceptance evidence\n", encoding="utf-8")
        state, changes = workflow.reconcile(self.root, self.project, state)
        self.assertIn("word_acceptance_evidence_hash_changed", changes)
        self.assertEqual(state["units"]["word"], "blocked")
        self.assertTrue(state["word"]["preview"])

    def test_missing_word_acceptance_evidence_invalidates_word(self):
        state = self.complete_content_review()
        self.write_docx()
        report = self.write_report("word-acceptance.md")
        state = workflow.record_word(
            self.root,
            self.project,
            state,
            content_sync=True,
            structure=True,
            visual=True,
            author_confirmed=False,
            preview=False,
            report_path=report,
        )
        (self.root / report).unlink()
        state, changes = workflow.reconcile(self.root, self.project, state)
        self.assertIn("word_acceptance_evidence_missing", changes)
        self.assertEqual(state["units"]["word"], "blocked")
        self.assertTrue(state["word"]["preview"])

    def test_preview_with_complete_checks_and_evidence_cannot_finish(self):
        state = self.complete_content_review()
        self.write_docx()
        state = workflow.record_word(
            self.root,
            self.project,
            state,
            content_sync=True,
            structure=True,
            visual=True,
            author_confirmed=False,
            preview=True,
            report_path=self.write_report("word-preview.md"),
        )
        self.assertEqual(state["units"]["word"], "blocked")
        self.assertTrue(state["word"]["preview"])
        self.assertEqual(workflow.select_work_unit(self.project, state)["unit"], "word")

    def test_bound_word_acceptance_evidence_allows_non_preview_completion(self):
        state = self.complete_content_review()
        output = self.write_docx()
        report = self.write_report("word-acceptance.md")
        state = workflow.record_word(
            self.root,
            self.project,
            state,
            content_sync=True,
            structure=True,
            visual=True,
            author_confirmed=False,
            preview=False,
            report_path=report,
        )
        self.assertEqual(state["units"]["word"], "completed")
        self.assertFalse(state["word"]["preview"])
        self.assertEqual(state["word"]["acceptance_evidence"]["docx_sha256"], workflow.file_hash(output))
        self.assertEqual(workflow.select_work_unit(self.project, state)["action"], "stop")

    def test_legacy_completed_word_without_evidence_becomes_blocked(self):
        state = self.complete_content_review()
        self.write_docx()
        state = workflow.record_word(
            self.root,
            self.project,
            state,
            content_sync=True,
            structure=True,
            visual=True,
            author_confirmed=False,
            preview=False,
            report_path=self.write_report("word-acceptance.md"),
        )
        state["word"].pop("acceptance_evidence")
        self.assertEqual(workflow.select_work_unit(self.project, state)["unit"], "word")
        state, changes = workflow.reconcile(self.root, self.project, state)
        self.assertIn("word_acceptance_evidence_record_missing", changes)
        self.assertEqual(state["units"]["word"], "blocked")
        self.assertTrue(state["word"]["preview"])

    def test_passing_review_skips_revision_in_continuous_chain(self):
        state = workflow.new_state()
        state = workflow.record_progress(self.root, self.project, state, unit="research")
        self.assertEqual(workflow.select_work_unit(self.project, state)["unit"], "outline")
        state = workflow.record_progress(self.root, self.project, state, unit="outline")
        state = workflow.record_progress(
            self.root,
            self.project,
            state,
            unit="draft",
            batch="first-half",
            manuscript_complete=False,
        )
        self.assertEqual(workflow.select_work_unit(self.project, state)["unit"], "draft")
        state = workflow.record_progress(
            self.root,
            self.project,
            state,
            unit="draft",
            batch="second-half",
            manuscript_complete=True,
        )
        self.assertEqual(workflow.select_work_unit(self.project, state)["unit"], "review")
        state = workflow.record_review(
            self.root,
            self.project,
            state,
            verdict="passed",
            blocking_count=0,
            report_path=self.write_report("chain-review.md"),
        )
        self.assertEqual(workflow.select_work_unit(self.project, state)["unit"], "humanize")

        state, pending = workflow.prepare_humanize(self.root, self.project, state)
        (self.root / pending["candidate"]).write_text("# Natural final draft\n\nSupported claim [1].\n", encoding="utf-8")
        state = workflow.commit_humanize(
            self.root,
            self.project,
            state,
            expected_sha256=pending["start_sha256"],
            integrity_report=self.write_report("chain-integrity.md"),
            carry_forward=True,
        )
        next_action = workflow.select_work_unit(self.project, state)
        self.assertEqual(next_action["unit"], "word")
        self.assertNotEqual(next_action["unit"], "revision")

        output = self.root / "output" / "paper.docx"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"final docx")
        state = workflow.record_word(
            self.root,
            self.project,
            state,
            content_sync=True,
            structure=True,
            visual=True,
            author_confirmed=True,
            preview=False,
            report_path=self.write_report("chain-word-acceptance.md"),
        )
        self.assertEqual(workflow.select_work_unit(self.project, state)["action"], "stop")

    def test_blocked_review_beats_stale_word(self):
        state = self.complete_foundation()
        state = workflow.record_review(
            self.root,
            self.project,
            state,
            verdict="blocked",
            blocking_count=2,
            report_path=self.write_report("blocked.md"),
        )
        output = self.root / "output" / "paper.docx"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"old preview")
        state = workflow.record_word(
            self.root,
            self.project,
            state,
            content_sync=False,
            structure=True,
            visual=False,
            author_confirmed=False,
            preview=True,
        )
        changed_project = json.loads(json.dumps(self.project))
        changed_project["word_config"]["page_margin_cm"] = 3
        state, changes = workflow.reconcile(self.root, changed_project, state)
        self.assertTrue(any(reason.startswith("word_dependency_changed") for reason in changes))
        self.assertEqual(workflow.select_work_unit(changed_project, state)["unit"], "revision")

    def test_missing_review_report_invalidates_review(self):
        state = self.complete_foundation()
        report = self.write_report("will-disappear.md")
        state = workflow.record_review(
            self.root,
            self.project,
            state,
            verdict="passed",
            blocking_count=0,
            report_path=report,
        )
        (self.root / report).unlink()
        state, changes = workflow.reconcile(self.root, self.project, state)
        self.assertIn("review_report_missing", changes)
        self.assertFalse(state["review"]["binding_valid"])
        self.assertEqual(workflow.select_work_unit(self.project, state)["unit"], "review")

    def test_changed_review_report_invalidates_review(self):
        state = self.complete_foundation()
        report = self.write_report("will-change.md")
        state = workflow.record_review(
            self.root,
            self.project,
            state,
            verdict="passed_with_advice",
            blocking_count=0,
            report_path=report,
        )
        (self.root / report).write_text("changed review evidence\n", encoding="utf-8")
        state, changes = workflow.reconcile(self.root, self.project, state)
        self.assertIn("review_report_hash_changed", changes)
        self.assertFalse(state["review"]["binding_valid"])

    def test_missing_humanize_integrity_report_invalidates_inherited_review(self):
        state = self.complete_foundation()
        state = workflow.record_review(
            self.root,
            self.project,
            state,
            verdict="passed",
            blocking_count=0,
            report_path=self.write_report("base-review.md"),
        )
        state, pending = workflow.prepare_humanize(self.root, self.project, state)
        (self.root / pending["candidate"]).write_text("# Humanized\n", encoding="utf-8")
        integrity = self.write_report("disappearing-integrity.md")
        state = workflow.commit_humanize(
            self.root,
            self.project,
            state,
            expected_sha256=pending["start_sha256"],
            integrity_report=integrity,
            carry_forward=True,
        )
        (self.root / integrity).unlink()
        state, changes = workflow.reconcile(self.root, self.project, state)
        self.assertIn("humanize_integrity_report_missing", changes)
        self.assertFalse(state["review"]["binding_valid"])

    def test_second_humanize_still_depends_on_first_integrity_report(self):
        state = self.complete_foundation()
        state = workflow.record_review(
            self.root,
            self.project,
            state,
            verdict="passed",
            blocking_count=0,
            report_path=self.write_report("base-two-round-review.md"),
        )
        reports = []
        for round_number in (1, 2):
            state, pending = workflow.prepare_humanize(self.root, self.project, state)
            (self.root / pending["candidate"]).write_text(
                f"# Humanized round {round_number}\n", encoding="utf-8"
            )
            reports.append(self.write_report(f"integrity-round-{round_number}.md"))
            state = workflow.commit_humanize(
                self.root,
                self.project,
                state,
                expected_sha256=pending["start_sha256"],
                integrity_report=reports[-1],
                carry_forward=True,
            )

        self.assertEqual(len(state["review"]["inheritance_chain"]), 2)
        (self.root / reports[0]).unlink()
        state, changes = workflow.reconcile(self.root, self.project, state)
        self.assertIn("humanize_integrity_report_missing", changes)
        self.assertFalse(state["review"]["binding_valid"])

    def test_rejected_humanize_candidate_can_be_prepared_again(self):
        state, pending = workflow.prepare_humanize(self.root, self.project, self.state)
        manuscript = self.root / "manuscript.md"
        snapshot = self.root / pending["snapshot"]
        candidate = self.root / pending["candidate"]
        original_master = manuscript.read_bytes()
        original_snapshot = snapshot.read_bytes()
        candidate.write_text("# Rejected candidate\n", encoding="utf-8")

        state = workflow.cancel_humanize(self.root, state, reason="rejected")
        self.assertIsNone(state["humanize"]["pending"])
        self.assertEqual(manuscript.read_bytes(), original_master)
        self.assertEqual(snapshot.read_bytes(), original_snapshot)
        self.assertFalse(candidate.exists())

        state, retried = workflow.prepare_humanize(self.root, self.project, state)
        self.assertEqual(retried["start_sha256"], pending["start_sha256"])
        self.assertEqual((self.root / retried["candidate"]).read_bytes(), original_master)
        self.assertEqual(snapshot.read_bytes(), original_snapshot)

    def test_conflicted_humanize_can_be_cancelled_and_prepared_again(self):
        state, pending = workflow.prepare_humanize(self.root, self.project, self.state)
        snapshot = self.root / pending["snapshot"]
        original_snapshot = snapshot.read_bytes()
        manuscript = self.root / "manuscript.md"
        manuscript.write_text("# Concurrent author edit\n", encoding="utf-8")
        concurrent_master = manuscript.read_bytes()

        with self.assertRaises(workflow.WorkflowError):
            workflow.commit_humanize(
                self.root,
                self.project,
                state,
                expected_sha256=pending["start_sha256"],
                integrity_report=self.write_report("conflict-integrity.md"),
                carry_forward=False,
            )
        state = workflow.cancel_humanize(self.root, state, reason="conflict")
        self.assertEqual(manuscript.read_bytes(), concurrent_master)
        self.assertEqual(snapshot.read_bytes(), original_snapshot)

        state, retried = workflow.prepare_humanize(self.root, self.project, state)
        self.assertEqual(retried["start_sha256"], workflow.file_hash(manuscript))
        self.assertEqual((self.root / retried["candidate"]).read_bytes(), concurrent_master)

    def test_limited_review_routes_to_evidence_or_blocks(self):
        state = self.complete_foundation()
        state = workflow.record_review(
            self.root,
            self.project,
            state,
            verdict="limited",
            blocking_count=1,
            report_path=self.write_report("limited.md"),
            limited_action="research",
        )
        action = workflow.select_work_unit(self.project, state)
        self.assertEqual((action["action"], action["unit"]), ("execute", "research"))

        state["review"]["limited_action"] = "blocked"
        action = workflow.select_work_unit(self.project, state)
        self.assertEqual(action["action"], "blocked")
        self.assertEqual(action["reason"], "limited_review_requires_evidence_or_decision")

    def test_explicit_word_preview_bypasses_content_gate_only_as_preview(self):
        action = workflow.select_work_unit(self.project, self.state, requested_unit="word", preview=True)
        self.assertEqual(action, {"action": "execute", "unit": "word", "reason": "explicit_preview", "preview": True})


if __name__ == "__main__":
    unittest.main()

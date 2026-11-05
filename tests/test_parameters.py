"""Regressions for run parameters.

The failure these guard against is quiet: a resumed run that silently uses
argparse defaults instead of what it was configured with still completes,
still writes a manifest, and reports nothing wrong -- the science is just
not the science that was asked for.
"""

import json
import tempfile
import unittest
from pathlib import Path

from drugstudio import parameters


class Defaults(unittest.TestCase):
    def test_every_parameter_declares_a_known_stage(self):
        for parameter in parameters.PARAMETERS:
            self.assertIn(parameter.stage, parameters.STAGE_ORDER, parameter.key)

    def test_defaults_round_trip_through_validation(self):
        self.assertEqual(parameters.validate(parameters.defaults()), parameters.defaults())

    def test_nothing_is_reported_as_changed_for_defaults(self):
        self.assertEqual(parameters.changed_from_default(parameters.defaults()), {})


class Validation(unittest.TestCase):
    def test_values_are_coerced_from_form_strings(self):
        values = parameters.validate({"exhaustiveness": "32", "receptor_ph": "6.5",
                                      "keep_receptor_water": "true"})
        self.assertEqual(values["exhaustiveness"], 32)
        self.assertEqual(values["receptor_ph"], 6.5)
        self.assertIs(values["keep_receptor_water"], True)

    def test_ranges_are_enforced(self):
        with self.assertRaises(ValueError):
            parameters.validate({"receptor_ph": 20})
        with self.assertRaises(ValueError):
            parameters.validate({"exhaustiveness": 0})

    def test_choices_are_enforced(self):
        with self.assertRaises(ValueError):
            parameters.validate({"p2rank_profile": "nonsense"})

    def test_residue_list_accepts_lines_or_commas(self):
        self.assertEqual(parameters.validate({"keep_receptor_residue": "ZN, MG\nCA"})["keep_receptor_residue"],
                         ["ZN", "MG", "CA"])

    def test_unknown_keys_are_dropped_not_rejected(self):
        # An older saved file must not block a run after a parameter is removed.
        self.assertEqual(parameters.validate({"gone": 1}), parameters.defaults())

    def test_partial_updates_keep_defaults_for_the_rest(self):
        values = parameters.validate({"seed": 7})
        self.assertEqual(values["seed"], 7)
        self.assertEqual(values["exhaustiveness"], parameters.BY_KEY["exhaustiveness"].default)


class AffectedStage(unittest.TestCase):
    def test_earliest_stage_wins(self):
        self.assertEqual(parameters.earliest_stage(["exhaustiveness", "receptor_ph"]), "receptor")
        self.assertEqual(parameters.earliest_stage(["exhaustiveness"]), "docking")
        self.assertIsNone(parameters.earliest_stage([]))


class Persistence(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)

    def tearDown(self):
        self._directory.cleanup()

    def write(self, value):
        path = self.root / parameters.RUNTIME_RELATIVE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_missing_file_yields_defaults(self):
        self.assertEqual(parameters.load(self.root), parameters.defaults())

    def test_saved_values_are_loaded(self):
        self.write({"exhaustiveness": 32, "optimize_xtb": True})
        values = parameters.load(self.root)
        self.assertEqual(values["exhaustiveness"], 32)
        self.assertIs(values["optimize_xtb"], True)

    def test_corrupt_file_falls_back_rather_than_raising(self):
        path = self.root / parameters.RUNTIME_RELATIVE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        self.assertEqual(parameters.load(self.root), parameters.defaults())

    def test_a_single_bad_value_does_not_discard_the_rest(self):
        self.write({"exhaustiveness": 32, "receptor_ph": "not a number"})
        values = parameters.load(self.root)
        self.assertEqual(values["exhaustiveness"], 32)
        self.assertEqual(values["receptor_ph"], parameters.BY_KEY["receptor_ph"].default)


class CommandLineRendering(unittest.TestCase):
    def test_defaults_render_without_absent_optionals(self):
        args = parameters.to_cli_args(parameters.defaults())
        self.assertIn("--exhaustiveness", args)
        # False booleans and unset text produce no flag at all.
        self.assertNotIn("--optimize-xtb", args)
        self.assertNotIn("--p2rank-executable", args)

    def test_booleans_render_as_bare_flags(self):
        args = parameters.to_cli_args(parameters.validate({"optimize_xtb": True,
                                                           "keep_receptor_water": True}))
        self.assertIn("--optimize-xtb", args)
        self.assertIn("--keep-receptor-water", args)

    def test_lists_repeat_their_flag(self):
        args = parameters.to_cli_args(parameters.validate({"keep_receptor_residue": ["ZN", "MG"]}))
        self.assertEqual(args.count("--keep-receptor-residue"), 2)
        self.assertIn("ZN", args)
        self.assertIn("MG", args)

    def test_every_rendered_flag_is_accepted_by_the_run_parser(self):
        # The CLI and this module declare the same knobs in two places; this
        # is what stops them drifting apart.
        from drugstudio.cli import build_parser
        values = parameters.validate({"optimize_xtb": True, "keep_receptor_water": True,
                                      "add_missing_residues": True,
                                      "keep_receptor_residue": ["ZN"],
                                      "p2rank_executable": "/usr/bin/prank"})
        argv = ["run", "--run-root", "/tmp/x", *parameters.to_cli_args(values)]
        parsed = build_parser().parse_args(argv)
        for key, value in values.items():
            self.assertEqual(getattr(parsed, key), value, key)


if __name__ == "__main__":
    unittest.main()


class ResumeUsesTheRunsOwnParameters(unittest.TestCase):
    """The bug this covers: the web server started runs with no parameter
    flags at all, so every resume reverted to argparse defaults while the
    manifest still advertised a runtime_configuration."""

    def setUp(self):
        from drugstudio.web.server import WebApplication
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        self.run_root = self.root / "proj" / "runs" / "r1"
        (self.run_root / "logs").mkdir(parents=True)
        (self.run_root / "inputs").mkdir(parents=True)
        (self.run_root / "manifest.json").write_text(json.dumps({
            "run_id": "r1", "status": "created", "target": {"target_id": "T"},
            "ligands": [], "artifacts": {}, "stage_results": {}, "tasks": [],
            "decisions": [], "approvals": [], "overrides": {}, "failures": [],
        }), encoding="utf-8")
        self.app = WebApplication(self.run_root, self.root, self.root, self.root, web_token=None)

    def tearDown(self):
        self.app.close()
        self._directory.cleanup()

    def started_command(self):
        """Start a workflow with Popen stubbed, returning the argv it built."""
        import subprocess
        captured = {}

        class FakeProcess:
            pid = 4321

            def poll(self):
                return None

        def fake_popen(command, **kwargs):
            captured["command"] = command
            return FakeProcess()

        real = subprocess.Popen
        subprocess.Popen = fake_popen
        try:
            self.app.start_workflow({}, self.run_root)
        finally:
            subprocess.Popen = real
            self.app._processes.clear()
        return captured["command"]

    def test_saved_parameters_reach_the_subprocess(self):
        self.app.save_run_parameters(
            {"values": {"exhaustiveness": 64, "seed": 99, "optimize_xtb": True,
                        "keep_receptor_residue": ["ZN"]}},
            self.run_root)
        command = self.started_command()
        self.assertIn("--exhaustiveness", command)
        self.assertEqual(command[command.index("--exhaustiveness") + 1], "64")
        self.assertEqual(command[command.index("--seed") + 1], "99")
        self.assertIn("--optimize-xtb", command)
        self.assertEqual(command[command.index("--keep-receptor-residue") + 1], "ZN")

    def test_the_parser_accepts_the_command_that_was_built(self):
        from drugstudio.cli import build_parser
        self.app.save_run_parameters({"values": {"exhaustiveness": 64, "p2rank_profile": "alphafold"}},
                                     self.run_root)
        command = self.started_command()
        parsed = build_parser().parse_args(command[command.index("run"):])
        self.assertEqual(parsed.exhaustiveness, 64)
        self.assertEqual(parsed.p2rank_profile, "alphafold")

    def test_saving_records_what_changed(self):
        result = self.app.save_run_parameters({"values": {"receptor_ph": 6.0}}, self.run_root)
        self.assertEqual(result["updated"], ["receptor_ph"])
        # Receptor runs before docking, so the change invalidates from there.
        self.assertEqual(result["affects_from"], "receptor")
        self.assertIn("receptor_ph", result["changed"])

    def test_a_change_lands_on_the_event_timeline(self):
        self.app.save_run_parameters({"values": {"seed": 5}}, self.run_root)
        events = self.app.events_for(self.run_root).read_since(0)
        updates = [event for event in events if event.get("type") == "parameters_updated"]
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["changed"], ["seed"])

    def test_saving_identical_values_records_nothing(self):
        self.app.save_run_parameters({"values": {"seed": 5}}, self.run_root)
        self.app.save_run_parameters({"values": {"seed": 5}}, self.run_root)
        updates = [event for event in self.app.events_for(self.run_root).read_since(0)
                   if event.get("type") == "parameters_updated"]
        self.assertEqual(len(updates), 1)

    def test_parameters_are_rejected_while_a_workflow_runs(self):
        class Busy:
            def poll(self):
                return None

        self.app._processes[self.run_root.resolve()] = Busy()
        try:
            with self.assertRaises(RuntimeError):
                self.app.save_run_parameters({"values": {"seed": 1}}, self.run_root)
            self.assertFalse(self.app.run_parameters(self.run_root)["editable"])
        finally:
            self.app._processes.clear()

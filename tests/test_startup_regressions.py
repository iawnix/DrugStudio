"""Regression checks for a fresh checkout and an empty workbench."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from drugstudio.integrations.tool_paths import external_runner
from drugstudio.web.server import WebApplication


class StartupRegressions(unittest.TestCase):
    def test_empty_runs_root_can_construct_the_workbench(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = root / "runs"
            workspace = root / "projects"
            workspace.mkdir()
            (workspace / "new-project").mkdir()
            app = WebApplication(None, workspace, runs, workspace)
            self.addCleanup(app.close)
            state = app.empty_snapshot(workspace / "new-project")
            self.assertIsNone(state["run_id"])
            self.assertEqual(state["project"]["project_id"], "new-project")
            self.assertEqual(app.list_runs("new-project"), [])

    def test_runner_can_be_configured_for_an_installed_package(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = Path(directory) / "runner.sh"
            runner.write_text("#!/bin/sh\n", encoding="utf-8")
            runner.chmod(0o755)
            with patch.dict("os.environ", {"DRUGSTUDIO_TEST_RUNNER": str(runner)}):
                self.assertEqual(
                    external_runner("DRUGSTUDIO_TEST_RUNNER", "missing/runner.sh"),
                    runner.resolve(),
                )


if __name__ == "__main__":
    unittest.main()

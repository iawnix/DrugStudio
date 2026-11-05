import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import venv
import drugstudio


class SourceLauncherRegression(unittest.TestCase):
    def test_source_launcher_works_without_installation_from_another_directory(self):
        launcher = Path(__file__).resolve().parents[1] / "run-agent.sh"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            venv.EnvBuilder(with_pip=False).create(root / "python")
            environment = dict(os.environ, DRUGSTUDIO_PYTHON=str(root / "python/bin/python"))
            environment.pop("PYTHONPATH", None)
            result = subprocess.run([str(launcher), "--help"], cwd=root, env=environment,
                                    text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("usage: drugstudio", result.stdout)

    def test_packaged_receptor_wrapper_uses_configured_environment_and_cwd(self):
        package = Path(drugstudio.__file__).parent
        self.assertTrue((package / "pbs/run_local_af3.pbs").is_file())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefix = root / "receptor"
            venv.EnvBuilder(with_pip=False).create(prefix)
            environment = dict(os.environ, DRUGSTUDIO_RECEPTOR_PREFIX=str(prefix), PYTHONPATH="/invalid")
            result = subprocess.run([
                "bash", str(package / "scripts/run_receptor_env.sh"), "python", "-c",
                "import os, sys; print(sys.prefix); print(os.getcwd()); print(os.environ.get('PYTHONPATH', 'unset'))",
            ], cwd=root, env=environment, text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines(), [str(prefix), str(root), "unset"])

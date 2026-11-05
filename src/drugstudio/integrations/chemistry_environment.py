"""Activate managed chemistry, then restore run-local cache and temp paths."""

from pathlib import Path

from .tool_paths import external_runner


CHEMISTRY_RUNNER = external_runner(
    "DOCK_AGENT_CHEMISTRY_RUNNER", ".agents/skills/rdkit/scripts/run_in_env.sh")


def chemistry_command(script: Path, arguments: list[str], cwd: Path,
                      runner: Path = CHEMISTRY_RUNNER) -> list[str]:
    folders = {"TMPDIR": "tmp", "XDG_CACHE_HOME": "cache", "XDG_CONFIG_HOME": "config",
               "XDG_DATA_HOME": "data", "MPLCONFIGDIR": "cache/matplotlib",
               "NUMBA_CACHE_DIR": "cache/numba"}
    assignments = []
    for variable, relative in folders.items():
        path = (cwd / relative).resolve()
        path.mkdir(parents=True, exist_ok=True)
        assignments.append(f"{variable}={path}")
    return [str(runner.resolve()), "env", *assignments, "PYTHONDONTWRITEBYTECODE=1",
            "python", str(script.resolve()), *arguments]

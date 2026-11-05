"""The one description of the run's tunable scientific parameters.

The CLI, the web API and the browser form all read this, so a parameter is
declared once rather than being spelled out as an argparse flag, a
validation branch and a form field that drift apart.

Editable values live in a run-local parameter-settings.json sidecar. Executed
configurations are immutable, content-addressed runtime_configuration artifacts.
Legacy inputs/runtime.json snapshots remain readable and are never overwritten.

Box centre and size are deliberately absent. They are a human review
decision recorded through ``overrides``, not a dial.
"""

from __future__ import annotations

import json
import math
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

RUNTIME_RELATIVE = "inputs/runtime.json"  # legacy immutable snapshot
SETTINGS_RELATIVE = "parameter-settings.json"


@dataclass(frozen=True)
class Parameter:
    key: str
    stage: str
    label: str
    kind: str                      # int | float | bool | choice | text | list
    default: Any
    help: str = ""
    unit: str = ""
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    choices: tuple[str, ...] = ()
    # Changing a parameter only matters from the stage that consumes it
    # onwards; the UI uses this to say what a change will invalidate.
    coerce: Optional[Callable[[Any], Any]] = field(default=None, repr=False)

    def as_json(self) -> dict:
        return {"key": self.key, "stage": self.stage, "label": self.label, "kind": self.kind,
                "default": self.default, "help": self.help, "unit": self.unit,
                "minimum": self.minimum, "maximum": self.maximum,
                "choices": list(self.choices)}


PARAMETERS: tuple[Parameter, ...] = (
    Parameter("receptor_ph", "receptor", "pH", "float", 7.4,
              help="PDBFixer 加氢时使用的 pH", minimum=0.0, maximum=14.0),
    Parameter("keep_receptor_water", "receptor", "保留结晶水", "bool", False,
              help="保留原始结构中的水分子"),
    Parameter("add_missing_residues", "receptor", "补全缺失残基", "bool", False,
              help="补上 PDBFixer 发现的序列缺口；补全结果需要人工审核"),
    Parameter("keep_receptor_residue", "receptor", "保留的残基", "list", [],
              help="需要保留的金属或辅因子残基名，每行一个，例如 ZN"),

    Parameter("optimize_xtb", "ligand", "xTB 构象优化", "bool", False,
              help="用 xTB 优化构象；更慢，且不校验化学一致性"),

    Parameter("p2rank_profile", "pocket", "P2Rank 配置", "choice", "auto",
              help="auto 会对预测结构自动选用 AlphaFold 配置",
              choices=("auto", "default", "alphafold")),
    Parameter("p2rank_box_size", "pocket", "候选盒边长", "float", 24.0, unit="Å",
              help="由口袋中心生成对接盒时的立方体边长", minimum=4.0, maximum=80.0),
    Parameter("p2rank_executable", "pocket", "prank 可执行文件", "text", None,
              help="留空则使用 DOCK_AGENT_P2RANK 或工作区中的安装"),

    Parameter("exhaustiveness", "docking", "搜索彻底度", "int", 8,
              help="Vina 的 exhaustiveness；越大越慢，结果越稳定", minimum=1, maximum=512),
    Parameter("workers", "docking", "并行任务数", "int", 1,
              help="同时运行的对接任务数", minimum=1, maximum=256),
    Parameter("allocated_cpus", "docking", "每任务 CPU 数", "int", 1,
              help="分配给单个对接任务的 CPU 核数", minimum=1, maximum=256),
    Parameter("seed", "ligand", "随机种子", "int", 20261016,
              help="对接与构象生成的随机种子；复现结果时保持不变", minimum=1, maximum=2147483647),
)

BY_KEY = {parameter.key: parameter for parameter in PARAMETERS}

# The stage each parameter is consumed by, in pipeline order, so a change can
# be reported as "this invalidates docking onwards".
STAGE_ORDER = ("structure", "receptor", "ligand", "pocket", "docking", "assessment")


def defaults() -> dict:
    return {parameter.key: parameter.default for parameter in PARAMETERS}


def _coerce(parameter: Parameter, value: Any) -> Any:
    if parameter.kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            text = value.strip().lower()
            if text in {"1", "true", "yes", "on"}:
                return True
            if text in {"0", "false", "no", "off"}:
                return False
        raise ValueError("需要布尔值")
    if parameter.kind in {"int", "float"}:
        if isinstance(value, bool):
            raise ValueError("需要数值")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("需要有限数值")
        if parameter.kind == "int":
            if not number.is_integer():
                raise ValueError("需要整数")
            return int(number)
        return number
    if parameter.kind == "choice":
        text = str(value)
        if text not in parameter.choices:
            raise ValueError(f"{parameter.label}：只能是 {', '.join(parameter.choices)}")
        return text
    if parameter.kind == "list":
        if value in (None, ""):
            return []
        if isinstance(value, str):
            items = [line.strip() for line in value.replace(",", "\n").splitlines()]
        elif isinstance(value, list):
            items = [str(item).strip() for item in value]
        else:
            raise ValueError("需要列表或逐行文本")
        cleaned = [item for item in items if item]
        if len(cleaned) > 64:
            raise ValueError(f"{parameter.label}：最多 64 项")
        if any(len(item) > 16 for item in cleaned):
            raise ValueError(f"{parameter.label}：单项过长")
        return cleaned
    if value in (None, ""):
        return None
    text = str(value).strip()
    if len(text) > 512:
        raise ValueError(f"{parameter.label}：内容过长")
    return text or None


def validate(values: dict) -> dict:
    """Coerce and range-check a partial update, returning a full value set."""
    merged = defaults()
    for key, value in (values or {}).items():
        parameter = BY_KEY.get(key)
        if parameter is None:
            # Unknown keys are dropped rather than rejected, so an older
            # saved file does not block a run after a parameter is removed.
            continue
        try:
            coerced = _coerce(parameter, value)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError(f"{parameter.label}：{error}" if "：" not in str(error) else str(error)) from error
        if parameter.minimum is not None and coerced is not None and coerced < parameter.minimum:
            raise ValueError(f"{parameter.label}：不能小于 {parameter.minimum}")
        if parameter.maximum is not None and coerced is not None and coerced > parameter.maximum:
            raise ValueError(f"{parameter.label}：不能大于 {parameter.maximum}")
        merged[key] = coerced
    return merged


def runtime_settings(run_root: Path) -> dict:
    """Read the latest executed configuration, including manual CLI box values."""
    path = run_root / RUNTIME_RELATIVE
    try:
        manifest = json.loads((run_root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = {}
    snapshots = [item for item in manifest.get("artifacts", {}).get("inputs", [])
                 if item.get("kind") == "runtime_configuration"]
    if snapshots:
        path = (run_root / snapshots[-1]["path"]).resolve()
        path.relative_to(run_root.resolve())
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def load(run_root: Path) -> dict:
    """Overlay editable settings on the executed snapshot; validate each value."""
    saved = runtime_settings(run_root)
    try:
        edits = json.loads((run_root / SETTINGS_RELATIVE).read_text(encoding="utf-8"))
        if isinstance(edits, dict):
            saved.update(edits)
    except (OSError, json.JSONDecodeError):
        pass
    merged = defaults()
    for key, value in saved.items():
        if key in BY_KEY:
            try:
                merged[key] = validate({key: value})[key]
            except (TypeError, ValueError, OverflowError):
                continue
    return merged


def required_rerun(run_root: Path, values: dict, stage_results: dict) -> Optional[str]:
    """Do not apply new parameters to already-computed downstream artifacts."""
    previous = {**defaults(), **runtime_settings(run_root)}
    changed = [key for key in BY_KEY if key in values and values[key] != previous.get(key)]
    earliest = earliest_stage(changed)
    if earliest is None:
        return None
    affected = set(STAGE_ORDER[STAGE_ORDER.index(earliest):])
    if any(key.split(":", 1)[0] in affected and
           result.get("status") not in {None, "pending", "stale"}
           for key, result in stage_results.items()):
        return earliest
    return None


def record_runtime(store, state, settings: dict):
    """Publish an immutable snapshot and make it the latest manifest reference."""
    digest = hashlib.sha256(json.dumps(settings, sort_keys=True, allow_nan=False).encode()).hexdigest()
    artifact = store.publish_json(relative_path=f"inputs/runtime-{digest}.json",
                                  value=settings, kind="runtime_configuration")
    inputs = state.artifacts.setdefault("inputs", [])
    # Reverting to an older configuration must still select it as the latest.
    inputs[:] = [item for item in inputs if item.path != artifact.path]
    state.add_artifact("inputs", artifact)
    store.save_manifest(state)
    # Once applied, future resumes use this snapshot. Otherwise an old UI
    # draft could silently undo an explicit CLI override on the next resume.
    store.path(SETTINGS_RELATIVE).unlink(missing_ok=True)
    return artifact


def changed_from_default(values: dict) -> dict:
    return {key: value for key, value in values.items()
            if key in BY_KEY and value != BY_KEY[key].default}


def earliest_stage(keys) -> Optional[str]:
    """The first pipeline stage affected by a set of changed parameters."""
    stages = [BY_KEY[key].stage for key in keys if key in BY_KEY]
    if not stages:
        return None
    return min(stages, key=lambda stage: STAGE_ORDER.index(stage))


def to_cli_args(values: dict) -> list[str]:
    """Render values as `drugstudio run` flags.

    The web server starts runs as a subprocess, so passing the saved values
    explicitly is what keeps a resumed run on the parameters it was
    configured with instead of the argparse defaults.
    """
    args: list[str] = []
    for parameter in PARAMETERS:
        value = values.get(parameter.key, parameter.default)
        flag = "--" + parameter.key.replace("_", "-")
        if parameter.kind == "bool":
            if value:
                args.append(flag)
        elif parameter.kind == "list":
            for item in value or []:
                args.extend([flag, str(item)])
        elif value is None:
            continue
        else:
            args.extend([flag, str(value)])
    return args


__all__ = ["PARAMETERS", "BY_KEY", "STAGE_ORDER", "RUNTIME_RELATIVE", "SETTINGS_RELATIVE", "Parameter",
           "runtime_settings", "record_runtime", "required_rerun",
           "changed_from_default", "defaults", "earliest_stage", "load", "to_cli_args", "validate"]

from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.requirements import Requirement


def test_flet_build_declares_certifi_for_runner_bootstrap() -> None:
    """The native Flet runner imports certifi before executing the app module."""
    project_path = Path(__file__).parents[1] / "pyproject.toml"
    project = tomllib.loads(project_path.read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]

    names = {Requirement(dependency).name for dependency in dependencies}

    assert "certifi" in names

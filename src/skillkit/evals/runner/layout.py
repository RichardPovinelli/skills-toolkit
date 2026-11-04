"""Per-collection locations the evaluation runner reads and writes.

The runner is distributed separately from the skill collections it grades, so
no path may be derived from the tool's own source location. A collection
supplies a :class:`CollectionLayout`; :meth:`CollectionLayout.from_root` applies
the conventional defaults and accepts explicit overrides.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SKILLS_DIRNAME = "skills"
DATA_DIRNAME = "validation"
RUNS_DIRNAME = "validation-runs"
REPORTS_DIRNAME = "reports"


@dataclass(frozen=True)
class CollectionLayout:
    """Where one graded collection keeps its skills, data, and artifacts."""

    root: Path
    skills_dir: Path
    data_root: Path
    cases_dir: Path
    state_dir: Path
    rubric_path: Path
    runs_dir: Path
    reports_dir: Path
    protected_dir_names: frozenset[str] = frozenset({"validation"})

    @classmethod
    def from_root(
        cls,
        root: Path,
        *,
        skills_dir: Path | None = None,
        data_root: Path | None = None,
        cases_dir: Path | None = None,
        state_dir: Path | None = None,
        rubric_path: Path | None = None,
        runs_dir: Path | None = None,
        reports_dir: Path | None = None,
    ) -> "CollectionLayout":
        """Resolve a collection layout from the collection root."""
        root = Path(root)
        resolved_data_root = Path(data_root or root / DATA_DIRNAME)
        resolved_skills = Path(skills_dir or root / SKILLS_DIRNAME)
        return cls(
            root=root,
            skills_dir=resolved_skills,
            data_root=resolved_data_root,
            cases_dir=Path(cases_dir or resolved_data_root / "cases"),
            state_dir=Path(state_dir or resolved_data_root / "state"),
            rubric_path=Path(rubric_path or resolved_data_root / "rubric.md"),
            runs_dir=Path(runs_dir or root / RUNS_DIRNAME),
            reports_dir=Path(reports_dir or root / REPORTS_DIRNAME),
            # The graded data directory is the one directory name whose mention
            # in agent output is treated as a leak.
            protected_dir_names=frozenset({resolved_data_root.name}),
        )

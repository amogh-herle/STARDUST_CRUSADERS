"""
Load Phase 8 analytics artifacts from disk.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


class AnalyticsLoader:
    def __init__(self, root: Path):
        self.root = Path(root)

    def csv_path(self, name: str) -> Path:
        return self.root / name

    def load_csv(self, name: str, dtype: dict | None = None) -> pd.DataFrame:
        path = self.csv_path(name)
        if not path.exists():
            return pd.DataFrame()
        return pd.read_csv(path, dtype=dtype)

    def load_json(self, name: str) -> Any:
        path = self.root / name
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def load_text(self, name: str) -> str:
        path = self.root / name
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def list_money_trail_files(self) -> list[Path]:
        trails_dir = self.root / "money_trails"
        if not trails_dir.exists():
            return []
        return sorted(trails_dir.glob("trail_*.csv"))

    def load_money_trail(self, account_id: str, direction: str | None = None) -> dict[str, Any]:
        trail_dir = self.root / "money_trails"
        if not trail_dir.exists():
            return {"files": []}

        files = []
        for trail_file in sorted(trail_dir.glob(f"trail_{account_id}_*.csv")):
            if direction and f"_{direction}.csv" not in trail_file.name:
                continue
            df = pd.read_csv(trail_file, dtype=str)
            files.append({
                "path": str(trail_file.name),
                "direction": direction or ("forward" if "forward" in trail_file.name else "backward"),
                "rows": len(df),
                "columns": df.columns.tolist(),
                "preview": df.head(5).to_dict(orient="records"),
            })

        return {"files": files}

    def load_all_money_trails(self) -> dict[str, Any]:
        trail_dir = self.root / "money_trails"
        if not trail_dir.exists():
            return {"files": []}

        files = []
        for trail_file in sorted(trail_dir.glob("trail_*.csv")):
            df = pd.read_csv(trail_file, dtype=str)
            files.append({
                "path": str(trail_file.name),
                "direction": "forward" if "forward" in trail_file.name else "backward",
                "rows": len(df),
                "columns": df.columns.tolist(),
                "preview": df.head(5).to_dict(orient="records"),
            })
        return {"files": files}

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def export_file(path: Path, metadata: dict[str, Any], rows: list[dict], overwrite: bool = False):
    if path.suffix.lower() not in (".csv", ".json"):
        raise ValueError("Choose a .csv or .json filename")
    mode = "w" if overwrite else "x"
    with path.expanduser().open(mode, newline="", encoding="utf-8") as handle:
        if path.suffix.lower() == ".json":
            json.dump({"metadata": metadata, "rows": rows}, handle, indent=2, ensure_ascii=False)
        else:
            merged = [{**metadata, **row} for row in rows]
            fields = list(dict.fromkeys(k for row in merged for k in row)) or list(metadata)
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in merged:
                # App names and labels are external input; keep spreadsheets from evaluating them.
                row = {
                    k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                    for k, v in row.items()
                }
                writer.writerow(
                    {
                        k: "'" + v
                        if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@"))
                        else v
                        for k, v in row.items()
                    }
                )

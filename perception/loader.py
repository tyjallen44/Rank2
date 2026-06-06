from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from .models import Entity, EntityType

# Required columns and their accepted aliases (all matched case-insensitively)
_COLUMN_ALIASES: dict[str, list[str]] = {
    "entity_type": ["entity_type", "type", "kind"],
    "name":        ["name", "provider_name", "practice_name", "hospital_name", "doctor_name"],
    "city":        ["city"],
    "state":       ["state", "st"],
    "zip":         ["zip", "zip_code", "postal_code"],
    "address":     ["address", "street", "street_address"],
    "npi":         ["npi", "npi_number"],
}

_REQUIRED = {"entity_type", "name", "city", "state"}


def load(path: str | Path) -> list[Entity]:
    """Load entities from a CSV or Excel spreadsheet."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Spreadsheet not found: {path}")

    if path.suffix.lower() in {".xlsx", ".xls"}:
        df = pd.read_excel(path, dtype=str)
    elif path.suffix.lower() == ".csv":
        df = pd.read_csv(path, dtype=str)
    else:
        raise ValueError(f"Unsupported file type: {path.suffix} (use .xlsx, .xls, or .csv)")

    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    col_map = _resolve_columns(df.columns.tolist())
    _check_required(col_map)

    entities: list[Entity] = []
    for i, row in df.iterrows():
        raw_type = _cell(row, col_map, "entity_type")
        if not raw_type:
            continue
        try:
            entity_type = EntityType(raw_type.lower().strip())
        except ValueError:
            raise ValueError(
                f"Row {i + 2}: unknown entity_type '{raw_type}'. "
                f"Must be one of: {', '.join(e.value for e in EntityType)}"
            )

        name = _cell(row, col_map, "name") or ""
        city = _cell(row, col_map, "city") or ""
        state = _cell(row, col_map, "state") or ""

        entity_id = _make_id(entity_type.value, name, city, state)

        entities.append(Entity(
            id=entity_id,
            entity_type=entity_type,
            name=name.strip(),
            city=city.strip(),
            state=state.upper().strip(),
            zip=_cell(row, col_map, "zip"),
            address=_cell(row, col_map, "address"),
            npi=_cell(row, col_map, "npi"),
        ))

    return entities


def _resolve_columns(columns: list[str]) -> dict[str, str]:
    """Map canonical field names to actual column names found in the file."""
    result: dict[str, str] = {}
    for field, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in columns:
                result[field] = alias
                break
    return result


def _check_required(col_map: dict[str, str]) -> None:
    missing = _REQUIRED - col_map.keys()
    if missing:
        raise ValueError(
            f"Spreadsheet is missing required column(s): {', '.join(sorted(missing))}. "
            f"See entities_template.csv for the expected format."
        )


def _cell(row: pd.Series, col_map: dict[str, str], field: str) -> str | None:
    col = col_map.get(field)
    if col is None:
        return None
    val = row.get(col)
    if pd.isna(val) or str(val).strip() == "":
        return None
    return str(val).strip()


def _make_id(entity_type: str, name: str, city: str, state: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", f"{entity_type}-{name}-{city}-{state}".lower()).strip("-")
    return slug

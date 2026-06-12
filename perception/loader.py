from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from .models import Entity, EntityType

# Required columns and their accepted aliases (all matched case-insensitively)
_COLUMN_ALIASES: dict[str, list[str]] = {
    "entity_type": ["entity_type", "type", "kind"],
    "name":        ["name", "account_name", "provider_name", "practice_name", "hospital_name", "doctor_name"],
    "city":        ["city"],
    "state":       ["state", "st"],
    "location":    ["main_location_city", "location", "city_state", "city,_state"],
    "zip":         ["zip", "zip_code", "postal_code"],
    "address":     ["address", "street", "street_address"],
    "npi":         ["npi", "npi_number"],
    "specialty":   ["specialty", "speciality", "service_line"],
}

# location is the only hard requirement (city+state or combined); everything else is optional
_REQUIRED: set[str] = set()

_STATE_ABBREVS: dict[str, str] = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}


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
        city = _cell(row, col_map, "city") or ""
        state = _cell(row, col_map, "state") or ""
        if not city:
            loc = _cell(row, col_map, "location") or ""
            city, state = _parse_location(loc) if loc else (city, state)

        if not city:
            continue  # no location — skip

        name = _cell(row, col_map, "name") or ""

        raw_type = _cell(row, col_map, "entity_type")
        specialty_val = _cell(row, col_map, "specialty")

        if raw_type:
            raw_type_lower = raw_type.lower().strip()
            try:
                entity_type = EntityType(raw_type_lower)
            except ValueError:
                # Unknown entity_type — treat it as a specialty indicator (e.g. "orthopedic")
                if not specialty_val:
                    specialty_val = raw_type.strip()
                entity_type = EntityType("practice") if specialty_val else EntityType("hospital")
        else:
            entity_type = EntityType("doctor") if _cell(row, col_map, "npi") else (
                EntityType("practice") if specialty_val else EntityType("hospital")
            )

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
            specialty=specialty_val,
        ))

    return entities


def _resolve_columns(columns: list[str]) -> dict[str, str]:
    """Map canonical field names to actual column names found in the file."""
    result: dict[str, str] = {}
    for field, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            # exact match first, then prefix match (handles "speciality_(blank_for_hospitals)" etc.)
            if alias in columns:
                result[field] = alias
                break
            for col in columns:
                if col.startswith(alias):
                    result[field] = col
                    break
            if field in result:
                break
    return result


def _check_required(col_map: dict[str, str]) -> None:
    missing = _REQUIRED - col_map.keys()
    if missing:
        raise ValueError(
            f"Spreadsheet is missing required column(s): {', '.join(sorted(missing))}. "
            f"See entities_template.csv for the expected format."
        )
    has_location = "location" in col_map or ("city" in col_map and "state" in col_map)
    if not has_location:
        raise ValueError(
            "Spreadsheet must have either a 'Main Location City' column (e.g. 'Mobile, Alabama') "
            "or separate 'city' and 'state' columns."
        )


def _parse_location(val: str) -> tuple[str, str]:
    """Split 'City, State' into (city, state_abbrev). State can be full name or 2-letter code."""
    parts = [p.strip() for p in val.split(",", 1)]
    if len(parts) == 2:
        city, state_raw = parts
        state_key = state_raw.lower()
        state = _STATE_ABBREVS.get(state_key, state_raw.upper())
        return city, state
    return val, ""


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

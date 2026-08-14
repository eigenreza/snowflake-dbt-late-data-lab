"""What both loaders share: the RAW layout, file discovery and the load audit record.

RAW keeps every file's rows exactly as delivered (all columns as text) plus
three load columns, so that staging can type, clean and deduplicate them,
and so that any row can be traced back to its file.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from common import legacy_format as legacy

RAW_SCHEMA = "raw"
AUDIT_TABLE = "load_audit"
FX_TABLE = "fx_rates"
FX_COLUMNS = ("rate_date", "currency", "rate_nok")
LOAD_COLUMNS = ("_file_name", "_extract_day", "_loaded_at")

ENTITY_BY_PREFIX = {prefix: entity for entity, prefix in legacy.FILE_PREFIX.items()}


def raw_table(entity: str) -> str:
    return entity


def raw_columns(entity: str) -> list[str]:
    """Legacy column names, in file order, followed by the load columns."""
    return [c.legacy for c in legacy.LAYOUTS[entity]] + list(LOAD_COLUMNS)


def raw_table_ddl(entity: str, schema: str = RAW_SCHEMA) -> str:
    cols = ",\n    ".join(f"{c} VARCHAR" for c in (col.legacy for col in legacy.LAYOUTS[entity]))
    return (
        f"CREATE TABLE IF NOT EXISTS {schema}.{raw_table(entity)} (\n    {cols},\n"
        f"    _file_name VARCHAR NOT NULL,\n    _extract_day DATE NOT NULL,\n    _loaded_at TIMESTAMP NOT NULL\n)"
    )


def fx_table_ddl(schema: str = RAW_SCHEMA) -> str:
    return (
        f"CREATE TABLE IF NOT EXISTS {schema}.{FX_TABLE} (\n"
        "    rate_date VARCHAR,\n    currency VARCHAR,\n    rate_nok VARCHAR,\n"
        "    _file_name VARCHAR NOT NULL,\n    _extract_day DATE NOT NULL,\n    _loaded_at TIMESTAMP NOT NULL\n)"
    )


def audit_table_ddl(schema: str = RAW_SCHEMA) -> str:
    return (
        f"CREATE TABLE IF NOT EXISTS {schema}.{AUDIT_TABLE} (\n"
        "    file_name      VARCHAR NOT NULL,\n"
        "    entity         VARCHAR NOT NULL,\n"
        "    extract_day    DATE NOT NULL,\n"
        "    file_hash      VARCHAR NOT NULL,\n"
        "    rows_in_file   INTEGER,\n"
        "    rows_loaded    INTEGER NOT NULL,\n"
        "    rows_rejected  INTEGER NOT NULL,\n"
        "    status         VARCHAR NOT NULL,\n"
        "    error_message  VARCHAR,\n"
        "    loaded_at      TIMESTAMP NOT NULL\n"
        ")"
    )


@dataclass(frozen=True)
class LandedFile:
    path: Path
    entity: str
    extract_day: date

    @property
    def name(self) -> str:
        return self.path.name


@dataclass
class LoadRecord:
    file_name: str
    entity: str
    extract_day: date
    file_hash: str
    rows_in_file: int | None
    rows_loaded: int
    rows_rejected: int
    status: str
    error_message: str | None
    loaded_at: datetime

    def as_row(self) -> list:
        return [
            self.file_name, self.entity, self.extract_day, self.file_hash, self.rows_in_file,
            self.rows_loaded, self.rows_rejected, self.status, self.error_message, self.loaded_at,
        ]


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def trailer_count(path: Path) -> int | None:
    """Row count declared by the trailer line, or None when the file has no trailer."""
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - 64))
        tail = handle.read().decode(legacy.ENCODING)
    last = tail.rstrip("\r\n").splitlines()[-1] if tail.strip() else ""
    if last.startswith(legacy.TRAILER_PREFIX + legacy.DELIMITER):
        return int(last.split(legacy.DELIMITER)[1])
    return None


def discover(landing_dir: Path, day: date | None = None) -> list[LandedFile]:
    """Landed legacy files in day order, optionally for one day only."""
    found: list[LandedFile] = []
    if not landing_dir.exists():
        return found
    for folder in sorted(p for p in landing_dir.iterdir() if p.is_dir()):
        try:
            folder_day = datetime.strptime(folder.name, "%Y%m%d").date()
        except ValueError:
            continue
        if day is not None and folder_day != day:
            continue
        for path in sorted(folder.glob("*.csv")):
            prefix = path.name.split("_")[0]
            entity = ENTITY_BY_PREFIX.get(prefix)
            if entity is not None:
                found.append(LandedFile(path, entity, folder_day))
    return found

"""The legacy core-system extract format.

The old extract job wrote one file per entity and day: semicolon separated,
decimal comma, DDMMYYYY dates, Latin-1 encoding, CRLF line endings, a header
line, and a trailer line with the row count. Text values that operators typed
by hand (status codes, transaction types, region names) vary in case from one
line to the next, and a small share of lines is written twice.

This module holds the column layouts, the writer used by the extract job, and
a reader used by the tests.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

ENCODING = "latin-1"
DELIMITER = ";"
LINE_ENDING = "\r\n"
DATE_FORMAT = "%d%m%Y"
TIMESTAMP_FORMAT = "%d%m%Y %H:%M:%S"
TRAILER_PREFIX = "TRAILER"

Kind = Literal["text", "int", "decimal", "date", "timestamp"]


@dataclass(frozen=True)
class Column:
    source: str
    legacy: str
    kind: Kind
    noisy_case: bool = False


LAYOUTS: dict[str, tuple[Column, ...]] = {
    "customers": (
        Column("customer_id", "CUST_ID", "int"),
        Column("customer_no", "CUST_NO", "text"),
        Column("first_name", "FIRST_NM", "text"),
        Column("last_name", "LAST_NM", "text"),
        Column("birth_date", "BIRTH_DT", "date"),
        Column("segment", "SEGM", "text", noisy_case=True),
        Column("region", "REGION", "text", noisy_case=True),
        Column("street", "STREET", "text"),
        Column("postal_code", "POST_CD", "text"),
        Column("city", "CITY", "text", noisy_case=True),
        Column("customer_since", "CUST_SINCE", "date"),
        Column("created_at", "CRT_TS", "timestamp"),
        Column("updated_at", "UPD_TS", "timestamp"),
    ),
    "accounts": (
        Column("account_no", "ACCT_NO", "text"),
        Column("customer_id", "CUST_ID", "int"),
        Column("account_type", "ACCT_TYP", "text", noisy_case=True),
        Column("open_date", "OPEN_DT", "date"),
        Column("close_date", "CLOSE_DT", "date"),
        Column("status", "STAT", "text", noisy_case=True),
        Column("opening_balance", "OPEN_BAL", "decimal"),
        Column("credit_limit", "CR_LIM", "decimal"),
        Column("created_at", "CRT_TS", "timestamp"),
        Column("updated_at", "UPD_TS", "timestamp"),
    ),
    "cards": (
        Column("card_id", "CARD_ID", "text"),
        Column("account_no", "ACCT_NO", "text"),
        Column("masked_pan", "PAN_MASK", "text"),
        Column("issued_date", "ISSUE_DT", "date"),
        Column("expiry_date", "EXP_DT", "date"),
        Column("status", "STAT", "text", noisy_case=True),
        Column("created_at", "CRT_TS", "timestamp"),
        Column("updated_at", "UPD_TS", "timestamp"),
    ),
    "merchants": (
        Column("merchant_id", "MERCH_ID", "int"),
        Column("merchant_name", "MERCH_NM", "text", noisy_case=True),
        Column("category_code", "CAT_CD", "text"),
        Column("category_name", "CAT_NM", "text", noisy_case=True),
        Column("country_code", "CTRY", "text", noisy_case=True),
        Column("city", "CITY", "text"),
        Column("created_at", "CRT_TS", "timestamp"),
        Column("updated_at", "UPD_TS", "timestamp"),
    ),
    "transactions": (
        Column("txn_id", "TXN_ID", "int"),
        Column("account_no", "ACCT_NO", "text"),
        Column("card_id", "CARD_ID", "text"),
        Column("merchant_id", "MERCH_ID", "int"),
        Column("txn_type", "TXN_TYP", "text", noisy_case=True),
        Column("event_date", "BOOK_DT", "date"),
        Column("amount", "AMT", "decimal"),
        Column("currency", "CCY", "text", noisy_case=True),
        Column("status", "STAT", "text", noisy_case=True),
        Column("reversal_of", "REV_OF", "int"),
        Column("created_at", "CRT_TS", "timestamp"),
        Column("updated_at", "UPD_TS", "timestamp"),
    ),
}

FILE_PREFIX = {
    "customers": "CUST",
    "accounts": "ACCT",
    "cards": "CARD",
    "merchants": "MERCH",
    "transactions": "TXN",
}

ENTITIES = tuple(LAYOUTS)


def file_name(entity: str, day: date) -> str:
    """Name of the legacy file for an entity and extract day, for example TXN_01122024.csv."""
    return f"{FILE_PREFIX[entity]}_{day.strftime(DATE_FORMAT)}.csv"


def format_decimal(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")


def parse_decimal(text: str) -> float:
    return float(text.replace(",", "."))


def parse_date(text: str) -> date:
    return datetime.strptime(text, DATE_FORMAT).date()


def parse_timestamp(text: str) -> datetime:
    return datetime.strptime(text, TIMESTAMP_FORMAT)


def _format_column(values: pd.Series, kind: Kind) -> pd.Series:
    missing = values.isna()
    if kind == "int":
        out = values.map(lambda v: "" if pd.isna(v) else str(int(v)))
    elif kind == "decimal":
        out = values.map(lambda v: "" if pd.isna(v) else format_decimal(float(v)))
    elif kind == "date":
        out = pd.to_datetime(values).dt.strftime(DATE_FORMAT)
    elif kind == "timestamp":
        out = pd.to_datetime(values).dt.strftime(TIMESTAMP_FORMAT)
    else:
        out = values.astype("string")
    out = out.astype("string").where(~missing, "")
    return out.astype(object)


def format_frame(frame: pd.DataFrame, entity: str) -> pd.DataFrame:
    """Render a core-system frame as legacy text columns, in legacy column order."""
    layout = LAYOUTS[entity]
    out = pd.DataFrame(index=frame.index)
    for col in layout:
        out[col.legacy] = _format_column(frame[col.source], col.kind)
    return out


def _vary_case(values: pd.Series, rng: np.random.Generator) -> pd.Series:
    """Rewrite each value in upper, lower or title case, the way the old job did."""
    choice = rng.random(len(values))
    upper = values.str.upper()
    lower = values.str.lower()
    title = values.str.title()
    out = values.copy()
    out[choice < 0.35] = upper[choice < 0.35]
    out[(choice >= 0.35) & (choice < 0.55)] = lower[(choice >= 0.35) & (choice < 0.55)]
    out[(choice >= 0.55) & (choice < 0.65)] = title[(choice >= 0.55) & (choice < 0.65)]
    return out


def add_noise(formatted: pd.DataFrame, entity: str, seed: int, duplicate_rate: float) -> pd.DataFrame:
    """Apply the legacy job's quirks deterministically for a given seed.

    Columns marked ``noisy_case`` vary in case from line to line, and a small
    share of lines is appended again at the end of the file as duplicates.
    """
    rng = np.random.default_rng(seed)
    out = formatted.copy()
    for col in LAYOUTS[entity]:
        if col.noisy_case and len(out):
            out[col.legacy] = _vary_case(out[col.legacy].astype("string"), rng).astype(object)
    if duplicate_rate > 0 and len(out):
        dup_mask = rng.random(len(out)) < duplicate_rate
        if dup_mask.any():
            out = pd.concat([out, out[dup_mask]], ignore_index=True)
    return out


def render(formatted: pd.DataFrame) -> bytes:
    """Serialize a formatted frame with header and trailer in the legacy encoding."""
    buffer = io.StringIO()
    formatted.to_csv(
        buffer,
        sep=DELIMITER,
        index=False,
        lineterminator=LINE_ENDING,
        quoting=csv.QUOTE_MINIMAL,
    )
    buffer.write(f"{TRAILER_PREFIX}{DELIMITER}{len(formatted)}{LINE_ENDING}")
    return buffer.getvalue().encode(ENCODING)


def write_legacy_file(path: Path, formatted: pd.DataFrame) -> int:
    """Write the file through a temporary name so a partial file is never left behind."""
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(render(formatted))
    tmp.replace(path)
    return len(formatted)


def read_legacy_file(path: Path) -> pd.DataFrame:
    """Read a legacy file back as text columns, checking the trailer count."""
    text = path.read_bytes().decode(ENCODING)
    lines = text.split(LINE_ENDING)
    if lines and lines[-1] == "":
        lines = lines[:-1]
    if not lines or not lines[-1].startswith(TRAILER_PREFIX + DELIMITER):
        raise ValueError(f"{path.name}: missing trailer line")
    declared = int(lines[-1].split(DELIMITER)[1])
    body = LINE_ENDING.join(lines[:-1]) + LINE_ENDING
    frame = pd.read_csv(
        io.StringIO(body),
        sep=DELIMITER,
        dtype=str,
        keep_default_na=False,
        lineterminator="\n",
    )
    frame = frame.map(lambda v: v.rstrip("\r") if isinstance(v, str) else v)
    frame.columns = [c.rstrip("\r") for c in frame.columns]
    if len(frame) != declared:
        raise ValueError(f"{path.name}: trailer says {declared} rows, file has {len(frame)}")
    return frame

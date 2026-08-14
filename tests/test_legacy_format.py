"""The legacy file format survives a round trip and its noise is reproducible."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from common import legacy_format as legacy


def _sample_transactions() -> pd.DataFrame:
    return pd.DataFrame(
        dict(
            txn_id=[10000001, 10000002],
            account_no=["99990000001", "99990000002"],
            card_id=["K00000001", None],
            merchant_id=pd.array([1001, None], dtype="Int64"),
            txn_type=["CARD_PURCHASE", "TRANSFER_IN"],
            event_date=[date(2024, 12, 1), date(2024, 11, 28)],
            amount=[-1234.5, 100.0],
            currency=["NOK", "NOK"],
            status=["POSTED", "POSTED"],
            reversal_of=pd.array([None, None], dtype="Int64"),
            created_at=[datetime(2024, 12, 1, 9, 5, 7), datetime(2024, 12, 2, 10, 0, 0)],
            updated_at=[datetime(2024, 12, 1, 9, 5, 7), datetime(2024, 12, 2, 10, 0, 0)],
        )
    )


def test_round_trip(tmp_path):
    formatted = legacy.format_frame(_sample_transactions(), "transactions")
    assert formatted.loc[0, "AMT"] == "-1234,50"
    assert formatted.loc[0, "BOOK_DT"] == "01122024"
    assert formatted.loc[1, "CARD_ID"] == "" and formatted.loc[1, "MERCH_ID"] == ""
    assert formatted.loc[1, "CRT_TS"] == "02122024 10:00:00"

    path = tmp_path / legacy.file_name("transactions", date(2024, 12, 1))
    assert path.name == "TXN_01122024.csv"
    legacy.write_legacy_file(path, formatted)
    back = legacy.read_legacy_file(path)
    assert legacy.parse_decimal(back.loc[0, "AMT"]) == -1234.5
    assert legacy.parse_date(back.loc[1, "BOOK_DT"]) == date(2024, 11, 28)
    assert legacy.parse_timestamp(back.loc[0, "UPD_TS"]) == datetime(2024, 12, 1, 9, 5, 7)
    assert back.loc[1, "CARD_ID"] == ""


def test_latin1_and_line_endings(tmp_path):
    customers = pd.DataFrame(
        dict(
            customer_id=[1], customer_no=["C100001"], first_name=["Åse"], last_name=["Sæther"],
            birth_date=[date(1970, 5, 17)], segment=["Standard"], region=["Trøndelag"], street=["Øvre Torggate 3"],
            postal_code=["7010"], city=["Trondheim"], customer_since=[date(2015, 1, 2)],
            created_at=[datetime(2015, 1, 2, 9, 0, 0)], updated_at=[datetime(2015, 1, 2, 9, 0, 0)],
        )
    )
    path = tmp_path / "CUST_17052024.csv"
    legacy.write_legacy_file(path, legacy.format_frame(customers, "customers"))
    raw = path.read_bytes()
    assert b"\r\n" in raw and b"S\xe6ther" in raw and b"Tr\xf8ndelag" in raw
    assert raw.endswith(b"TRAILER;1\r\n")
    back = legacy.read_legacy_file(path)
    assert back.loc[0, "LAST_NM"] == "Sæther" and back.loc[0, "REGION"] == "Trøndelag"


def test_noise_is_deterministic_and_adds_duplicates():
    frame = pd.concat([_sample_transactions()] * 50, ignore_index=True)
    formatted = legacy.format_frame(frame, "transactions")
    a = legacy.add_noise(formatted, "transactions", seed=42, duplicate_rate=0.1)
    b = legacy.add_noise(formatted, "transactions", seed=42, duplicate_rate=0.1)
    pd.testing.assert_frame_equal(a, b)
    c = legacy.add_noise(formatted, "transactions", seed=43, duplicate_rate=0.1)
    assert not a.equals(c)
    assert len(a) > len(formatted)
    assert a["TXN_ID"].nunique() == 2
    assert {v for v in a["STAT"]} > {"POSTED"}
    assert (a["TXN_ID"] == a["TXN_ID"]).all()


def test_trailer_mismatch_is_detected(tmp_path):
    path = tmp_path / "TXN_01012024.csv"
    path.write_bytes(b"TXN_ID;AMT\r\n1;2,00\r\nTRAILER;5\r\n")
    try:
        legacy.read_legacy_file(path)
    except ValueError as exc:
        assert "trailer" in str(exc)
    else:
        raise AssertionError("mismatched trailer was accepted")

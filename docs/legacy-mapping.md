# Legacy extract to staging: column mapping

The core system's extract job writes one file per entity and day in the format the old
reporting platform expected. This page records that format and how every column is
read into the staging layer.

## File conventions

| Aspect | Legacy extract | Handled in |
|---|---|---|
| Files | `CUST_`, `ACCT_`, `CARD_`, `MERCH_`, `TXN_` + `DDMMYYYY.csv`, one folder per extract day (`YYYYMMDD`) | loader |
| Separator | semicolon | file format / `read_csv` |
| Encoding | Latin-1 (ISO 8859-1), so `ø`, `æ`, `å` are single bytes | file format / `read_csv` |
| Line ending | CRLF | file format / `read_csv` |
| Header | first line, legacy column names | file format / `read_csv` (skipped) |
| Trailer | last line `TRAILER;<row count>`; two fields instead of a full record | rejected by the load and counted in `raw.load_audit` (one rejected row per file) |
| Dates | `DDMMYYYY`, for example `01122024` | `parse_legacy_date` macro |
| Timestamps | `DDMMYYYY HH:MM:SS` | `parse_legacy_timestamp` macro |
| Decimals | decimal comma, two places, for example `-1234,50` | `parse_decimal_comma` macro |
| Nulls | empty field | `nullif(trim(x), '')` |
| Casing | status codes, types, currency, region, city, segment and merchant name vary in case from line to line | `clean_code` (upper) and `title_case` macros |
| Duplicates | a small share of lines is written twice | `qualify row_number() ... = 1` per business key and update time in every staging model |
| Versions | a record changed in the core system is delivered again with a later `UPD_TS` | kept in staging; the facts and current-state views pick the newest |

RAW keeps every column as text with three load columns: `_file_name`, `_extract_day`, `_loaded_at`.

## Customers (`CUST_*.csv` to `stg_customers`)

| Legacy | Staging | Type | Rule |
|---|---|---|---|
| `CUST_ID` | `customer_id` | integer | key |
| `CUST_NO` | `customer_no` | text | trimmed |
| `FIRST_NM` | `first_name` | text | trimmed |
| `LAST_NM` | `last_name` | text | trimmed |
| `BIRTH_DT` | `birth_date` | date | `DDMMYYYY` |
| `SEGM` | `segment` | text | title case (`Standard`, `Premium`, `Student`, `Private Banking`) |
| `REGION` | `region` | text | title case with small words lower (`Møre og Romsdal`); checked against the `regions` seed |
| `STREET` | `street` | text | trimmed |
| `POST_CD` | `postal_code` | text | trimmed; kept as text to preserve leading zeros |
| `CITY` | `city` | text | title case (`Mo i Rana`) |
| `CUST_SINCE` | `customer_since` | date | `DDMMYYYY` |
| `CRT_TS` | `created_at` | timestamp | `DDMMYYYY HH:MM:SS` |
| `UPD_TS` | `updated_at` | timestamp | `DDMMYYYY HH:MM:SS`; version key together with `CUST_ID` |

## Accounts (`ACCT_*.csv` to `stg_accounts`)

| Legacy | Staging | Type | Rule |
|---|---|---|---|
| `ACCT_NO` | `account_no` | text | key; eleven digits, kept as text |
| `CUST_ID` | `customer_id` | integer | |
| `ACCT_TYP` | `account_type` | text | upper (`CURRENT`, `SAVINGS`) |
| `OPEN_DT` | `open_date` | date | |
| `CLOSE_DT` | `close_date` | date | null while open |
| `STAT` | `status` | text | upper (`OPEN`, `CLOSED`) |
| `OPEN_BAL` | `opening_balance` | decimal(18,2) | decimal comma |
| `CR_LIM` | `credit_limit` | decimal(18,2) | decimal comma; overdraft limit, 0 when the account may not go below zero |
| `CRT_TS` | `created_at` | timestamp | |
| `UPD_TS` | `updated_at` | timestamp | version key with `ACCT_NO` |

## Cards (`CARD_*.csv` to `stg_cards`)

| Legacy | Staging | Type | Rule |
|---|---|---|---|
| `CARD_ID` | `card_id` | text | key |
| `ACCT_NO` | `account_no` | text | |
| `PAN_MASK` | `masked_pan` | text | masked card number as delivered |
| `ISSUE_DT` | `issued_date` | date | |
| `EXP_DT` | `expiry_date` | date | |
| `STAT` | `status` | text | upper |
| `CRT_TS` | `created_at` | timestamp | |
| `UPD_TS` | `updated_at` | timestamp | version key with `CARD_ID` |

## Merchants (`MERCH_*.csv` to `stg_merchants`)

| Legacy | Staging | Type | Rule |
|---|---|---|---|
| `MERCH_ID` | `merchant_id` | integer | key |
| `MERCH_NM` | `merchant_name` | text | title case |
| `CAT_CD` | `category_code` | text | trimmed; checked against the `merchant_categories` seed |
| `CAT_NM` | `category_name` | text | taken from the seed by code; the delivered name is only a fallback |
| `CTRY` | `country_code` | text | upper |
| `CITY` | `city` | text | trimmed |
| `CRT_TS` | `created_at` | timestamp | |
| `UPD_TS` | `updated_at` | timestamp | version key with `MERCH_ID` |

## Transactions (`TXN_*.csv` to `stg_transactions`)

| Legacy | Staging | Type | Rule |
|---|---|---|---|
| `TXN_ID` | `txn_id` | bigint | key |
| `ACCT_NO` | `account_no` | text | |
| `CARD_ID` | `card_id` | text | null unless a card purchase |
| `MERCH_ID` | `merchant_id` | integer | null unless a card purchase or bill payment |
| `TXN_TYP` | `txn_type` | text | upper; one of `CARD_PURCHASE`, `BILL_PAYMENT`, `TRANSFER_IN`, `TRANSFER_OUT`, `ATM_WITHDRAWAL`, `FEE`, `SALARY`, `INTEREST` |
| `BOOK_DT` | `event_date` | date | booking date, the date every report and KPI is based on |
| `AMT` | `amount` | decimal(18,2) | decimal comma; signed from the account's view, debits negative |
| `CCY` | `currency` | text | upper; `NOK`, `EUR`, `USD` or `SEK` |
| `STAT` | `status` | text | upper |
| `REV_OF` | `reversal_of` | bigint | id of the reversed transaction, on reversal rows only |
| `CRT_TS` | `created_at` | timestamp | first write in the core system |
| `UPD_TS` | `updated_at` | timestamp | last change; version key with `TXN_ID`. `updated_at > created_at` marks a corrected row |

## Exchange rates (`fx_rates_nok.csv` to `stg_fx_rates`)

The rate cache is not a legacy file: comma separated, UTF-8, ISO dates, decimal point.

| Cache | Staging | Type | Rule |
|---|---|---|---|
| `rate_date` | `rate_date` | date | publication date |
| `currency` | `currency` | text | upper |
| `rate_nok` | `rate_nok` | decimal(18,6) | NOK per one unit; the Norges Bank feed quotes SEK per 100 and the extract job already divides by the unit multiplier |

Days without a publication get the last published rate in `int_fx_rates_daily`.

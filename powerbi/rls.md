# Row-level security in the Power BI report

The report applies the same regional rule as the Snowflake row access policies: a regional
manager sees only the customers, accounts, transactions and balances of their region.

## Static roles

Modeling > Manage roles. One role per region, each with a filter on `Customer Current`:

| Role | Table | DAX filter |
|---|---|---|
| `Region Vestland` | `Customer Current` | `[region] = "Vestland"` |
| `Region Oslo` | `Customer Current` | `[region] = "Oslo"` |
| `All regions` | (no filter) | |

Because `Customer Current` filters `DIM_ACCOUNT` (one to many on `customer_id`) and
`DIM_ACCOUNT` filters both facts (one to many on `account_no`), the regional filter reaches
transactions and balances through the relationships. No bidirectional filtering is needed.

`DIM_CUSTOMER` (all versions) is reached from the transaction fact only. Add the same filter
on it (`[region] = "Vestland"` on the current version) if you want the customer version
table itself to be restricted in table visuals:

```
DIM_CUSTOMER[is_current] = TRUE () && DIM_CUSTOMER[region] = "Vestland"
```

## Dynamic role (one role, many users)

Import a small mapping table `Region Access` with the columns `user_principal_name` and
`region` (the Power BI counterpart of `MARTS.REGION_ACCESS`). Create one role, `Regional
manager`, with this filter on `Customer Current`:

```
[region]
    IN CALCULATETABLE (
        VALUES ( 'Region Access'[region] ),
        'Region Access'[user_principal_name] = USERPRINCIPALNAME ()
    )
```

Users not listed in the table see nothing, which is the safe default.

## Testing

Modeling > View as > pick a role (and, for the dynamic role, type a user principal name
listed in the mapping table). The cards and charts should show one region only. Compare the
regional totals with an unrestricted view: the sum over all regional roles must equal the
total.

Keep a screenshot of the "View as" banner with each role for the documentation.

## Relation to the Snowflake policies

Snowflake enforces the rule for anyone who queries `MARTS` with a regional role, whatever
tool they use. Power BI enforces it inside the report for viewers of the published report.
With Import mode the report holds all rows, so the Power BI role is what protects viewers;
with DirectQuery the Snowflake policy would apply as well, provided each viewer connects
with their own regional role.

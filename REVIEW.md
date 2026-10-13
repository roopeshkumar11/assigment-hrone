# REVIEW.md

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `compute_late_minutes` | Incorrect timezone and naive datetime logic. It mutates UTC naive datetime to IST directly. It also fails to implement floor minutes (R2). | Test a late punch-in; the minutes would be incorrectly calculated or crash on DST/tz boundaries. | Replaced with logic that converts UTC datetime to IST, parses `shift_start`, and calculates floor minutes delta. |
| 2 | `compute_work_hours` | Uses `round()`, which applies banker's rounding. Rule R4 requires half-up rounding. | E.g. `round(4.5, 0)` gives 4.0 instead of 5.0 (or similar with 2 decimals). | Used Python's `decimal` module with `ROUND_HALF_UP` to round to 2 decimals. |
| 3 | `compute_overtime` | Mixes naive datetime (`punch_out`) with aware datetime (`end`), raising `TypeError`. It also misses the minimum 30 min requirement (R3). | Punching out an employee would yield a `TypeError` in production. | Convert `punch_out` to IST, calculate delta, and return 0 if delta < 30. |
| 4 | `create_employee` | Check-then-insert race condition. Simultaneous requests with the same `emp_code` could both insert, leading to duplicates. | Sending two simultaneous `POST /employees` with the same body yields two 201s. | Added a MongoDB unique index on `emp_code` and handled `DuplicateKeyError` to return 409. |
| 5 | `create_employee` | `datetime.now()` creates a naive local datetime instead of a UTC/aware one. | The stored `created_at` would be interpreted as UTC by default, making it wrong by 5h30m in India. | Replaced with `datetime.now(timezone.utc)`. |
| 6 | `punch_in` | Check-then-insert race condition for `(emp_code, date)`. | Sending two simultaneous `punch-in` requests could yield two records for the same day. | Added a compound unique index on `(emp_code, date)` and handled `DuplicateKeyError` to return 409. |
| 7 | `punch_in` | Fails to truncate instants to whole seconds (R1), and `ts.date()` uses UTC/local instead of IST to determine the attendance date. Doesn't handle overnight shifts correctly. | 09:40:00.900 is kept with ms. Also, overnight shift punch-ins would be recorded under the wrong date. | Truncated ms to 0. Converted `ts` to IST to compute `date`. Added logic for overnight shift detection. |
| 8 | `punch_in` | If the employee doesn't exist, `emp["shift_start"]` raises `TypeError` (HTTP 500). | A request with a non-existent `emp_code` returns 500. | Added a check: `if not emp: raise HTTPException(404, ...)`. |
| 9 | `list_employees` | `total` counts all documents in the collection, ignoring the `department` filter. | Querying `?department=Engineering` might return 3 items but `total: 15`. | Modified `db.employees.count_documents(q)` to use the filter `q`. |
| 10 | `list_attendance` | Loads the entire collection into memory to sort, paginate, and doesn't validate `date_from > date_to`. | High memory usage/OOM on 100k records. Request with invalid date range succeeds instead of returning 422. | Used MongoDB cursor `.sort().skip().limit()`. Added validation to return 422 if `date_from > date_to`. |

**Not a defect:**
- Connecting to MongoDB directly in the global scope. This is standard in FastAPI starter templates (though dependency injection might be better, it is acceptable here).
- Hardcoding IST instead of inferring it, as R1 explicitly mandates IST.

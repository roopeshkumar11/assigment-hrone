# DECISIONS.md

1. **Indexes.** Which indexes did you create, and which query or guarantee does each one serve? Which one did you consider and reject?
- `employees {emp_code: 1} (unique)`: Enforces uniqueness of `emp_code` and optimizes lookups.
- `attendance_logs {emp_code: 1, date: 1} (unique)`: Enforces exactly one record per employee per day and prevents punch-in races.
- `attendance_logs {date: -1, emp_code: 1}`: Optimizes the `list_attendance` endpoint's exact sorting and filtering.
- `employees {department: 1}`: Optimizes employee list filtering and department-based analytics.
- `attendance_logs {date: 1, late_minutes: 1}`: Optimizes the late leaderboard.
- I considered creating a heavily compound index `{emp_code: 1, date: 1, status: 1, late_minutes: 1, half_day: 1}` to fully cover the analytics queries. I rejected it because it adds unnecessary write overhead; the `{emp_code: 1, date: 1}` unique index and the `{date: 1}` prefix are sufficiently selective for lookups and aggregations.

2. **Punch-in race.** Two identical punch-in requests arrive at the same instant. Walk through exactly what happens in your code.
Both requests fetch the employee document and construct the attendance record. Both attempt to execute `db.attendance_logs.insert_one(doc)` concurrently. The first request successfully inserts the record. The second request attempts to insert a record with the same `emp_code` and `date`. The compound unique index on `(emp_code, date)` triggers a `DuplicateKeyError` in MongoDB. The FastAPI application catches this exception and returns a `409 Conflict` to the client.

3. **Ties.** Two employees are tied on late minutes at the cutoff of `limit`. What does your leaderboard return, and why?
The pipeline uses the MongoDB `$setWindowFields` stage with the `$rank` operator, which implements standard competition ranking (e.g., 1, 2, 2, 4). The filtering stage `$match: {rank: {$lte: limit}}` is applied after the ranking is computed. If there is a tie at the cutoff rank, all employees sharing that rank will satisfy the condition and be returned. Therefore, the endpoint might return more than `limit` rows.

4. **Headcount.** How does your department summary make sure employees with zero logs are counted?
The pipeline intentionally originates from the `employees` collection rather than `attendance_logs`. It first matches all employees who joined on or before the end of the month, grouping them by department to reliably compute the total headcount. It then uses a `$lookup` to fetch their corresponding attendance logs. Even if the `$lookup` returns an empty array for an employee, they have already contributed to the departmental headcount count.

5. **One thing you would change** if this had to serve 100x the data.
Running complex aggregations over millions of raw attendance records on demand would eventually time out. I would introduce daily materialized views. A background cron job would pre-aggregate the daily metrics for each employee and department at midnight, writing them to dedicated reporting collections. Analytics endpoints would simply query these pre-computed summaries instead of scanning massive raw log collections.

# Starter Code Review

This document summarizes the main defects identified in the starter implementation, the changes made in `app/main.py`, and the remaining items to address before submission.

## 1. Employee Management

### Employee creation
- **Problem:** Duplicate employee codes could cause conflicting records, and the creation timestamp was not explicitly timezone-aware.
- **Fix:** Added a unique index on `emp_code`, handled `DuplicateKeyError` with HTTP 409, and used `datetime.now(timezone.utc)` for `created_at`.
- **Response:** The internal MongoDB `_id` is removed, and `created_at` is returned in epoch milliseconds.

### Employee listing
- **Problem:** Pagination, filtering, and total counts could be incorrect or inefficient.
- **Fix:** Pagination uses `(page - 1) * page_size`, validates page limits, applies the department filter to both results and count, and sorts by `emp_code` ascending.

## 2. Attendance Calculations

### Late arrival
- **Problem:** The original calculation did not correctly handle overnight shifts or the 10-minute grace period.
- **Fix:** `compute_late_minutes()` considers both shift start and end times, handles early-morning punches for overnight shifts, truncates fractional seconds, and records lateness only when it exceeds 10 minutes.

### Work hours and overtime
- **Problem:** Default rounding and overnight shift-end calculations could produce incorrect results.
- **Fix:** `compute_work_hours()` uses `Decimal` with `ROUND_HALF_UP` to round to two decimal places. `compute_overtime()` handles overnight shifts and counts overtime only when it reaches 30 whole minutes.

### Attendance date and timestamps
- **Problem:** Local timezone assumptions and fractional seconds could affect attendance calculations.
- **Fix:** Punch-in timestamps are converted to IST, truncated to whole seconds, and assigned an attendance date that accounts for overnight shifts.

## 3. Punch-In and Punch-Out

### Punch-in validation and concurrency
- **Problem:** Unknown employees, invalid statuses, and simultaneous requests could cause incorrect responses or duplicate attendance records.
- **Fix:** Unknown employees return HTTP 404, invalid presence statuses return HTTP 422, and a unique compound index on `(emp_code, date)` protects against duplicate records. Duplicate-key errors return HTTP 409.

### Punch-out processing
- **Problem:** The original implementation did not reliably handle open attendance records, repeated punch-outs, or elapsed-time limits.
- **Fix:** The endpoint finds the most recent open punch-in at or before the supplied timestamp, rejects invalid ordering and durations exceeding 24 hours, and calculates work hours, overtime, and half-day status. An atomic update prevents two concurrent requests from closing the same record successfully.

### Response identifiers
- **Problem:** Internal MongoDB identifiers should not appear in API responses.
- **Fix:** Attendance responses remove `_id` and expose punch timestamps as epoch milliseconds.

## 4. Attendance Listing and Regularization

### Attendance filtering and pagination
- **Problem:** Invalid date ranges, inefficient pagination, and inconsistent sorting could affect results.
- **Fix:** Date formats and date ranges are validated, filtering and counting are performed in MongoDB, and records are sorted by date descending and employee code ascending. Pagination limits are validated.

### Regularization and audit history
- **Problem:** Manual corrections must preserve the audit trail and recompute derived attendance fields.
- **Fix:** The PATCH endpoint validates status changes and timestamps, recalculates attendance metrics, rejects no-op updates, records actual field changes, and uses a conditional update to detect concurrent modifications. Missing history and half-day fields are handled with defaults.

## 5. Analytics

### Monthly employee summary
- **Fix:** Calculates working days from the joining date, counts weekday presence with half-days weighted as 0.5, and reports leave, lateness, overtime, and attendance percentage.

### Department summary
- **Fix:** Starts from employees who joined by month-end and uses `$lookup` with preserved empty results, so employees without attendance logs still contribute to headcount. The aggregation calculates department-level attendance metrics and sorts departments by name.

### Late leaderboard
- **Fix:** Uses MongoDB aggregation and competition ranking. Ties receive the same rank, and applying the limit after ranking allows tied employees at the cutoff to be included.

### Department trend
- **Fix:** Uses `$densify` to fill missing dates and `$setWindowFields` for the seven-day moving average. It calculates daily headcount, presence, lateness, and attendance rates, with validation for date ranges and unknown departments.

### Query-plan inspection
- **Fix:** The explain endpoint supports attendance listing, employee monthly analytics, department summaries, the late leaderboard, and department trends using execution statistics.

## 6. Remaining Items to Verify or Fix

The following points should not be considered resolved solely because the relevant endpoints exist:

1. **Punch-in timestamp validation:** Unlike punch-out and regularization, punch-in does not explicitly validate the supplied epoch-millisecond range before converting it.
2. **Regularization history storage:** The current implementation stores `history.at` and changed punch-time values as epoch-millisecond integers in MongoDB. `DATA_MODEL.md` specifies BSON dates for these fields. Update the storage format to match the data model while retaining epoch milliseconds in API responses.
3. **Employee input validation:** Verify that `joined_on`, `shift_start`, and `shift_end` are validated against the required formats before employee creation.
4. **Concurrency and edge cases:** Test simultaneous punch-ins, simultaneous punch-outs, overnight boundary times, rounding boundaries, legacy records without history, and leaderboard ties using appropriate test cases.
5. **Explain plans:** Recheck all five explain endpoints against the assignment's index and collection-scan requirements using execution statistics.

## Conclusion

The implementation now includes employee management, attendance processing, regularization, analytics, and query-plan inspection. The remaining items above should be checked against the assignment contract and tested before the final submission. This review describes the code inspected; it does not claim that every possible edge case has been verified.
# DECISIONS.md

### 1. Indexes
I created a unique index on `emp_code` to prevent duplicate employees and a unique compound index on `(emp_code, date)` to prevent duplicate daily attendance records. Other indexes support department filtering, date-based attendance queries, and status filtering. I would avoid adding indexes that are not needed because they increase storage and write costs.

### 2. Punch-in race
If two identical punch-in requests arrive together, both may initially find no record. The unique `(emp_code, date)` index allows only one record to be inserted. The other request gets a duplicate-key error, which the API should handle without creating a second record.

### 3. Ties
The leaderboard uses competition ranking. Employees tied on late minutes receive the same rank. Since the limit is applied after ranking, tied employees at the cutoff can make the result contain more than the requested limit.

### 4. Headcount
The department summary starts with employees who joined by the end of the selected month and looks up their attendance logs. Employees with no logs are therefore still counted.

### 5. Future improvement
For 100 times more data, I would test query performance on a larger dataset and optimize indexes and aggregation pipelines based on execution statistics.
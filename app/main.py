"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

from decimal import Decimal, ROUND_HALF_UP

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]


IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


@app.on_event("startup")
def create_indexes():
    # Employee indexes
    db.employees.create_index(
        [("emp_code", 1)],
        unique=True
    )

    db.employees.create_index(
        [("department", 1), ("emp_code", 1)]
    )

    # Attendance indexes
    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)],
        unique=True
    )

    db.attendance_logs.create_index(
        [("date", -1), ("emp_code", 1)]
    )

    db.attendance_logs.create_index(
        [("status", 1), ("date", -1), ("emp_code", 1)]
    )




# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #



def compute_late_minutes(
    punch_in: datetime,
    shift_start: str,
    shift_end: str
) -> int:
    if punch_in.tzinfo is None:
        punch_in = punch_in.replace(tzinfo=timezone.utc)

    punch_in = punch_in.astimezone(IST).replace(microsecond=0)

    start_hour, start_minute = map(int, shift_start.split(":"))
    end_hour, end_minute = map(int, shift_end.split(":"))

    shift_start_time = punch_in.replace(
        hour=start_hour,
        minute=start_minute,
        second=0,
        microsecond=0
    )

    # For overnight shifts, early-morning punches belong
    # to the previous day's shift.
    if shift_end <= shift_start:
        if punch_in.hour * 60 + punch_in.minute < end_hour * 60 + end_minute:
            shift_start_time -= timedelta(days=1)

    minutes_late = int(
        (punch_in - shift_start_time).total_seconds() // 60
    )

    # Record lateness only when more than 10 minutes late.
    return minutes_late if minutes_late > 10 else 0



def compute_work_hours(
    punch_in: datetime,
    punch_out: datetime
) -> float:
    # Truncate both timestamps before calculating.
    punch_in = punch_in.replace(microsecond=0)
    punch_out = punch_out.replace(microsecond=0)

    seconds = (punch_out - punch_in).total_seconds()
    hours = Decimal(str(seconds)) / Decimal("3600")

    # Round to two decimal places using half-up rounding.
    rounded_hours = hours.quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP
    )

    return float(rounded_hours)




def compute_overtime(
    punch_out: datetime,
    shift_start: str,
    shift_end: str,
    date_str: str
) -> int:
    if punch_out.tzinfo is None:
        punch_out = punch_out.replace(tzinfo=timezone.utc)

    punch_out = punch_out.astimezone(IST).replace(microsecond=0)

    end_hour, end_minute = map(int, shift_end.split(":"))

    shift_end_time = datetime.strptime(
        date_str, "%Y-%m-%d"
    ).replace(
        hour=end_hour,
        minute=end_minute,
        second=0,
        microsecond=0,
        tzinfo=IST
    )

    # Overnight shift ends on the following day.
    if shift_end <= shift_start:
        shift_end_time += timedelta(days=1)

    minutes = int(
        (punch_out - shift_end_time).total_seconds() // 60
    )

    # Overtime is counted only when it reaches 30 minutes.
    return minutes if minutes >= 30 else 0



# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"

    
class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None


class RegularizeRequest(BaseModel):
    status: Optional[str] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    reason: str
    regularized_by: str


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="MongoDB unavailable"
        )



@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="emp_code already exists"
        )

    doc.pop("_id", None)

    # Return created_at as epoch milliseconds.
    doc["created_at"] = int(
    doc["created_at"].timestamp() * 1000
    )

    return doc



@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    q = {}
    if department:
        q["department"] = department
    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)
    items = list(db.employees.find(q, {"_id": 0}).skip(skip).sort("emp_code", 1).limit(page_size))
    return {"items": items, "total": total, "page": page, "page_size": page_size}




@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    # Validate attendance status.
    if body.status not in ["PRESENT", "WFH", "ON_DUTY"]:
        raise HTTPException(
            status_code=422,
            detail="Invalid attendance status"
        )

    # Check whether the employee exists.
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if emp is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found"
        )

    # Convert epoch milliseconds to IST, or use the current time.
    if body.punched_at is not None:
        ts = datetime.fromtimestamp(
            body.punched_at / 1000,
            tz=timezone.utc
        ).astimezone(IST)
    else:
        ts = datetime.now(IST)

    # Truncate fractional seconds before calculations.
    ts = ts.replace(microsecond=0)

    # Determine the attendance date in IST.
    d = ts.date().isoformat()

    shift_start = emp["shift_start"]
    shift_end = emp["shift_end"]

    # For overnight shifts, an early-morning punch belongs
    # to the shift that started on the previous day.
    if shift_end <= shift_start:
        shift_end_hour, shift_end_minute = map(
            int, shift_end.split(":")
        )

        current_minutes = ts.hour * 60 + ts.minute
        shift_end_minutes = (
            shift_end_hour * 60 + shift_end_minute
        )

        if current_minutes < shift_end_minutes:
            d = (ts.date() - timedelta(days=1)).isoformat()

    # Check for an existing attendance record.
    # The unique database index protects against concurrent requests.
    existing = db.attendance_logs.find_one({
        "emp_code": body.emp_code,
        "date": d
    })

    if existing:
        raise HTTPException(
            status_code=409,
            detail="Already punched in for this date"
        )

    # Build the attendance document.
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(
            ts,
            shift_start,
            shift_end
        ),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    # Insert the record. Concurrent duplicates receive HTTP 409.
    
    # Insert the record into MongoDB.
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="Already punched in for this date"
        )

    # Create a separate response so the stored document is unchanged.
    response = doc.copy()

    # Never return MongoDB's internal ObjectId.
    response.pop("_id", None)

    # Return punch_in as epoch milliseconds.
    response["punch_in"] = int(
        ts.astimezone(timezone.utc).timestamp() * 1000
    )

    return response



def validate_date(value: Optional[str], field_name: str):
    if value is None:
        return None

    try:
        parsed_date = date.fromisoformat(value)

        # Ensure the input strictly follows YYYY-MM-DD.
        if parsed_date.isoformat() != value:
            raise ValueError

        return value

    except (ValueError, TypeError):
        raise HTTPException(
            status_code=422,
            detail=(
                f"{field_name} must be a valid date "
                "in YYYY-MM-DD format"
            )
        )


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    # Validate dates before building the MongoDB query.
    date_from = validate_date(date_from, "date_from")
    date_to = validate_date(date_to, "date_to")

    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=422,
            detail="date_from cannot be later than date_to"
        )

    # Build the query using only the supplied filters.
    q = {}

    if emp_code:
        q["emp_code"] = emp_code

    if date_from or date_to:
        q["date"] = {}

        if date_from:
            q["date"]["$gte"] = date_from

        if date_to:
            q["date"]["$lte"] = date_to

    if status:
        q["status"] = status

    total = db.attendance_logs.count_documents(q)

    skip = (page - 1) * page_size

   
    page_docs = list(
        db.attendance_logs.find(q, {"_id": 0})
        .sort([("date", -1), ("emp_code", 1)])
        .skip(skip)
        .limit(page_size)
    )

    for item in page_docs:
        for field in ("punch_in", "punch_out"):
            value = item.get(field)

            if value is not None:
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)

                item[field] = int(value.timestamp() * 1000)

    return {
        "items": page_docs,
        "total": total,
        "page": page,
        "page_size": page_size,
    }

@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str, date: str, body: RegularizeRequest
):
    attendance_date = validate_date(date, "date")

    if len(body.reason.strip()) < 5 or len(body.reason) > 200:
        raise HTTPException(status_code=422, detail="Invalid reason")

    if not body.regularized_by.strip() or len(body.regularized_by) > 50:
        raise HTTPException(status_code=422, detail="Invalid regularized_by")

    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    record = db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": attendance_date
    })
    if record is None:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    changes_requested = body.model_fields_set
    old = {
        "status": record["status"],
        "punch_in": record.get("punch_in"),
        "punch_out": record.get("punch_out"),
        "work_hours": record.get("work_hours"),
        "late_minutes": record.get("late_minutes", 0),
        "overtime_minutes": record.get("overtime_minutes", 0),
        "half_day": record.get("half_day", False),
    }

    new = old.copy()

    if "status" in changes_requested:
        if body.status not in ["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]:
            raise HTTPException(status_code=422, detail="Invalid status")
        new["status"] = body.status

   
    for field in ("punch_in", "punch_out"):
        if field in changes_requested:
            value = getattr(body, field)

            if value is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"{field} cannot be null when supplied"
                )

            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 100000000000 <= value <= 4102444800000
            ):
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid {field}"
                )

            instant = datetime.fromtimestamp(
                value / 1000, tz=timezone.utc
            ).replace(microsecond=0)

            new[field] = instant


    if new["status"] in ["ABSENT", "LEAVE"]:
        if "punch_in" in changes_requested or "punch_out" in changes_requested:
            raise HTTPException(
                status_code=422,
                detail="ABSENT or LEAVE cannot include punch times"
            )
        new["punch_in"] = None
        new["punch_out"] = None
        new["work_hours"] = None
        new["late_minutes"] = 0
        new["overtime_minutes"] = 0
        new["half_day"] = False
    else:
        if new["punch_in"] is None:
            raise HTTPException(
                status_code=422,
                detail="Presence status requires punch_in"
            )

        punch_in_utc = new["punch_in"]
        if punch_in_utc.tzinfo is None:
            punch_in_utc = punch_in_utc.replace(tzinfo=timezone.utc)

        punch_in_ist = punch_in_utc.astimezone(IST)

        # Punch-in must belong to this attendance date under R1.
        expected_date = punch_in_ist.date().isoformat()
        if emp["shift_end"] <= emp["shift_start"]:
            end_hour, end_minute = map(int, emp["shift_end"].split(":"))
            if punch_in_ist.hour * 60 + punch_in_ist.minute < end_hour * 60 + end_minute:
                expected_date = (
                    punch_in_ist.date() - timedelta(days=1)
                ).isoformat()

        if expected_date != attendance_date:
            raise HTTPException(
                status_code=422,
                detail="Punch-in does not belong to this attendance date"
            )

        new["late_minutes"] = compute_late_minutes(
            punch_in_ist, emp["shift_start"], emp["shift_end"]
        )

        if new["punch_out"] is not None:
            punch_out_utc = new["punch_out"]
            if punch_out_utc.tzinfo is None:
                punch_out_utc = punch_out_utc.replace(tzinfo=timezone.utc)

            if punch_out_utc <= punch_in_utc:
                raise HTTPException(
                    status_code=422,
                    detail="Punch-out must be later than punch-in"
                )
            if (punch_out_utc - punch_in_utc).total_seconds() > 86400:
                raise HTTPException(
                    status_code=422,
                    detail="Punch-out cannot be more than 24 hours after punch-in"
                )

            punch_out_ist = punch_out_utc.astimezone(IST)
            new["work_hours"] = compute_work_hours(
                punch_in_utc, punch_out_utc
            )
            new["overtime_minutes"] = compute_overtime(
                punch_out_ist,
                emp["shift_start"],
                emp["shift_end"],
                attendance_date
            )
            new["half_day"] = new["work_hours"] < 4.50
        else:
            new["punch_out"] = None
            new["work_hours"] = None
            new["overtime_minutes"] = 0
            new["half_day"] = False

    def as_epoch(value):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp() * 1000)

    history_changes = {}
    for field in (
        "status", "punch_in", "punch_out", "work_hours",
        "late_minutes", "overtime_minutes", "half_day"
    ):
        before = old[field]
        after = new[field]

        if field in ("punch_in", "punch_out"):
            before = as_epoch(before)
            after = as_epoch(after)

        if before != after:
            history_changes[field] = {"from": before, "to": after}

    if not history_changes:
        raise HTTPException(
            status_code=422, detail="Request does not change anything"
        )

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    history_entry = {
        "at": now_ms,
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": history_changes
    }

    set_values = {}
    for field in (
        "status", "punch_in", "punch_out", "work_hours",
        "late_minutes", "overtime_minutes", "half_day"
    ):
        if old[field] != new[field]:
            value = new[field]
            if field in ("punch_in", "punch_out") and value is not None:
                value = value.astimezone(timezone.utc)
            set_values[field] = value

    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "history": record.get("history", [])
        },
        {
            "$set": set_values,
            "$push": {"history": history_entry}
        }
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record changed concurrently; retry"
        )

    updated = db.attendance_logs.find_one({"_id": record["_id"]})
    updated.pop("_id", None)

    for field in ("punch_in", "punch_out"):
        updated[field] = as_epoch(updated.get(field))

    for entry in updated.get("history", []):
        entry["at"] = int(entry["at"])
        for change in entry.get("changes", {}).values():
            for side in ("from", "to"):
                if isinstance(change.get(side), datetime):
                    change[side] = as_epoch(change[side])

    return updated


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    # Convert the supplied epoch milliseconds to IST.
    if body.punched_at is not None:
        if (
            isinstance(body.punched_at, bool)
            or not isinstance(body.punched_at, int)
            or not 100000000000 <= body.punched_at <= 4102444800000
        ):
            raise HTTPException(
                status_code=422, detail="Invalid punched_at timestamp"
            )

        ts = datetime.fromtimestamp(
            body.punched_at / 1000, tz=timezone.utc
        ).astimezone(IST)
    else:
        ts = datetime.now(IST)

    # Truncate to whole seconds.
    ts = ts.replace(microsecond=0)
    ts_utc = ts.astimezone(timezone.utc)

    # Find the most recent open punch-in at or before this timestamp.
    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$lte": ts_utc},
            "punch_out": None,
        },
        sort=[("punch_in", -1)],
    )

    if record is None:
        # Distinguish an already-closed punch-in from no punch-in.
        previous = db.attendance_logs.find_one(
            {
                "emp_code": body.emp_code,
                "punch_in": {"$lte": ts_utc},
            },
            sort=[("punch_in", -1)],
        )

        if previous is not None:
            raise HTTPException(
                status_code=409, detail="Already punched out"
            )

        raise HTTPException(
            status_code=404, detail="Attendance record not found"
        )

    punch_in = record["punch_in"]
    if punch_in.tzinfo is None:
        punch_in = punch_in.replace(tzinfo=timezone.utc)
    else:
        punch_in = punch_in.astimezone(timezone.utc)

    elapsed_seconds = (ts_utc - punch_in).total_seconds()

    if elapsed_seconds <= 0:
        raise HTTPException(
            status_code=422,
            detail="Punch-out must be later than punch-in",
        )

    if elapsed_seconds > 86400:
        raise HTTPException(
            status_code=422,
            detail="Punch-out cannot be more than 24 hours after punch-in",
        )

    attendance_date = record["date"]

    work_hours = compute_work_hours(punch_in, ts_utc)
    overtime_minutes = compute_overtime(
        ts, emp["shift_start"], emp["shift_end"], attendance_date
    )
    half_day = work_hours < 4.50

    # Atomic update: only one concurrent request can close this record.
    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": ts_utc,
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day,
            }
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409, detail="Already punched out"
        )

    updated = db.attendance_logs.find_one({"_id": record["_id"]})
    updated.pop("_id", None)

    for field in ("punch_in", "punch_out"):
        value = updated.get(field)
        if value is not None:
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            updated[field] = int(value.timestamp() * 1000)

    return updated



@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    import calendar
    import re
    from decimal import Decimal, ROUND_HALF_UP

    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(
            status_code=422,
            detail="month must use YYYY-MM format"
        )

    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    year, month_number = map(int, month.split("-"))
    last_day = calendar.monthrange(year, month_number)[1]
    month_start = f"{month}-01"
    month_end = f"{month}-{last_day:02d}"

    try:
        joined_value = emp["joined_on"]

        if isinstance(joined_value, datetime):
            joined_on = joined_value.date()
        else:
            joined_on = date.fromisoformat(joined_value)

    except (ValueError, TypeError, KeyError):
        raise HTTPException(
            status_code=422,
            detail="Employee has an invalid joined_on date"
        )
    first_day = max(joined_on, date(year, month_number, 1))
    final_day = date(year, month_number, last_day)

    working_days = 0
    if first_day <= final_day:
        current = first_day
        while current <= final_day:
            if current.weekday() < 5:
                working_days += 1
            current += timedelta(days=1)

    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {"$gte": month_start, "$lte": month_end}
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {
                                "$cond": [
                                    "$half_day", 0.5, 1
                                ]
                            },
                            0
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {
                        "$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]
                    }
                },
                "total_late_minutes": {"$sum": "$late_minutes"},
                "total_overtime_minutes": {"$sum": "$overtime_minutes"}
            }
        }
    ]

    result = list(db.attendance_logs.aggregate(pipeline))
    totals = result[0] if result else {}

    # Present days only count on weekdays.
    weekday_pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {"$gte": month_start, "$lte": month_end},
                "status": {"$in": ["PRESENT", "WFH", "ON_DUTY"]}
            }
        },
        {
            "$addFields": {
                "date_obj": {
                    "$dateFromString": {"dateString": "$date"}
                }
            }
        },
        {
            "$match": {
                "$expr": {
                    "$in": [
                        {"$dayOfWeek": "$date_obj"},
                        [2, 3, 4, 5, 6]
                    ]
                }
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {"$cond": ["$half_day", 0.5, 1]}
                }
            }
        }
    ]

    weekday_result = list(db.attendance_logs.aggregate(weekday_pipeline))
    present_days = weekday_result[0]["present_days"] if weekday_result else 0

    attendance_pct = None
    if working_days > 0:
        pct = (
            Decimal(str(present_days)) * Decimal("100")
            / Decimal(working_days)
        )
        attendance_pct = float(
            pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": totals.get("leave_days", 0),
        "late_count": totals.get("late_count", 0),
        "total_late_minutes": totals.get("total_late_minutes", 0),
        "total_overtime_minutes": totals.get("total_overtime_minutes", 0),
        "attendance_pct": attendance_pct
    }



@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None
):
    import calendar
    import re

    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(
            status_code=422,
            detail="month must be in YYYY-MM format"
        )

    year, month_number = map(int, month.split("-"))
    last_day = calendar.monthrange(year, month_number)[1]

    start_date = f"{month}-01"
    end_date = f"{month}-{last_day:02d}"
 
    employee_match = {
       "joined_on": {
        "$lte": end_date
    }
    }

    if department:
        employee_match["department"] = department

    pipeline = [
        {"$match": employee_match},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"emp": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {"$eq": ["$emp_code", "$$emp"]},
                            "date": {
                                "$gte": start_date,
                                "$lte": end_date
                            }
                        }
                    },
                    {
                        "$addFields": {
                            "is_working_day": {
                                "$in": [
                                    {
                                        "$dayOfWeek": {
                                            "$dateFromString": {
                                                "dateString": "$date"
                                            }
                                        }
                                    },
                                    [2, 3, 4, 5, 6]
                                ]
                            }
                        }
                    }
                ],
                "as": "logs"
            }
        },
        {
            "$unwind": {
                "path": "$logs",
                "preserveNullAndEmptyArrays": True
            }
        },
        {
            "$group": {
                "_id": {
                    "department": "$department",
                    "emp_code": "$emp_code"
                },
                "headcount": {"$first": 1},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$logs.status",
                                            ["PRESENT", "WFH", "ON_DUTY"]
                                        ]
                                    },
                                    {"$eq": ["$logs.is_working_day", True]}
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$logs.half_day", True]},
                                    0.5,
                                    1
                                ]
                            },
                            0
                        ]
                    }
                },
                "work_hours_total": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$logs.status",
                                            ["PRESENT", "WFH", "ON_DUTY"]
                                        ]
                                    },
                                    {"$ne": ["$logs.work_hours", None]}
                                ]
                            },
                            "$logs.work_hours",
                            0
                        ]
                    }
                },
                "work_hours_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$logs.status",
                                            ["PRESENT", "WFH", "ON_DUTY"]
                                        ]
                                    },
                                    {"$ne": ["$logs.work_hours", None]}
                                ]
                            },
                            1,
                            0
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]},
                            1,
                            0
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$logs.late_minutes", 0]}
                },
                "leave_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$logs.status", "LEAVE"]},
                            1,
                            0
                        ]
                    }
                },
                "on_duty_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$logs.status", "ON_DUTY"]},
                            1,
                            0
                        ]
                    }
                }
            }
        },
        {
            "$group": {
                "_id": "$_id.department",
                "headcount": {"$sum": "$headcount"},
                "present_days": {"$sum": "$present_days"},
                "work_hours_total": {"$sum": "$work_hours_total"},
                "work_hours_count": {"$sum": "$work_hours_count"},
                "late_count": {"$sum": "$late_count"},
                "total_late_minutes": {"$sum": "$total_late_minutes"},
                "leave_count": {"$sum": "$leave_count"},
                "on_duty_count": {"$sum": "$on_duty_count"}
            }
        },
        {"$sort": {"_id": 1}}
    ]

    results = list(db.employees.aggregate(pipeline))

    items = []

    for item in results:
        avg_work_hours = None

        if item["work_hours_count"] > 0:
            avg_work_hours = float(
                (
                    Decimal(str(item["work_hours_total"]))
                    / Decimal(str(item["work_hours_count"]))
                ).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP
                )
            )

        items.append({
            "department": item["_id"],
            "headcount": item["headcount"],
            "present_days": item["present_days"],
            "avg_work_hours": avg_work_hours,
            "late_count": item["late_count"],
            "total_late_minutes": item["total_late_minutes"],
            "leave_count": item["leave_count"],
            "on_duty_count": item["on_duty_count"]
        })

    return {"month": month, "items": items}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(default=10, ge=1, le=50),
    department: Optional[str] = None
):
    import calendar
    import re

    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(
            status_code=422,
            detail="month must be in YYYY-MM format"
        )

    last_day = calendar.monthrange(
        int(month[:4]), int(month[5:7])
    )[1]

    start_date = f"{month}-01"
    end_date = f"{month}-{last_day:02d}"

    pipeline = [
        {
            "$match": {
                "date": {"$gte": start_date, "$lte": end_date},
                "late_minutes": {"$gt": 0}
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1}
            }
        },
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee"
            }
        },
        {"$unwind": "$employee"}
    ]

    if department is not None:
        pipeline.append({
            "$match": {"employee.department": department}
        })

    pipeline.extend([
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {
                    "rank": {"$rank": {}}
                }
            }
        },
        {"$match": {"rank": {"$lte": limit}}},
        {
            "$sort": {
                "total_late_minutes": -1,
                "_id": 1
            }
        },
        {
            "$project": {
                "_id": 0,
                "rank": 1,
                "emp_code": "$_id",
                "name": "$employee.name",
                "department": "$employee.department",
                "total_late_minutes": 1,
                "late_count": 1
            }
        }
    ])

    items = list(db.attendance_logs.aggregate(pipeline))

    return {
        "month": month,
        "items": items
    }


@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to")
):
    from datetime import date, datetime, time, timedelta, timezone
    from decimal import Decimal, ROUND_HALF_UP

    start = validate_date(from_date, "from")
    end = validate_date(to_date, "to")
    start_day = date.fromisoformat(start)
    end_day = date.fromisoformat(end)

    if end_day < start_day:
        raise HTTPException(status_code=422, detail="'to' cannot be before 'from'")

    if (end_day - start_day).days + 1 > 92:
        raise HTTPException(status_code=422, detail="Date range cannot exceed 92 days")

    if not db.employees.find_one({"department": department}):
        raise HTTPException(status_code=404, detail="Unknown department")

    # Convert ISO dates to UTC BSON dates for aggregation.
    start_dt = datetime.combine(start_day, time.min, tzinfo=timezone.utc)
    end_dt = datetime.combine(end_day, time.min, tzinfo=timezone.utc)

    pipeline = [
        {"$match": {"department": department}},
       
        {
            "$lookup": { "from": "attendance_logs",
                "let": {"employee_code": "$emp_code"}, "pipeline": [
                    {
                        "$match": { "$expr": { "$and": [
                                    {"$eq": ["$emp_code", "$$employee_code"]},
                                    {"$gte": ["$date", start]},
                                    {"$lte": ["$date", end]} ]
                            } } }
                ], "as": "logs" }
        },

        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {
            "$group": { "_id": "$logs.date", "present_count": {
                    "$sum": { "$cond": [ {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$cond": [{"$eq": ["$logs.half_day", True]}, 0.5, 1]}, 0
                        ] } },
                "late_count": {
                    "$sum": { "$cond": [ {"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]}, 1, 0
                        ] } } }
        },
        {"$match": {"_id": {"$ne": None}}},
        {
            "$project": { "_id": 0, "date": {"$dateFromString": {"dateString": "$_id"}},
                "present_count": 1, "late_count": 1 }
        },
        {
            "$densify": { "field": "date",
                "range": { "step": 1, "unit": "day",
                    "bounds":  [start_dt, end_dt + timedelta(days=1)] } }
        },
        {
            "$set": {
                "present_count": {"$ifNull": ["$present_count", 0]},
                "late_count": {"$ifNull": ["$late_count", 0]}
            }
        },
        {
            "$set": { "date_string": {
                    "$dateToString": { "format": "%Y-%m-%d", "date": "$date" } },
                "weekday": {"$dayOfWeek": "$date"} }
        },
       
        {
            "$lookup": { "from": "employees", "let": {"current_date": "$date"},
                "pipeline": [ { "$match": { "$expr": {
                                "$and": [ {"$eq": ["$department", department]}, {
                                        "$lte": [ { "$convert": { "input": "$joined_on",
                                                    "to": "date", "onError": None, "onNull": None }
                                            }, "$$current_date" ] } ]
                            } } }, {"$count": "count"}
                ], "as": "headcount_result" }
        },

        {
            "$set": { "headcount": { "$ifNull": [
                        {"$arrayElemAt": ["$headcount_result.count", 0]}, 0 ] },
                "is_working_day": {"$and": [{"$gte": ["$weekday", 2]}, {"$lte": ["$weekday", 6]}]} }
        },
        {
            "$set": { "attendance_rate": {
                    "$cond": [ {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
                        {"$round": [{"$divide": ["$present_count", "$headcount"]}, 4]}, None ]
                } }
        },
        {
            "$setWindowFields": { "sortBy": {"date": 1},
                "output": { "moving_avg_7d": { "$avg": "$attendance_rate",
                        "window": {"documents": [-6, 0]} }
                } }
        },
        {
            "$project": { "_id": 0, "date": "$date_string", "is_working_day": 1, "headcount": 1,
                "present_count": 1, "late_count": 1, "attendance_rate": 1,
                "moving_avg_7d": {"$round": ["$moving_avg_7d", 4]}
            }
        },
        {"$sort": {"date": 1}}
    ]


    items = list(db.employees.aggregate(pipeline))

    return {"department": department, "items": items}


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = Query(default=10, ge=1, le=50),
    from_date: Optional[str] = Query(default=None, alias="from"),
    to_date: Optional[str] = Query(default=None, alias="to"),
):
    allowed_endpoints = {
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    }

    if endpoint not in allowed_endpoints:
        raise HTTPException(
            status_code=422,
            detail="Unsupported endpoint name"
        )

    if endpoint == "attendance_list":
        date_from = validate_date(date_from, "date_from")
        date_to = validate_date(date_to, "date_to")

        if date_from and date_to and date_from > date_to:
            raise HTTPException(
                status_code=422,
                detail="date_from cannot be later than date_to"
            )

        query = {}

        if emp_code:
            query["emp_code"] = emp_code

        if date_from or date_to:
            query["date"] = {}

            if date_from:
                query["date"]["$gte"] = date_from

            if date_to:
                query["date"]["$lte"] = date_to

        if status:
            query["status"] = status

        skip = (page - 1) * page_size

        explain_result = db.command(
            "explain",
            {
                "find": "attendance_logs",
                "filter": query,
                "sort": {"date": -1, "emp_code": 1},
                "skip": skip,
                "limit": page_size,
            },
            verbosity="executionStats",
        )

        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain_result,
        }

   
    if endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(
                status_code=422,
                detail="emp_code and month are required for employee_monthly"
            )

        import re
        import calendar

        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            raise HTTPException(
                status_code=422,
                detail="month must be in YYYY-MM format"
            )

        last_day = calendar.monthrange(
            int(month[:4]), int(month[5:7])
        )[1]

        start_date = f"{month}-01"
        end_date = f"{month}-{last_day:02d}"

        explain_result = db.command(
            "explain",
            {
                "aggregate": "attendance_logs",
                "pipeline": [
                    {
                        "$match": {
                            "emp_code": emp_code,
                            "date": {
                                "$gte": start_date,
                                "$lte": end_date
                            }
                        }
                    },
                    {
                        "$group": {
                            "_id": "$emp_code",
                            "present_days": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$in": [
                                                "$status",
                                                ["PRESENT", "WFH", "ON_DUTY"]
                                            ]
                                        },
                                        {
                                            "$cond": [
                                                {"$eq": ["$half_day", True]},
                                                0.5,
                                                1
                                            ]
                                        },
                                        0
                                    ]
                                }
                            }
                        }
                    }
                ],
                "cursor": {}
            },
            verbosity="executionStats"
        )

        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain_result
        }

    if endpoint == "department_summary":
        import re
        import calendar

        if not month or not re.fullmatch(
            r"\d{4}-(0[1-9]|1[0-2])", month
        ):
            raise HTTPException(
                status_code=422,
                detail="A valid month in YYYY-MM format is required"
            )

        last_day = calendar.monthrange(
            int(month[:4]), int(month[5:7])
        )[1]

        start_date = f"{month}-01"
        end_date = f"{month}-{last_day:02d}"

        employee_match = {"joined_on": {"$lte": end_date}}
        if department:
            employee_match["department"] = department

        pipeline = [
            {"$match": employee_match},
            {
                "$lookup": {
                    "from": "attendance_logs",
                    "let": {"emp": "$emp_code"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$eq": ["$emp_code", "$$emp"]
                                },
                                "date": {
                                    "$gte": start_date,
                                    "$lte": end_date
                                }
                            }
                        },
                        {
                            "$addFields": {
                                "is_working_day": {
                                    "$in": [
                                        {
                                            "$dayOfWeek": {
                                                "$dateFromString": {
                                                    "dateString": "$date"
                                                }
                                            }
                                        },
                                        [2, 3, 4, 5, 6]
                                    ]
                                }
                            }
                        }
                    ],
                    "as": "logs"
                }
            },
            {
                "$unwind": {
                    "path": "$logs",
                    "preserveNullAndEmptyArrays": True
                }
            },
            {
                "$group": {
                    "_id": {
                        "department": "$department",
                        "emp_code": "$emp_code"
                    },
                    "headcount": {"$first": 1},
                    "present_days": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {
                                            "$in": [
                                                "$logs.status",
                                                ["PRESENT", "WFH", "ON_DUTY"]
                                            ]
                                        },
                                        {"$eq": ["$logs.is_working_day", True]}
                                    ]
                                },
                                {
                                    "$cond": [
                                        {"$eq": ["$logs.half_day", True]},
                                        0.5,
                                        1
                                    ]
                                },
                                0
                            ]
                        }
                    },
                    "work_hours_total": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {
                                            "$in": [
                                                "$logs.status",
                                                ["PRESENT", "WFH", "ON_DUTY"]
                                            ]
                                        },
                                        {"$ne": ["$logs.work_hours", None]}
                                    ]
                                },
                                "$logs.work_hours",
                                0
                            ]
                        }
                    },
                    "work_hours_count": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {
                                            "$in": [
                                                "$logs.status",
                                                ["PRESENT", "WFH", "ON_DUTY"]
                                            ]
                                        },
                                        {"$ne": ["$logs.work_hours", None]}
                                    ]
                                },
                                1,
                                0
                            ]
                        }
                    },
                    "late_count": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$gt": [
                                        {"$ifNull": ["$logs.late_minutes", 0]},
                                        0
                                    ]
                                },
                                1,
                                0
                            ]
                        }
                    },
                    "total_late_minutes": {
                        "$sum": {"$ifNull": ["$logs.late_minutes", 0]}
                    },
                    "leave_count": {
                        "$sum": {
                            "$cond": [
                                {"$eq": ["$logs.status", "LEAVE"]},
                                1,
                                0
                            ]
                        }
                    },
                    "on_duty_count": {
                        "$sum": {
                            "$cond": [
                                {"$eq": ["$logs.status", "ON_DUTY"]},
                                1,
                                0
                            ]
                        }
                    }
                }
            },
            {
                "$group": {
                    "_id": "$_id.department",
                    "headcount": {"$sum": "$headcount"},
                    "present_days": {"$sum": "$present_days"},
                    "work_hours_total": {"$sum": "$work_hours_total"},
                    "work_hours_count": {"$sum": "$work_hours_count"},
                    "late_count": {"$sum": "$late_count"},
                    "total_late_minutes": {"$sum": "$total_late_minutes"},
                    "leave_count": {"$sum": "$leave_count"},
                    "on_duty_count": {"$sum": "$on_duty_count"}
                }
            },
            {"$sort": {"_id": 1}}
        ]

        explain_result = db.command(
            "explain",
            {
                "aggregate": "employees",
                "pipeline": pipeline,
                "cursor": {}
            },
            verbosity="executionStats"
        )

        return {
            "endpoint": endpoint,
            "collection": "employees",
            "explain": explain_result
        }


    if endpoint == "late_leaderboard":
        import re
        import calendar

        if not month or not re.fullmatch(
            r"\d{4}-(0[1-9]|1[0-2])", month
        ):
            raise HTTPException(
                status_code=422,
                detail="A valid month in YYYY-MM format is required"
            )

        last_day = calendar.monthrange(
            int(month[:4]), int(month[5:7])
        )[1]

        pipeline = [
            {
                "$match": {
                    "date": {
                        "$gte": f"{month}-01",
                        "$lte": f"{month}-{last_day:02d}"
                    },
                    "late_minutes": {"$gt": 0}
                }
            },
            {
                "$group": {
                    "_id": "$emp_code",
                    "total_late_minutes": {"$sum": "$late_minutes"},
                    "late_count": {"$sum": 1}
                }
            },
            {
                "$lookup": {
                    "from": "employees",
                    "localField": "_id",
                    "foreignField": "emp_code",
                    "as": "employee"
                }
            },
            {"$unwind": "$employee"}
        ]

        if department:
            pipeline.append({
                "$match": {"employee.department": department}
            })

        pipeline.extend([
            {
                "$setWindowFields": {
                    "sortBy": {"total_late_minutes": -1},
                    "output": {"rank": {"$rank": {}}}
                }
            },
            {"$match": {"rank": {"$lte": limit}}},
            {"$sort": {"total_late_minutes": -1, "_id": 1}}
        ])

        explain_result = db.command(
            "explain",
            {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {}
            },
            verbosity="executionStats"
        )

        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain_result
        }

    
    if endpoint == "department_trend":
        from datetime import date, datetime, time, timedelta, timezone

        start = validate_date(from_date, "from")
        end = validate_date(to_date, "to")

        if not department or not start or not end:
            raise HTTPException(
                status_code=422,
                detail="department, from and to are required"
            )

        start_day = date.fromisoformat(start)
        end_day = date.fromisoformat(end)

        if end_day < start_day:
            raise HTTPException(
                status_code=422,
                detail="'to' cannot be before 'from'"
            )

        if (end_day - start_day).days + 1 > 92:
            raise HTTPException(
                status_code=422,
                detail="Date range cannot exceed 92 days"
            )

        if not db.employees.find_one({"department": department}):
            raise HTTPException(
                status_code=404,
                detail="Unknown department"
            )

        start_dt = datetime.combine(
            start_day, time.min, tzinfo=timezone.utc
        )
        end_dt = datetime.combine(
            end_day, time.min, tzinfo=timezone.utc
        )

        pipeline = [
            {"$match": {"department": department}},
            {
                "$lookup": {
                    "from": "attendance_logs",
                    "let": {"employee_code": "$emp_code"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {
                                            "$eq": [
                                                "$emp_code",
                                                "$$employee_code"
                                            ]
                                        },
                                        {"$gte": ["$date", start]},
                                        {"$lte": ["$date", end]}
                                    ]
                                }
                            }
                        }
                    ],
                    "as": "logs"
                }
            },
            {
                "$unwind": {
                    "path": "$logs",
                    "preserveNullAndEmptyArrays": True
                }
            },
            {
                "$group": {
                    "_id": "$logs.date",
                    "present_count": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$in": [
                                        "$logs.status",
                                        ["PRESENT", "WFH", "ON_DUTY"]
                                    ]
                                },
                                {
                                    "$cond": [
                                        {"$eq": ["$logs.half_day", True]},
                                        0.5,
                                        1
                                    ]
                                },
                                0
                            ]
                        }
                    },
                    "late_count": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$gt": [
                                        {"$ifNull": ["$logs.late_minutes", 0]},
                                        0
                                    ]
                                },
                                1,
                                0
                            ]
                        }
                    }
                }
            },
            {"$match": {"_id": {"$ne": None}}},
            {
                "$project": {
                    "_id": 0,
                    "date": {
                        "$dateFromString": {"dateString": "$_id"}
                    },
                    "present_count": 1,
                    "late_count": 1
                }
            },
            {
                "$densify": {
                    "field": "date",
                    "range": {
                        "step": 1,
                        "unit": "day",
                        "bounds": [start_dt, end_dt + timedelta(days=1)]
                    }
                }
            },
            {
                "$set": {
                    "present_count": {"$ifNull": ["$present_count", 0]},
                    "late_count": {"$ifNull": ["$late_count", 0]}
                }
            },
            {
                "$set": {
                    "date_string": {
                        "$dateToString": {
                            "format": "%Y-%m-%d",
                            "date": "$date"
                        }
                    },
                    "weekday": {"$dayOfWeek": "$date"}
                }
            },
            {
                "$lookup": {
                    "from": "employees",
                    "let": {"current_date": "$date"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq": ["$department", department]},
                                        {
                                            "$lte": [
                                                {
                                                    "$convert": {
                                                        "input": "$joined_on",
                                                        "to": "date",
                                                        "onError": None,
                                                        "onNull": None
                                                    }
                                                },
                                                "$$current_date"
                                            ]
                                        }
                                    ]
                                }
                            }
                        },
                        {"$count": "count"}
                    ],
                    "as": "headcount_result"
                }
            },
            {
                "$set": {
                    "headcount": {
                        "$ifNull": [
                            {"$arrayElemAt": ["$headcount_result.count", 0]},
                            0
                        ]
                    },
                    "is_working_day": {
                        "$and": [
                            {"$gte": ["$weekday", 2]},
                            {"$lte": ["$weekday", 6]}
                        ]
                    }
                }
            },
            {
                "$set": {
                    "attendance_rate": {
                        "$cond": [
                            {
                                "$and": [
                                    "$is_working_day",
                                    {"$gt": ["$headcount", 0]}
                                ]
                            },
                            {
                                "$round": [
                                    {
                                        "$divide": [
                                            "$present_count",
                                            "$headcount"
                                        ]
                                    },
                                    4
                                ]
                            },
                            None
                        ]
                    }
                }
            },
            {
                "$setWindowFields": {
                    "sortBy": {"date": 1},
                    "output": {
                        "moving_avg_7d": {
                            "$avg": "$attendance_rate",
                            "window": {"documents": [-6, 0]}
                        }
                    }
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "date": "$date_string",
                    "is_working_day": 1,
                    "headcount": 1,
                    "present_count": 1,
                    "late_count": 1,
                    "attendance_rate": 1,
                    "moving_avg_7d": {
                        "$round": ["$moving_avg_7d", 4]
                    }
                }
            },
            {"$sort": {"date": 1}}
        ]

        explain_result = db.command(
            "explain",
            {
                "aggregate": "employees",
                "pipeline": pipeline,
                "cursor": {}
            },
            verbosity="executionStats"
        )

        return {
            "endpoint": endpoint,
            "collection": "employees",
            "explain": explain_result
        }

    raise HTTPException(
        status_code=422,
        detail="Unsupported endpoint name"
    )

    









# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #

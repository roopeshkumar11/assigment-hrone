import os
import decimal
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Union
import calendar

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Path
from pydantic import BaseModel, Field
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"), tz_aware=True)
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")

@app.on_event("startup")
def startup_db_client():
    db.employees.create_index([("emp_code", ASCENDING)], unique=True)
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING)], unique=True)
    db.attendance_logs.create_index([("date", DESCENDING), ("emp_code", ASCENDING)])
    db.employees.create_index([("department", ASCENDING)])
    # Optimization index for late_leaderboard and trend
    db.attendance_logs.create_index([("date", ASCENDING), ("late_minutes", ASCENDING)])

def parse_time(time_str: str):
    h, m = map(int, time_str.split(":"))
    return h, m

def get_attendance_date(punch_in_ms: int, shift_start: str, shift_end: str) -> str:
    dt_utc = datetime.fromtimestamp(punch_in_ms / 1000, tz=timezone.utc)
    dt_ist = dt_utc.astimezone(IST)
    date_ist = dt_ist.date()
    if shift_end <= shift_start:
        punch_time_str = dt_ist.strftime("%H:%M:%S")
        if punch_time_str < shift_end + ":00":
            date_ist -= timedelta(days=1)
    return date_ist.isoformat()

def compute_late_minutes(punch_in_ms: int, shift_start: str, attendance_date: str) -> int:
    dt_utc = datetime.fromtimestamp(punch_in_ms / 1000, tz=timezone.utc)
    dt_ist = dt_utc.astimezone(IST)
    h, m = parse_time(shift_start)
    shift_start_dt = datetime.fromisoformat(attendance_date).replace(hour=h, minute=m, tzinfo=IST)
    delta_seconds = (dt_ist - shift_start_dt).total_seconds()
    if delta_seconds > 10 * 60:
        return int(delta_seconds // 60)
    return 0

def compute_overtime(punch_out_ms: int, shift_end: str, attendance_date: str, shift_start: str) -> int:
    dt_utc = datetime.fromtimestamp(punch_out_ms / 1000, tz=timezone.utc)
    dt_ist = dt_utc.astimezone(IST)
    h, m = parse_time(shift_end)
    shift_end_date = datetime.fromisoformat(attendance_date)
    if shift_end <= shift_start:
        shift_end_date += timedelta(days=1)
    shift_end_dt = shift_end_date.replace(hour=h, minute=m, tzinfo=IST)
    delta_seconds = (dt_ist - shift_end_dt).total_seconds()
    delta_minutes = int(delta_seconds // 60)
    if delta_minutes >= 30:
        return delta_minutes
    return 0

def compute_work_hours(punch_in_ms: int, punch_out_ms: int) -> float:
    delta_seconds = (punch_out_ms - punch_in_ms) / 1000
    hours = decimal.Decimal(str(delta_seconds)) / decimal.Decimal("3600")
    return float(hours.quantize(decimal.Decimal("0.01"), rounding=decimal.ROUND_HALF_UP))

def compute_half_day(work_hours: float) -> bool:
    return work_hours < 4.50

def truncate_ms(ms: int) -> int:
    return (ms // 1000) * 1000

def ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(truncate_ms(ms) / 1000, tz=timezone.utc)

def serialize_doc(doc: dict) -> dict:
    if doc is None: return None
    doc.pop("_id", None)
    if doc.get("punch_in"):
        doc["punch_in"] = int(doc["punch_in"].timestamp() * 1000)
    if doc.get("punch_out"):
        doc["punch_out"] = int(doc["punch_out"].timestamp() * 1000)
    if doc.get("created_at"):
        doc["created_at"] = int(doc["created_at"].timestamp() * 1000)
    if "history" in doc:
        for h in doc["history"]:
            if h.get("at"):
                h["at"] = int(h["at"].timestamp() * 1000)
            if "changes" in h:
                for k in ["punch_in", "punch_out"]:
                    if k in h["changes"]:
                        if h["changes"][k].get("from"):
                            h["changes"][k]["from"] = int(h["changes"][k]["from"].timestamp() * 1000)
                        if h["changes"][k].get("to"):
                            h["changes"][k]["to"] = int(h["changes"][k]["to"].timestamp() * 1000)
    return doc

class EmployeeIn(BaseModel):
    emp_code: str = Field(..., pattern=r"^EMP\d{4,6}$")
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(..., min_length=1, max_length=50)
    shift_start: str = Field("09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field("18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: str

class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(None, ge=100000000000, le=4102444800000)
    status: str = Field("PRESENT", pattern="^(PRESENT|WFH|ON_DUTY)$")

class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(None, ge=100000000000, le=4102444800000)

class RegularizeIn(BaseModel):
    status: Optional[str] = Field(None, pattern="^(PRESENT|ABSENT|LEAVE|WFH|ON_DUTY)$")
    punch_in: Optional[int] = Field(None, ge=100000000000, le=4102444800000)
    punch_out: Optional[int] = Field(None, ge=100000000000, le=4102444800000)
    reason: str = Field(..., min_length=5, max_length=200)
    regularized_by: str = Field(..., min_length=1, max_length=50)

@app.get("/health")
def health():
    try:
        client.admin.command('ping')
        return {"status": "ok"}
    except:
        raise HTTPException(503, "Database unavailable")

@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if body.shift_start == body.shift_end:
        raise HTTPException(422, "shift_start and shift_end must differ")
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    return serialize_doc(doc)

@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    q = {}
    if department:
        q["department"] = department
    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)
    items = list(db.employees.find(q).sort("emp_code", ASCENDING).skip(skip).limit(page_size))
    return {"items": [serialize_doc(item) for item in items], "total": total, "page": page, "page_size": page_size}

@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")
    
    punched_at_ms = body.punched_at if body.punched_at else int(datetime.now(timezone.utc).timestamp() * 1000)
    punched_at_ms = truncate_ms(punched_at_ms)
    
    d = get_attendance_date(punched_at_ms, emp["shift_start"], emp["shift_end"])
    
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ms_to_dt(punched_at_ms),
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(punched_at_ms, emp["shift_start"], d),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")
        
    return serialize_doc(doc)

@app.post("/attendance/punch-out", status_code=200)
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")
        
    punched_at_ms = body.punched_at if body.punched_at else int(datetime.now(timezone.utc).timestamp() * 1000)
    punched_at_ms = truncate_ms(punched_at_ms)
    punched_at_dt = ms_to_dt(punched_at_ms)
    
    log = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$lte": punched_at_dt}},
        sort=[("punch_in", DESCENDING)]
    )
    
    if not log:
        raise HTTPException(404, "No punch-in found")
        
    if log.get("punch_out") is not None:
        raise HTTPException(409, "already punched out")
        
    punch_in_ms = int(log["punch_in"].timestamp() * 1000)
    if punched_at_ms <= punch_in_ms:
        raise HTTPException(422, "punched_at must be after punch_in")
    if punched_at_ms - punch_in_ms > 24 * 3600 * 1000:
        raise HTTPException(422, "punched_at cannot be more than 24 hours after punch_in")
        
    work_hours = compute_work_hours(punch_in_ms, punched_at_ms)
    overtime_minutes = compute_overtime(punched_at_ms, emp["shift_end"], log["date"], emp["shift_start"])
    half_day = compute_half_day(work_hours)
    
    update = {
        "$set": {
            "punch_out": punched_at_dt,
            "work_hours": work_hours,
            "overtime_minutes": overtime_minutes,
            "half_day": half_day
        }
    }
    
    res = db.attendance_logs.find_one_and_update(
        {"_id": log["_id"], "punch_out": None},
        update,
        return_document=True
    )
    if not res:
        raise HTTPException(409, "already punched out")
        
    return serialize_doc(res)

@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        if date_from and date_to and date_from > date_to:
            raise HTTPException(422, "date_from > date_to")
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status
        
    skip = (page - 1) * page_size
    total = db.attendance_logs.count_documents(q)
    docs = list(db.attendance_logs.find(q).sort([("date", DESCENDING), ("emp_code", ASCENDING)]).skip(skip).limit(page_size))
    
    return {"items": [serialize_doc(d) for d in docs], "total": total, "page": page, "page_size": page_size}

@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str, body: RegularizeIn):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")
        
    log = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not log:
        raise HTTPException(404, "Record not found")
        
    new_status = body.status if body.status else log["status"]
    
    if new_status in ["ABSENT", "LEAVE"]:
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(422, "Cannot supply punch times for ABSENT/LEAVE")
            
    new_punch_in_ms = body.punch_in if body.punch_in is not None else (int(log["punch_in"].timestamp() * 1000) if log.get("punch_in") else None)
    new_punch_out_ms = body.punch_out if body.punch_out is not None else (int(log["punch_out"].timestamp() * 1000) if log.get("punch_out") else None)
    
    if new_status in ["PRESENT", "WFH", "ON_DUTY"]:
        if not new_punch_in_ms:
            raise HTTPException(422, "Presence status requires a punch_in")
            
        new_punch_in_ms = truncate_ms(new_punch_in_ms)
        if new_punch_out_ms:
            new_punch_out_ms = truncate_ms(new_punch_out_ms)
            if new_punch_out_ms <= new_punch_in_ms:
                raise HTTPException(422, "punch_out must be after punch_in")
            if new_punch_out_ms - new_punch_in_ms > 24 * 3600 * 1000:
                raise HTTPException(422, "punch_out cannot be more than 24 hours after punch_in")
                
        computed_date = get_attendance_date(new_punch_in_ms, emp["shift_start"], emp["shift_end"])
        if computed_date != date:
            raise HTTPException(422, "punch_in falls on a different attendance date")
            
    if new_status in ["ABSENT", "LEAVE"]:
        new_punch_in_dt = None
        new_punch_out_dt = None
        new_work_hours = None
        new_late_minutes = 0
        new_overtime_minutes = 0
        new_half_day = False
    else:
        new_punch_in_dt = ms_to_dt(new_punch_in_ms)
        new_punch_out_dt = ms_to_dt(new_punch_out_ms) if new_punch_out_ms else None
        new_late_minutes = compute_late_minutes(new_punch_in_ms, emp["shift_start"], date)
        if new_punch_out_ms:
            new_work_hours = compute_work_hours(new_punch_in_ms, new_punch_out_ms)
            new_overtime_minutes = compute_overtime(new_punch_out_ms, emp["shift_end"], date, emp["shift_start"])
            new_half_day = compute_half_day(new_work_hours)
        else:
            new_work_hours = None
            new_overtime_minutes = 0
            new_half_day = False
            
    changes = {}
    current_punch_in = log.get("punch_in")
    current_punch_out = log.get("punch_out")
    
    if log["status"] != new_status:
        changes["status"] = {"from": log["status"], "to": new_status}
    if current_punch_in != new_punch_in_dt:
        changes["punch_in"] = {"from": current_punch_in, "to": new_punch_in_dt}
    if current_punch_out != new_punch_out_dt:
        changes["punch_out"] = {"from": current_punch_out, "to": new_punch_out_dt}
    if log.get("work_hours") != new_work_hours:
        changes["work_hours"] = {"from": log.get("work_hours"), "to": new_work_hours}
    if log.get("late_minutes", 0) != new_late_minutes:
        changes["late_minutes"] = {"from": log.get("late_minutes", 0), "to": new_late_minutes}
    if log.get("overtime_minutes", 0) != new_overtime_minutes:
        changes["overtime_minutes"] = {"from": log.get("overtime_minutes", 0), "to": new_overtime_minutes}
    if log.get("half_day", False) != new_half_day:
        changes["half_day"] = {"from": log.get("half_day", False), "to": new_half_day}
        
    if not changes:
        raise HTTPException(422, "No changes detected")
        
    history_entry = {
        "at": datetime.now(timezone.utc),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes
    }
    
    update = {
        "$set": {
            "status": new_status,
            "punch_in": new_punch_in_dt,
            "punch_out": new_punch_out_dt,
            "work_hours": new_work_hours,
            "late_minutes": new_late_minutes,
            "overtime_minutes": new_overtime_minutes,
            "half_day": new_half_day
        },
        "$push": {"history": history_entry}
    }
    
    current_history_len = len(log.get("history", []))
    
    res = db.attendance_logs.find_one_and_update(
        {"_id": log["_id"], "history": {"$size": current_history_len}},
        update,
        return_document=True
    )
    if not res:
        raise HTTPException(409, "Concurrent modification detected")
        
    return serialize_doc(res)

def get_working_days(month: str, joined_on: str) -> int:
    y, m = map(int, month.split("-"))
    _, days_in_month = calendar.monthrange(y, m)
    start_date = max(f"{month}-01", joined_on)
    end_date = f"{month}-{days_in_month:02d}"
    if start_date > end_date:
        return 0
    start_dt = datetime.fromisoformat(start_date)
    end_dt = datetime.fromisoformat(end_date)
    working_days = 0
    curr = start_dt
    while curr <= end_dt:
        if curr.weekday() < 5:
            working_days += 1
        curr += timedelta(days=1)
    return working_days

def get_month_end(month: str) -> str:
    y, m = map(int, month.split("-"))
    _, d = calendar.monthrange(y, m)
    return f"{month}-{d:02d}"

def _build_employee_monthly_pipeline(emp_code: str, month: str):
    return [
        {"$match": {
            "emp_code": emp_code,
            "date": {"$gte": f"{month}-01", "$lte": f"{month}-31"}
        }},
        {"$group": {
            "_id": None,
            "present_days_whole": {
                "$sum": {
                    "$cond": [
                        {"$and": [
                            {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$eq": ["$half_day", False]},
                            {"$in": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, [2, 3, 4, 5, 6]]}
                        ]},
                        1, 0
                    ]
                }
            },
            "present_days_half": {
                "$sum": {
                    "$cond": [
                        {"$and": [
                            {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$eq": ["$half_day", True]},
                            {"$in": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, [2, 3, 4, 5, 6]]}
                        ]},
                        0.5, 0
                    ]
                }
            },
            "leave_days": {
                "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
            },
            "late_count": {
                "$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}
            },
            "total_late_minutes": {
                "$sum": {"$ifNull": ["$late_minutes", 0]}
            },
            "total_overtime_minutes": {
                "$sum": {"$ifNull": ["$overtime_minutes", 0]}
            }
        }}
    ]

@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str = Query(..., pattern=r'^\d{4}-(0[1-9]|1[0-2])$')):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")
        
    working_days = get_working_days(month, emp["joined_on"])
    pipeline = _build_employee_monthly_pipeline(emp_code, month)
    res = list(db.attendance_logs.aggregate(pipeline))
    
    if res:
        data = res[0]
        present_days = data["present_days_whole"] + data["present_days_half"]
        leave_days = data["leave_days"]
        late_count = data["late_count"]
        total_late = data["total_late_minutes"]
        total_ot = data["total_overtime_minutes"]
    else:
        present_days = 0.0
        leave_days = 0
        late_count = 0
        total_late = 0
        total_ot = 0
        
    attendance_pct = None
    if working_days > 0:
        pct = (present_days / working_days) * 100
        attendance_pct = float(decimal.Decimal(str(pct)).quantize(decimal.Decimal("0.01"), rounding=decimal.ROUND_HALF_UP))
        
    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": float(present_days),
        "leave_days": leave_days,
        "late_count": late_count,
        "total_late_minutes": total_late,
        "total_overtime_minutes": total_ot,
        "attendance_pct": attendance_pct
    }

def _build_department_summary_pipeline(month: str, department: Optional[str] = None):
    month_end = get_month_end(month)
    match_stage = {"joined_on": {"$lte": month_end}}
    if department:
        match_stage["department"] = department
        
    return [
        {"$match": match_stage},
        {"$group": {
            "_id": "$department",
            "headcount": {"$sum": 1},
            "emp_codes": {"$push": "$emp_code"}
        }},
        {"$lookup": {
            "from": "attendance_logs",
            "localField": "emp_codes",
            "foreignField": "emp_code",
            "pipeline": [
                {"$match": {
                    "date": {"$gte": f"{month}-01", "$lte": f"{month}-31"}
                }}
            ],
            "as": "logs"
        }},
        {"$unwind": {
            "path": "$logs",
            "preserveNullAndEmptyArrays": True
        }},
        {"$group": {
            "_id": "$_id",
            "headcount": {"$first": "$headcount"},
            "present_days_whole": {
                "$sum": {
                    "$cond": [
                        {"$and": [
                            {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$eq": ["$logs.half_day", False]},
                            {"$in": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$logs.date"}}}, [2, 3, 4, 5, 6]]}
                        ]},
                        1, 0
                    ]
                }
            },
            "present_days_half": {
                "$sum": {
                    "$cond": [
                        {"$and": [
                            {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$eq": ["$logs.half_day", True]},
                            {"$in": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$logs.date"}}}, [2, 3, 4, 5, 6]]}
                        ]},
                        0.5, 0
                    ]
                }
            },
            "work_hours_sum": {
                "$sum": {
                    "$cond": [
                        {"$and": [
                            {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$ne": ["$logs.work_hours", None]}
                        ]},
                        "$logs.work_hours", 0
                    ]
                }
            },
            "work_hours_count": {
                "$sum": {
                    "$cond": [
                        {"$and": [
                            {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$ne": ["$logs.work_hours", None]}
                        ]},
                        1, 0
                    ]
                }
            },
            "late_count": {
                "$sum": {"$cond": [{"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]}, 1, 0]}
            },
            "total_late_minutes": {
                "$sum": {"$ifNull": ["$logs.late_minutes", 0]}
            },
            "leave_count": {
                "$sum": {"$cond": [{"$eq": ["$logs.status", "LEAVE"]}, 1, 0]}
            },
            "on_duty_count": {
                "$sum": {"$cond": [{"$eq": ["$logs.status", "ON_DUTY"]}, 1, 0]}
            }
        }},
        {"$sort": {"_id": 1}}
    ]

@app.get("/analytics/departments/summary")
def department_summary(month: str = Query(..., pattern=r'^\d{4}-(0[1-9]|1[0-2])$'), department: Optional[str] = None):
    pipeline = _build_department_summary_pipeline(month, department)
    res = list(db.employees.aggregate(pipeline))
    items = []
    for d in res:
        avg_work_hours = None
        if d["work_hours_count"] > 0:
            avg = d["work_hours_sum"] / d["work_hours_count"]
            avg_work_hours = float(decimal.Decimal(str(avg)).quantize(decimal.Decimal("0.01"), rounding=decimal.ROUND_HALF_UP))
            
        items.append({
            "department": d["_id"],
            "headcount": d["headcount"],
            "present_days": float(d["present_days_whole"] + d["present_days_half"]),
            "avg_work_hours": avg_work_hours,
            "late_count": d["late_count"],
            "total_late_minutes": d["total_late_minutes"],
            "leave_count": d["leave_count"],
            "on_duty_count": d["on_duty_count"]
        })
        
    return {"month": month, "items": items}

def _build_late_leaderboard_pipeline(month: str, limit: int, department: Optional[str] = None):
    pipeline = [
        {"$match": {
            "date": {"$gte": f"{month}-01", "$lte": f"{month}-31"},
            "late_minutes": {"$gt": 0}
        }},
        {"$group": {
            "_id": "$emp_code",
            "total_late_minutes": {"$sum": "$late_minutes"},
            "late_count": {"$sum": 1}
        }},
        {"$lookup": {
            "from": "employees",
            "localField": "_id",
            "foreignField": "emp_code",
            "as": "emp"
        }},
        {"$unwind": "$emp"}
    ]
    if department:
        pipeline.append({"$match": {"emp.department": department}})
        
    pipeline.extend([
        {"$setWindowFields": {
            "sortBy": {"total_late_minutes": -1},
            "output": {
                "rank": {
                    "$rank": {}
                }
            }
        }},
        {"$match": {"rank": {"$lte": limit}}},
        {"$sort": {"rank": 1, "_id": 1}}
    ])
    return pipeline

@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(..., pattern=r'^\d{4}-(0[1-9]|1[0-2])$'),
    limit: int = Query(10, ge=1, le=50),
    department: Optional[str] = None
):
    pipeline = _build_late_leaderboard_pipeline(month, limit, department)
    res = list(db.attendance_logs.aggregate(pipeline))
    items = []
    for d in res:
        items.append({
            "rank": d["rank"],
            "emp_code": d["_id"],
            "name": d["emp"]["name"],
            "department": d["emp"]["department"],
            "total_late_minutes": d["total_late_minutes"],
            "late_count": d["late_count"]
        })
    return {"month": month, "items": items}

def _build_department_trend_pipeline(department: str, from_date: str, to_date: str):
    return [
        {"$match": {"department": department}},
        {"$lookup": {
            "from": "attendance_logs",
            "localField": "emp_code",
            "foreignField": "emp_code",
            "pipeline": [
                {"$match": {
                    "date": {"$gte": from_date, "$lte": to_date}
                }}
            ],
            "as": "logs"
        }},
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {"$group": {
            "_id": "$logs.date",
            "present_count_whole": {
                "$sum": {
                    "$cond": [
                        {"$and": [
                            {"$ne": ["$logs.status", None]},
                            {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$eq": [{"$ifNull": ["$logs.half_day", False]}, False]}
                        ]},
                        1, 0
                    ]
                }
            },
            "present_count_half": {
                "$sum": {
                    "$cond": [
                        {"$and": [
                            {"$ne": ["$logs.status", None]},
                            {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$eq": [{"$ifNull": ["$logs.half_day", False]}, True]}
                        ]},
                        0.5, 0
                    ]
                }
            },
            "late_count": {
                "$sum": {"$cond": [{"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]}, 1, 0]}
            }
        }},
        {"$match": {"_id": {"$ne": None}}},
        {"$project": {
            "date": "$_id",
            "date_dt": {"$dateFromString": {"dateString": "$_id"}},
            "present_count": {"$add": ["$present_count_whole", "$present_count_half"]},
            "late_count": 1,
            "_id": 0
        }},
        {"$densify": {
            "field": "date_dt",
            "range": {
                "step": 1,
                "unit": "day",
                "bounds": [
                    datetime.fromisoformat(from_date),
                    datetime.fromisoformat(to_date) + timedelta(days=1)
                ]
            }
        }},
        {"$addFields": {
            "date": {
                "$cond": [
                    {"$not": ["$date"]},
                    {"$dateToString": {"format": "%Y-%m-%d", "date": "$date_dt"}},
                    "$date"
                ]
            },
            "present_count": {"$ifNull": ["$present_count", 0]},
            "late_count": {"$ifNull": ["$late_count", 0]},
            "is_working_day": {
                "$in": [{"$dayOfWeek": "$date_dt"}, [2, 3, 4, 5, 6]]
            }
        }},
        {"$lookup": {
            "from": "employees",
            "pipeline": [
                {"$match": {"department": department}}
            ],
            "as": "all_emps"
        }},
        {"$addFields": {
            "headcount": {
                "$size": {
                    "$filter": {
                        "input": "$all_emps",
                        "as": "emp",
                        "cond": {"$lte": ["$$emp.joined_on", "$date"]}
                    }
                }
            }
        }},
        {"$addFields": {
            "attendance_rate": {
                "$cond": [
                    {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
                    {"$divide": ["$present_count", "$headcount"]},
                    None
                ]
            }
        }},
        {"$sort": {"date": 1}},
        {"$setWindowFields": {
            "sortBy": {"date": 1},
            "output": {
                "moving_avg_7d": {
                    "$avg": "$attendance_rate",
                    "window": {
                        "documents": [-6, "current"]
                    }
                }
            }
        }},
        {"$project": {
            "date": 1,
            "is_working_day": 1,
            "headcount": 1,
            "present_count": 1,
            "late_count": 1,
            "attendance_rate": 1,
            "moving_avg_7d": 1,
            "_id": 0
        }}
    ]

@app.get("/analytics/departments/{department}/trend")
def department_trend(department: str, from_date: str = Query(..., alias="from"), to_date: str = Query(..., alias="to")):
    if from_date > to_date:
        raise HTTPException(422, "from > to")
        
    d1 = datetime.fromisoformat(from_date)
    d2 = datetime.fromisoformat(to_date)
    if (d2 - d1).days > 92:
        raise HTTPException(422, "Range exceeds 92 days")
        
    emp_count = db.employees.count_documents({"department": department})
    if emp_count == 0:
        raise HTTPException(404, "Unknown department")
        
    pipeline = _build_department_trend_pipeline(department, from_date, to_date)
    res = list(db.employees.aggregate(pipeline))
    
    for r in res:
        if r.get("attendance_rate") is not None:
            r["attendance_rate"] = float(decimal.Decimal(str(r["attendance_rate"])).quantize(decimal.Decimal("0.0001"), rounding=decimal.ROUND_HALF_UP))
        if r.get("moving_avg_7d") is not None:
            r["moving_avg_7d"] = float(decimal.Decimal(str(r["moving_avg_7d"])).quantize(decimal.Decimal("0.0001"), rounding=decimal.ROUND_HALF_UP))
            
    return {"department": department, "items": res}

@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str = Path(..., pattern="^(attendance_list|employee_monthly|department_summary|late_leaderboard|department_trend)$"),
    emp_code: Optional[str] = None,
    month: Optional[str] = Query(None, pattern=r'^\d{4}-(0[1-9]|1[0-2])$'),
    department: Optional[str] = None,
    limit: int = Query(10, ge=1, le=50),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    collection = ""
    cmd = {}
    
    if endpoint == "attendance_list":
        collection = "attendance_logs"
        q = {}
        if emp_code: q["emp_code"] = emp_code
        if date_from or date_to:
            q["date"] = {}
            if date_from: q["date"]["$gte"] = date_from
            if date_to: q["date"]["$lte"] = date_to
        if status: q["status"] = status
        skip = (page - 1) * page_size
        cmd = {
            "find": collection,
            "filter": q,
            "sort": {"date": -1, "emp_code": 1},
            "skip": skip,
            "limit": page_size
        }
    elif endpoint == "employee_monthly":
        if not emp_code or not month: raise HTTPException(422, "Missing parameters")
        collection = "attendance_logs"
        pipeline = _build_employee_monthly_pipeline(emp_code, month)
        cmd = {"aggregate": collection, "pipeline": pipeline, "cursor": {}}
    elif endpoint == "department_summary":
        if not month: raise HTTPException(422, "Missing parameters")
        collection = "employees"
        pipeline = _build_department_summary_pipeline(month, department)
        cmd = {"aggregate": collection, "pipeline": pipeline, "cursor": {}}
    elif endpoint == "late_leaderboard":
        if not month: raise HTTPException(422, "Missing parameters")
        collection = "attendance_logs"
        pipeline = _build_late_leaderboard_pipeline(month, limit, department)
        cmd = {"aggregate": collection, "pipeline": pipeline, "cursor": {}}
    elif endpoint == "department_trend":
        if not department or not from_date or not to_date: raise HTTPException(422, "Missing parameters")
        collection = "employees"
        pipeline = _build_department_trend_pipeline(department, from_date, to_date)
        cmd = {"aggregate": collection, "pipeline": pipeline, "cursor": {}}
        
    explain_res = db.command("explain", cmd, verbosity="executionStats")
    return {"endpoint": endpoint, "collection": collection, "explain": explain_res}

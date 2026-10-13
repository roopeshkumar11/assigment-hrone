# HROne Employee Attendance & Analytics API

A high-performance, production-ready Attendance & Analytics REST API built with **FastAPI**, **MongoDB (PyMongo)**, and **Pydantic**. The service handles employee lifecycle management, biometric punch-in/out logging, manual regularization with complete audit trails, and complex aggregation analytics (monthly metrics, departmental summaries, late arrival leaderboards, and 7-day rolling trends).

---

## Table of Contents
1. [Tech Stack & Prerequisites](#tech-stack--prerequisites)
2. [Project Structure](#project-structure)
3. [Setup & Installation](#setup--installation)
4. [Environment Configuration](#environment-configuration)
5. [Database Seeding](#database-seeding)
6. [Running the Application](#running-the-application)
7. [How to Open Swagger UI & Interactive API Docs](#how-to-open-swagger-ui--interactive-api-docs)
8. [Data Conventions & Core Business Rules](#data-conventions--core-business-rules)
9. [Complete API Reference & Endpoints](#complete-api-reference--endpoints)
   - [System Endpoints](#1-system-endpoints)
   - [Employee Management](#2-employee-management)
   - [Attendance Logging & Regularization](#3-attendance-logging--regularization)
   - [Analytics & Reporting](#4-analytics--reporting)
   - [Admin & Query Optimization](#5-admin--query-optimization)
10. [Database Indexes](#database-indexes)

---

## Tech Stack & Prerequisites

- **Python**: Version 3.10+ (Recommended: 3.11)
- **Framework**: [FastAPI](https://fastapi.tiangolo.com/) (>=0.110)
- **ASGI Server**: [Uvicorn](https://www.uvicorn.org/) (>=0.29)
- **Database**: [MongoDB](https://www.mongodb.com/) (v5.0+ or v6.0+ recommended for window functions like `$setWindowFields` and `$densify`)
- **Driver**: [PyMongo](https://pymongo.readthedocs.io/) (>=4.7)
- **Validation**: [Pydantic](https://docs.pydantic.dev/) (>=2.6)

---

## Project Structure

```text
candidate_kit/
├── app/
│   ├── __init__.py
│   └── main.py              # Main FastAPI application, schemas, routes & aggregations
├── sample_data/
│   ├── employees.json        # Sample employee documents
│   └── attendance_logs.json  # Sample attendance documents
├── .env                     # MongoDB connection parameters
├── DATA_MODEL.md            # Database schema and document shape reference
├── DECISIONS.md             # Architectural decisions, index rationale & edge-case handling
├── openapi.yaml             # Complete OpenAPI 3.0.3 contract specification
├── requirements.txt         # Python package dependencies
├── REVIEW.md                # Code review bug-fixes and audit log
├── sample_seed.py           # Database seeding script
└── README.md                # Project documentation & API guide
```

---

## Setup & Installation

### 1. Clone or Open Workspace
Ensure your shell is inside the project root directory:
```bash
cd candidate_kit
```

### 2. Create and Activate Virtual Environment (Recommended)

**On Windows (PowerShell):**
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

**On Linux / macOS:**
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Required Dependencies
```bash
pip install -r requirements.txt
```

---

## Environment Configuration

Configure your MongoDB connection in the `.env` file located in the root directory:

```env
MONGO_URI="mongodb://localhost:27017"
MONGO_DB="attendance_db"
```

> **Note:** Real system environment variables take precedence over the `.env` file. If MongoDB runs on MongoDB Atlas or a remote server, supply the full connection URI (e.g. `mongodb+srv://<user>:<password>@cluster.mongodb.net/`).

---

## Database Seeding

To load initial sample data (6 employees and 9 attendance records) into MongoDB, run:

```bash
python sample_seed.py
```
*(Or use `python3 sample_seed.py` / `python3.11 sample_seed.py` depending on your environment)*

Output:
```text
loaded  6 documents into 'attendance_db.employees'
loaded  9 documents into 'attendance_db.attendance_logs'
```

---

## Running the Application

Start the Uvicorn ASGI server with hot-reload enabled:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Or run via Python module:
```bash
python -m uvicorn app.main:app --port 8000 --reload
```

When started, FastAPI automatically connects to MongoDB and idempotently creates all required database indexes.

---

## How to Open Swagger UI & Interactive API Docs

Once the server is running on port 8000, you can open and test the interactive API documentation directly in your browser:

### 1. Swagger UI (Interactive Testing)
👉 **URL:** [http://localhost:8000/docs](http://localhost:8000/docs)

- **How to use Swagger UI:**
  1. Open [http://localhost:8000/docs](http://localhost:8000/docs) in Chrome, Edge, Firefox, or any browser.
  2. Click on any API endpoint accordion (e.g., `POST /employees` or `POST /attendance/punch-in`).
  3. Click the **"Try it out"** button in the top right of the endpoint card.
  4. Fill in required path parameters, query parameters, or JSON request body.
  5. Click **"Execute"**.
  6. Inspect the live HTTP response code (200, 201, 404, 409, 422), headers, and JSON response body.

### 2. ReDoc (Alternative Clean Documentation View)
👉 **URL:** [http://localhost:8000/redoc](http://localhost:8000/redoc)

- Formatted two-column layout with detailed request models, response types, and schemas.

### 3. OpenAPI Raw Schema (JSON)
👉 **URL:** [http://localhost:8000/openapi.json](http://localhost:8000/openapi.json)

- Raw JSON schema conforming to OpenAPI 3.0, suitable for importing into **Postman**, **Insomnia**, or code generators.

---

## Data Conventions & Core Business Rules

Before calling endpoints, keep these contract rules in mind:

| Rule | Description |
|---|---|
| **Employee ID** | Employees are identified by `emp_code` (e.g., `EMP0001`). MongoDB's internal `_id` is never exposed. |
| **Instants / Timestamps** | Epoch milliseconds (integer count of ms since `1970-01-01T00:00:00Z`). Milliseconds below `100000000000` (11 digits) are rejected with `422`. Timestamps are truncated to whole seconds before evaluation. |
| **Timezone** | Shift times and calendar dates follow **Indian Standard Time (IST, UTC+05:30)**. Dates are `YYYY-MM-DD`, months are `YYYY-MM`, shifts are `HH:MM` (24-hour). |
| **Overnight Shifts** | When `shift_end <= shift_start` (e.g. `22:00` to `06:00`), punch-ins before `shift_end` are attributed to the shift that started the previous calendar day. |
| **Late Rule (R2)** | Grace period is 10 minutes. A punch-in is late only if strictly `> 10:00` after `shift_start`. If late, `late_minutes` is the whole floor minutes elapsed since `shift_start` (e.g., `09:40:01` for a `09:30` shift is 10 minutes late). |
| **Overtime Rule (R3)** | Whole floor minutes between `shift_end` and `punch_out`, counted only when `>= 30` minutes. |
| **Work Hours Rule (R4)** | `(punch_out - punch_in) in seconds / 3600`, rounded to 2 decimals using **Round-Half-Up** arithmetic. |
| **Half-Day Rule (R5)** | When `work_hours < 4.50`, `half_day = true`. Counts as 0.5 of a present day. |
| **Working Days (R7)** | Monday through Friday. If an employee joined mid-month, working days are counted starting from `joined_on`. |
| **Audit History** | Attendance corrections via `PATCH` record an immutable history trail with `{at, by, reason, changes}`. |

---

## Complete API Reference & Endpoints

### 1. System Endpoints

#### `GET /health`
- **Summary**: Readiness probe.
- **Description**: Verifies database connectivity by pinging MongoDB. Returns 200 if MongoDB answers, otherwise 503.
- **Response (200 OK)**:
  ```json
  {
    "status": "ok"
  }
  ```

---

### 2. Employee Management

#### `POST /employees`
- **Summary**: Create a new employee.
- **Status Code**: `201 Created`
- **Request Body**:
  ```json
  {
    "emp_code": "EMP0101",
    "name": "Aarav Sharma",
    "email": "aarav.sharma@example.com",
    "department": "Engineering",
    "shift_start": "09:30",
    "shift_end": "18:30",
    "joined_on": "2026-01-15"
  }
  ```
- **Constraints**:
  - `emp_code`: Matches pattern `^EMP\d{4,6}$`. Enforced unique via MongoDB unique index. Duplicate returns `409 Conflict`.
  - `shift_start` and `shift_end` must differ (cannot be equal).
- **Response (201 Created)**:
  ```json
  {
    "emp_code": "EMP0101",
    "name": "Aarav Sharma",
    "email": "aarav.sharma@example.com",
    "department": "Engineering",
    "shift_start": "09:30",
    "shift_end": "18:30",
    "joined_on": "2026-01-15",
    "created_at": 1775878200000
  }
  ```

#### `GET /employees`
- **Summary**: List employees with pagination and optional department filter.
- **Query Parameters**:
  - `department` (optional, string): Filter by department name.
  - `page` (optional, default `1`, min `1`): Current page number.
  - `page_size` (optional, default `20`, min `1`, max `100`): Results per page.
- **Response (200 OK)**:
  ```json
  {
    "items": [
      {
        "emp_code": "EMP0101",
        "name": "Aarav Sharma",
        "email": "aarav.sharma@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-01-15",
        "created_at": 1775878200000
      }
    ],
    "total": 1,
    "page": 1,
    "page_size": 20
  }
  ```

---

### 3. Attendance Logging & Regularization

#### `POST /attendance/punch-in`
- **Summary**: Record biometric/portal punch-in.
- **Status Code**: `201 Created`
- **Request Body**:
  ```json
  {
    "emp_code": "EMP0101",
    "punched_at": 1775880000000,
    "status": "PRESENT"
  }
  ```
  *(Note: `punched_at` is optional; if omitted, current server time is used. `status` must be `PRESENT`, `WFH`, or `ON_DUTY`).*
- **Behavior**:
  - Automatically derives the IST calendar `date`.
  - Computes `late_minutes` against the employee's `shift_start`.
  - Guaranteed single punch-in per date via compound unique index `(emp_code, date)`. Duplicate punch-in returns `409 Conflict`.
- **Response (201 Created)**:
  ```json
  {
    "emp_code": "EMP0101",
    "date": "2026-04-11",
    "status": "PRESENT",
    "punch_in": 1775880000000,
    "punch_out": null,
    "work_hours": null,
    "late_minutes": 0,
    "overtime_minutes": 0,
    "half_day": false,
    "history": []
  }
  ```

#### `POST /attendance/punch-out`
- **Summary**: Record punch-out for the most recent open punch-in.
- **Status Code**: `200 OK`
- **Request Body**:
  ```json
  {
    "emp_code": "EMP0101",
    "punched_at": 1775914200000
  }
  ```
- **Behavior**:
  - Locates the latest punch-in without a punch-out.
  - Validates `punched_at > punch_in` and `punched_at - punch_in <= 24 hours`.
  - Computes `work_hours`, `overtime_minutes`, and `half_day`.
- **Response (200 OK)**:
  ```json
  {
    "emp_code": "EMP0101",
    "date": "2026-04-11",
    "status": "PRESENT",
    "punch_in": 1775880000000,
    "punch_out": 1775914200000,
    "work_hours": 9.5,
    "late_minutes": 0,
    "overtime_minutes": 60,
    "half_day": false,
    "history": []
  }
  ```

#### `GET /attendance`
- **Summary**: Query attendance logs with filters and pagination.
- **Query Parameters**:
  - `emp_code` (optional, string): Filter by employee code.
  - `date_from` (optional, string `YYYY-MM-DD`): Start date inclusive.
  - `date_to` (optional, string `YYYY-MM-DD`): End date inclusive (`date_from <= date_to`).
  - `status` (optional, string): Filter by status (`PRESENT`, `ABSENT`, `LEAVE`, `WFH`, `ON_DUTY`).
  - `page` (optional, default `1`)
  - `page_size` (optional, default `20`)
- **Response (200 OK)**:
  ```json
  {
    "items": [...],
    "total": 45,
    "page": 1,
    "page_size": 20
  }
  ```

#### `PATCH /attendance/{emp_code}/{date}`
- **Summary**: Regularize / adjust attendance record (HR Correction).
- **Status Code**: `200 OK`
- **Path Parameters**:
  - `emp_code`: Employee code (e.g. `EMP0101`).
  - `date`: Attendance date `YYYY-MM-DD`.
- **Request Body**:
  ```json
  {
    "status": "PRESENT",
    "punch_in": 1775880000000,
    "punch_out": 1775914200000,
    "reason": "Biometric device offline during morning shift",
    "regularized_by": "HR_ADMIN"
  }
  ```
- **Behavior**:
  - Recomputes all derived fields (`late_minutes`, `work_hours`, `overtime_minutes`, `half_day`).
  - Appends an audit entry into `history` containing timestamp `at`, author `by`, `reason`, and a diff of modified fields in `changes`.
- **Response (200 OK)**: Returns updated record with the newly appended history entry.

---

### 4. Analytics & Reporting

#### `GET /analytics/employees/{emp_code}/monthly`
- **Summary**: Monthly attendance metrics for a single employee.
- **Query Parameters**:
  - `month` (required, `YYYY-MM`): e.g. `2026-03`.
- **Response (200 OK)**:
  ```json
  {
    "emp_code": "EMP0101",
    "month": "2026-03",
    "working_days": 22,
    "present_days": 20.5,
    "leave_days": 1,
    "late_count": 2,
    "total_late_minutes": 35,
    "total_overtime_minutes": 120,
    "attendance_pct": 93.18
  }
  ```

#### `GET /analytics/departments/summary`
- **Summary**: Departmental headcount and monthly attendance roll-up.
- **Query Parameters**:
  - `month` (required, `YYYY-MM`): Target month.
  - `department` (optional, string): Filter to a specific department.
- **Response (200 OK)**: Array of department summaries:
  ```json
  [
    {
      "department": "Engineering",
      "month": "2026-03",
      "headcount": 12,
      "present_days": 230.5,
      "avg_work_hours": 8.75,
      "late_count": 15,
      "total_late_minutes": 240,
      "leave_count": 8,
      "on_duty_count": 3
    }
  ]
  ```

#### `GET /analytics/leaderboard/late`
- **Summary**: Top late employees for a month with dense competition ranking.
- **Query Parameters**:
  - `month` (required, `YYYY-MM`)
  - `limit` (optional, default `10`, range `1-50`)
  - `department` (optional, string)
- **Response (200 OK)**:
  ```json
  {
    "month": "2026-03",
    "items": [
      {
        "rank": 1,
        "emp_code": "EMP0003",
        "name": "Karan Mehra",
        "department": "Sales",
        "total_late_minutes": 145,
        "late_count": 5
      }
    ]
  }
  ```

#### `GET /analytics/departments/{department}/trend`
- **Summary**: Daily attendance rate and 7-day rolling moving average for a department.
- **Path Parameters**:
  - `department`: Department name (e.g., `Engineering`).
- **Query Parameters**:
  - `from` (required, `YYYY-MM-DD`): Start date.
  - `to` (required, `YYYY-MM-DD`): End date (max 92 days span).
- **Response (200 OK)**:
  ```json
  {
    "department": "Engineering",
    "items": [
      {
        "date": "2026-03-01",
        "is_working_day": false,
        "headcount": 12,
        "present_count": 0.0,
        "late_count": 0,
        "attendance_rate": null,
        "moving_avg_7d": 0.9167
      }
    ]
  }
  ```

---

### 5. Admin & Query Optimization

#### `GET /admin/explain/{endpoint}`
- **Summary**: Returns MongoDB `executionStats` query plan for analytics pipelines.
- **Path Parameters**:
  - `endpoint`: One of `attendance_list`, `employee_monthly`, `department_summary`, `late_leaderboard`, `department_trend`.
- **Query Parameters**: Supports the same query parameters as the target endpoint.
- **Response (200 OK)**:
  ```json
  {
    "endpoint": "late_leaderboard",
    "collection": "attendance_logs",
    "explain": {
      "stages": [...],
      "serverInfo": {...}
    }
  }
  ```

---

## Database Indexes

Indexes are automatically ensured on application startup:

1. **`employees.emp_code`** `(ASCENDING, UNIQUE)`:
   - Enforces unique employee codes and provides $O(1)$ lookup performance.
2. **`attendance_logs.(emp_code, date)`** `(ASCENDING, UNIQUE)`:
   - Enforces exactly one record per employee per day and eliminates race conditions during concurrent punch-ins.
3. **`attendance_logs.(date DESCENDING, emp_code ASCENDING)`**:
   - Powers the paginated `GET /attendance` query with zero in-memory sorting overhead.
4. **`employees.department`** `(ASCENDING)`:
   - Accelerates department filtering and `$lookup` aggregations.
5. **`attendance_logs.(date ASCENDING, late_minutes ASCENDING)`**:
   - Optimizes date filtering and pipeline execution for the late leaderboard and trend queries.

---

## Testing & Verifying
Run the following cURL commands in your terminal or via Swagger UI to verify:

```bash
# 1. Health probe
curl -X GET http://localhost:8000/health

# 2. List employees
curl -X GET "http://localhost:8000/employees?page=1&page_size=10"

# 3. Monthly analytics
curl -X GET "http://localhost:8000/analytics/employees/EMP0001/monthly?month=2026-03"
```

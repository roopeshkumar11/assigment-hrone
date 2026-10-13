# HROne Employee Attendance & Analytics API

## How to run

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Set environment variables (or rely on `.env` if developing locally):
   ```
   MONGO_URI="mongodb://localhost:27017"
   MONGO_DB="attendance_db"
   ```
3. Start the application:
   ```bash
   uvicorn app.main:app --port 8000
   ```

## Notes
- All endpoints, aggregations, and indices required by the contract have been implemented in `app/main.py`.
- MongoDB indexes are created idempotently on startup.
- Review findings are in `REVIEW.md` and decisions are documented in `DECISIONS.md`.

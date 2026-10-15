# Employee Attendance & Analytics API

## Project Overview

This project implements the HROne Software Engineer Trainee assignment: an Employee Attendance & Analytics API built with FastAPI and MongoDB.

The API manages employee attendance, calculates attendance metrics, supports attendance corrections with audit history, and provides monthly and department-level analytics.

## Candidate Kit

Read the assignment materials in this order:

1. `PROBLEM_STATEMENT.docx` — assignment requirements and evaluation criteria.
2. `openapi.yaml` — API contract and business rules R1–R10.
3. `DATA_MODEL.md` — MongoDB collections, field types, and sample documents.
4. `app/main.py` — application implementation.
5. `REVIEW.md` and `DECISIONS.md` — review findings and technical decisions.

The `sample_data/` directory contains sample documents in MongoDB Extended JSON format. `sample_seed.py` loads the sample data into MongoDB.

## Implemented Features

- Employee creation and paginated listing.
- Attendance punch-in and punch-out.
- Late arrival, work-hours, overtime, and half-day calculations.
- Attendance filtering and pagination.
- Attendance regularization with audit history.
- Monthly employee attendance analytics.
- Department attendance summaries.
- Late-comer leaderboard with competition ranking.
- Department trends with date gap filling and a seven-day moving average.
- MongoDB explain endpoints for query-plan inspection.

## Tech Stack

- Python 3.13
- FastAPI
- PyMongo
- MongoDB
- Uvicorn
- python-dotenv

## Local Setup

### 1. Create a virtual environment

Run these commands from the repository root:

```bash
python -m venv .venv
```

### 2. Activate the environment and install dependencies

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. Configure environment variables

Create a local `.env` file in the repository root:

```env
MONGO_URI=mongodb://127.0.0.1:27017
MONGO_DB=attendance_db
```

Ensure MongoDB is running locally before starting the application.

### 4. Load sample data (optional)

```powershell
python sample_seed.py
```

**Note:** The seed script clears existing documents in the `employees` and `attendance_logs` collections before inserting the sample data. Do not run it if you need to preserve existing records.

### 5. Start the API

```powershell
python -m uvicorn app.main:app --reload --port 8000
```

Open Swagger UI at:

[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

Use Swagger UI to inspect and test the available endpoints.

## Database and Configuration

The application reads `MONGO_URI` and `MONGO_DB` from environment variables. Required MongoDB indexes are created when the application starts.

The database contains two main collections:

- `employees` — employee details, departments, shift timings, and joining dates.
- `attendance_logs` — daily attendance, punch timestamps, calculated metrics, and regularization history.

See `DATA_MODEL.md` for the detailed schema.

## API Capabilities

The API includes endpoints for:

- Employee management.
- Attendance listing, punch-in, punch-out, and regularization.
- Employee monthly attendance analytics.
- Department attendance summaries.
- Late-comer leaderboard.
- Department attendance trends.
- Query execution-plan inspection.

See the interactive Swagger documentation at `/docs` for endpoint paths, parameters, and request schemas.

## Testing

The endpoints were tested locally through Swagger UI. The tested scenarios included attendance filtering and pagination, punch-in and punch-out, attendance regularization, monthly analytics, department summaries, leaderboard ranking, department trends, and MongoDB query-plan inspection.

Testing with the supplied sample data does not guarantee every possible grader dataset or edge case has been verified.

## Documentation

- `DATA_MODEL.md` — database structure, field types, and sample documents.
- `DECISIONS.md` — indexing, concurrency, ranking, headcount, and scaling decisions.
- `REVIEW.md` — review findings, defects addressed, testing, and remaining limitations.

## Submission Checklist

Before submission, confirm that the repository includes the required application code, dependency file, documentation, and sample data.

Do not commit `.env`, credentials, `.venv`, or database dumps. Submit the Git repository URL according to the HROne assignment instructions.

## Known Limitations

Document any requirements or edge cases that remain incomplete or unverified. Do not claim that an untested requirement has been fully verified.
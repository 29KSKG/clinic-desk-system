# 🩺 ClinicDesk

Conflict-free appointment booking for a multi-doctor clinic front desk.

A doctor can never be double-booked, two patients can never take the same slot, and
cancellations are charged by one consistent rule: **free if cancelled at least 24 hours
before the appointment, a flat fee if later** (both values are configurable).

| Layer | Choice |
|---|---|
| API | FastAPI (Python 3.10+) |
| DB | SQLite via SQLAlchemy ORM (swap `DATABASE_URL` for Postgres) |
| UI | Streamlit |
| Auth | JWT (PyJWT) + PBKDF2-SHA256 password hashing |
| Tests | pytest + FastAPI TestClient |

---

## 1. Setup

```bash
git clone <your-repo-url>
cd clinic-desk

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
cp .env.example .env               # optional — defaults work as-is
```

## 2. Run

Two processes. **Terminal 1 — API:**

```bash
uvicorn backend.main:app --reload --port 8000
python -m backend.seed             # optional demo data (run once, in another shell)
```

**Terminal 2 — UI:**

```bash
streamlit run frontend/app.py --server.port 8501
```

| What | Where |
|---|---|
| Front-desk UI + landing page | http://localhost:8501 |
| Top-bar navigation | Home and Sign in when signed out; the eight desk screens once signed in |
| Swagger / OpenAPI docs | http://localhost:8000/docs |
| Health check | http://localhost:8000/api/health |

Demo login after seeding: **desk@clinic.com / desk1234**

### GitHub Codespaces

Codespaces forwards ports automatically. Set the port **8000** visibility to *Public*, then
point Streamlit at the forwarded API URL before starting it:

```bash
export API_BASE_URL="https://<your-codespace>-8000.app.github.dev"
streamlit run frontend/app.py --server.port 8501
```

## 3. Configuration

All optional — every key has a default (see `.env.example`).

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./clinicdesk.db` | Any SQLAlchemy URL |
| `SECRET_KEY` | `dev-secret-change-me` | JWT signing key |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `720` | Token lifetime |
| `FREE_CANCEL_HOURS` | `24` | Notice needed for a free cancellation |
| `LATE_CANCEL_FEE` | `300` | Flat late-cancellation fee |
| `NO_SHOW_FEE` | `500` | Charged when the desk marks a no-show |
| `CURRENCY` | `INR` | Display currency |
| `API_BASE_URL` | `http://localhost:8000` | Where the Streamlit UI finds the API |
| `MORNING_REMINDER_HOUR` | `8` | Hour (0–23) the T1 reminder job fires on each simulated day |
| `NO_SHOW_GRACE_MINUTES` | `30` | Minutes past start before T2 auto-marks a no-show |

## 4. API endpoints

All routes except `/`, `/api/health` and `/api/auth/*` require
`Authorization: Bearer <token>`.

### Auth
| Method | Path | Purpose |
|---|---|---|
| POST | `/api/auth/register` | Create a front-desk account |
| POST | `/api/auth/login` | Exchange email + password for a JWT |
| GET | `/api/auth/check-email?email=` | Does an account exist? (drives the sign-in ↔ register redirect) |
| GET | `/api/auth/me` | Current user |

### Doctors
| Method | Path | Purpose |
|---|---|---|
| POST | `/api/doctors` | Add a doctor (working hours, slot length) |
| GET | `/api/doctors` | List / search — `q`, `active_only`, `page`, `page_size`, `sort_by`, `order` |
| GET | `/api/doctors/{id}` | One doctor |
| PATCH | `/api/doctors/{id}` | Update details or deactivate |
| GET | `/api/doctors/{id}/day?date=YYYY-MM-DD` | **Doctor's day view** — booked, cancelled, free slots, utilisation |
| GET | `/api/doctors/{id}/availability?date=&duration_minutes=` | Bookable slot starts |

### Patients
| Method | Path | Purpose |
|---|---|---|
| POST | `/api/patients` | Register a patient (phone is unique) |
| GET | `/api/patients` | List / search — `q`, `page`, `page_size`, `sort_by`, `order` |
| GET | `/api/patients/{id}` | One patient |
| PATCH | `/api/patients/{id}` | Update details |

### Appointments
| Method | Path | Purpose |
|---|---|---|
| POST | `/api/appointments` | Book — returns **409** on any overlap |
| GET | `/api/appointments` | **Find by patient name / phone / doctor** — `q`, `doctor_id`, `patient_id`, `status`, `date_from`, `date_to`, `page`, `page_size`, `sort_by`, `order` |
| GET | `/api/appointments/{id}` | One appointment |
| GET | `/api/appointments/{id}/cancellation-quote` | Fee preview **before** cancelling |
| POST | `/api/appointments/{id}/cancel` | Cancel; applies free or late fee automatically |
| POST | `/api/appointments/{id}/complete` | Mark completed |
| POST | `/api/appointments/{id}/no-show` | Mark no-show (applies no-show fee) |
| PATCH | `/api/appointments/{id}/reschedule` | **T6** — move it to a new time; same doctor and patient always, overlap re-checked against the new window |

### Clock & notifications (T1, T2)
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/clock` | Current clinic time, and whether it's simulated |
| POST | `/api/clock` | Move the clock, then run due jobs — see below |
| GET | `/api/outbox` | What the (mocked) Notification Service has sent — `patient_id`, `appointment_id`, `kind`, `page`, `page_size`, `sort_by`, `order` |

`POST /api/clock` body — send **one** of these (an empty body just re-runs due jobs against the current time):

```json
{"now": "2026-09-18T08:05:00"}      // set the clock to an exact time
{"advance_minutes": 45}             // move forward relative to the current clock
{"advance_hours": 24}
{"reset": true}                     // drop back to the real wall clock
```

Response:
```json
{"now":"2026-09-18T08:05:00","is_simulated":true,
 "no_shows_marked":[14,15],"reminders_sent":[41,42,43]}
```

Every POST re-runs, in order:
1. **T2 — no-show sweep**: any `booked` appointment more than `NO_SHOW_GRACE_MINUTES`
   (default 30) past its start is marked `no_show` and charged `NO_SHOW_FEE`.
2. **T1 — morning reminders**: once the clock reaches `MORNING_REMINDER_HOUR` (default 8)
   on a given simulated day, every patient with a `booked` appointment that day is sent a
   reminder via the Notification Service — logged to `outbox` and readable at
   `GET /api/outbox`. Runs once per simulated day and once per appointment, so repeated
   clock advances never double-send.

Everything else in the API — booking-in-the-past checks, cancellation notice, the
doctor's day view, today's stats — reads "now" from this same clock, so advancing it
moves the whole system, not just these two jobs.

### Search & meta
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/search?q=` | Global search across patients, doctors and appointments |
| GET | `/api/stats/today` | Desk dashboard counters (respects the simulated day) |
| GET | `/api/policy` | Current cancellation policy |
| GET | `/api/health` | Liveness |

### Example

```bash
TOKEN=$(curl -s -X POST localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"desk@clinic.com","password":"desk1234"}' | python -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

curl -s -X POST localhost:8000/api/appointments \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"doctor_id":1,"patient_id":1,"start_at":"2026-09-18T10:00:00","duration_minutes":30}'
```

A clash comes back as:

```json
{"detail":{"error":"doctor_double_booked",
           "message":"Dr. Asha Menon already has an appointment from 10:00 to 10:30 on 18 Sep 2026.",
           "conflicting_appointment_id":7,"conflicting_patient":"Ananya Rao"}}
```

### Pagination & sorting

Every list endpoint returns the same envelope:

```json
{"items":[...],"total":42,"page":2,"page_size":10,"total_pages":5,
 "sort_by":"start_at","order":"desc"}
```

Sort fields are whitelisted per resource (appointments: `start_at`, `created_at`, `status`, `id`).

## 5. Tests

```bash
pytest -q
```

`tests/test_rules.py` covers the pure rules (overlap maths, working hours, fee cut-off,
slot generation); `tests/test_api.py` drives the real API end to end — booking, every kind
of clash, cancellation fees, search, pagination, and the three twists: a reschedule onto an
occupied slot is refused with the doctor/patient unchanged (T6), the no-show sweep is
checked at 29 and 31 minutes past start (T2), and the morning reminder job is checked
before, at, and after its trigger hour with a duplicate-send check via `/api/outbox` (T1).

## 6. Debugging

| Symptom | Fix |
|---|---|
| UI says *Cannot reach the API* | The API isn't running, or `API_BASE_URL` is wrong. Check `curl localhost:8000/api/health`. In Codespaces make port 8000 public. |
| *Session expired* | JWT expired or `SECRET_KEY` changed — sign in again. |
| `ModuleNotFoundError: backend` | Run uvicorn/pytest from the **repo root**, not from inside `backend/`. |
| Booking rejected with 422 | Outside working hours, in the past, or duration outside 5–240 minutes — the message says which. |
| Booking rejected as "in the past" but the time looks fine | The clinic's clock is probably still simulated from testing T1/T2. Check `GET /api/clock` — if `is_simulated` is `true`, call `POST /api/clock` with `{"reset": true}` (or hit **Reset to real time** on the Clock & jobs page) to return to the real wall clock. |
| Want a clean slate | Stop the server, delete `clinicdesk.db*`, restart, re-run `python -m backend.seed`. |
| Inspect the data | `sqlite3 clinicdesk.db "select id,doctor_id,start_at,end_at,status from appointments;"` |
| Trace a request | Uvicorn logs every call; add `--log-level debug` for SQL-level detail, or set `echo=True` on the engine in `backend/database.py`. |

## 7. UI behaviour

* **Top navigation bar**, not a sidebar. Signed out you see **Home** and **Sign in** only;
  the eight desk screens appear the moment you authenticate.
* **Gated routing** — any attempt to land on a desk page while signed out is bounced to the
  sign-in screen with an explanation, so there is no way to reach a page whose API calls
  would 401.
* **Sign in ↔ register redirect** — if the email isn't in our records the app switches to the
  register form with the address already filled in; if it *is* already registered, the
  register form bounces back to sign-in the same way. A successful registration signs you
  straight in rather than making you fill a second form.
* **Error handling at three levels** — every API call is wrapped (timeout, connection
  refused, validation errors, 409 conflicts, expired tokens each get their own message); every
  form validates before it submits; and the router catches any unexpected exception so the
  desk sees a friendly message with the traceback tucked into an expander instead of a red
  Streamlit crash.
* **Session expiry** is detected on any 401 and clears the token so the next action asks you
  to sign in again.
* Pagination has real previous/next controls wired to the API envelope, statuses render as
  colour-coded badges, the doctor's day shows a utilisation bar and free slots as chips, and
  cancellation requires ticking that the patient was told about the fee.
* **Clock & jobs page** — advances the simulated clock (+30 min, +1 hour, +1 day, jump to
  tomorrow 8 AM, or an exact date/time), shows what the last advance triggered, and lists
  the Notification Service's outbox. This is the UI for exercising T1 and T2 without
  waiting in real time.

## 8. Project layout

```
backend/
  config.py       settings from environment
  database.py     engine, session, SQLite pragmas (BEGIN IMMEDIATE, foreign_keys)
  models.py       User, Doctor, Patient, Appointment + indexes
  schemas.py      Pydantic request/response models
  scheduling.py   pure rules: overlap, working hours, cancellation fee, free slots
  security.py     password hashing + JWT
  main.py         all endpoints
  seed.py         demo data
frontend/app.py   Streamlit UI (top navbar, gated routing, 9 screens)
.streamlit/config.toml  theme
tests/            unit + API tests
```
"""End-to-end API tests against a throwaway SQLite file."""
import os
import pathlib

os.environ["DATABASE_URL"] = "sqlite:///./test_clinicdesk.db"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.database import Base, engine  # noqa: E402
from backend.main import app  # noqa: E402

from datetime import datetime, timedelta  # noqa: E402


@pytest.fixture(scope="module")
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)
    pathlib.Path("test_clinicdesk.db").unlink(missing_ok=True)


@pytest.fixture(scope="module")
def auth(client):
    client.post(
        "/api/auth/register",
        json={"email": "t@t.com", "full_name": "Tester", "password": "secret123"},
    )
    token = client.post(
        "/api/auth/login", json={"email": "t@t.com", "password": "secret123"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def tomorrow_at(hour, minute=0):
    d = datetime.now() + timedelta(days=1)
    return d.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat()


def test_protected_routes_require_a_token(client):
    assert client.get("/api/appointments").status_code == 401


def test_register_rejects_duplicate_email(client, auth):
    r = client.post(
        "/api/auth/register",
        json={"email": "t@t.com", "full_name": "Again", "password": "secret123"},
    )
    assert r.status_code == 409


def test_booking_flow_and_conflicts(client, auth):
    doctor = client.post(
        "/api/doctors",
        json={"name": "Dr. Test", "specialty": "GP", "work_start": "09:00",
              "work_end": "17:00", "slot_minutes": 30},
        headers=auth,
    ).json()
    p1 = client.post(
        "/api/patients", json={"full_name": "Alice A", "phone": "9990000001"}, headers=auth
    ).json()
    p2 = client.post(
        "/api/patients", json={"full_name": "Bob B", "phone": "9990000002"}, headers=auth
    ).json()

    first = client.post(
        "/api/appointments",
        json={"doctor_id": doctor["id"], "patient_id": p1["id"],
              "start_at": tomorrow_at(10), "duration_minutes": 30},
        headers=auth,
    )
    assert first.status_code == 201

    # same doctor, same slot, different patient -> rejected
    clash = client.post(
        "/api/appointments",
        json={"doctor_id": doctor["id"], "patient_id": p2["id"],
              "start_at": tomorrow_at(10), "duration_minutes": 30},
        headers=auth,
    )
    assert clash.status_code == 409
    assert clash.json()["detail"]["error"] == "doctor_double_booked"

    # same doctor, partially overlapping -> rejected
    partial = client.post(
        "/api/appointments",
        json={"doctor_id": doctor["id"], "patient_id": p2["id"],
              "start_at": tomorrow_at(10, 15), "duration_minutes": 30},
        headers=auth,
    )
    assert partial.status_code == 409

    # butting up against the end of the first one -> allowed
    adjacent = client.post(
        "/api/appointments",
        json={"doctor_id": doctor["id"], "patient_id": p2["id"],
              "start_at": tomorrow_at(10, 30), "duration_minutes": 30},
        headers=auth,
    )
    assert adjacent.status_code == 201

    # outside working hours -> rejected
    late = client.post(
        "/api/appointments",
        json={"doctor_id": doctor["id"], "patient_id": p1["id"],
              "start_at": tomorrow_at(20), "duration_minutes": 30},
        headers=auth,
    )
    assert late.status_code == 422

    # a freed slot becomes bookable again
    cancelled = client.post(
        f"/api/appointments/{first.json()['id']}/cancel", json={"reason": "test"}, headers=auth
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["cancellation_type"] == "free"

    rebook = client.post(
        "/api/appointments",
        json={"doctor_id": doctor["id"], "patient_id": p1["id"],
              "start_at": tomorrow_at(10), "duration_minutes": 30},
        headers=auth,
    )
    assert rebook.status_code == 201

    # the same patient cannot be in two places at once
    other_doctor = client.post(
        "/api/doctors", json={"name": "Dr. Other", "work_start": "09:00", "work_end": "17:00"},
        headers=auth,
    ).json()
    double = client.post(
        "/api/appointments",
        json={"doctor_id": other_doctor["id"], "patient_id": p1["id"],
              "start_at": tomorrow_at(10), "duration_minutes": 30},
        headers=auth,
    )
    assert double.status_code == 409
    assert double.json()["detail"]["error"] == "patient_double_booked"


def test_late_cancellation_charges_a_fee(client, auth):
    doctor = client.post(
        "/api/doctors", json={"name": "Dr. Soon", "work_start": "00:00", "work_end": "23:59"},
        headers=auth,
    ).json()
    patient = client.post(
        "/api/patients", json={"full_name": "Carl C", "phone": "9990000003"}, headers=auth
    ).json()
    soon = (datetime.now() + timedelta(hours=2)).replace(second=0, microsecond=0).isoformat()

    appt = client.post(
        "/api/appointments",
        json={"doctor_id": doctor["id"], "patient_id": patient["id"],
              "start_at": soon, "duration_minutes": 30},
        headers=auth,
    ).json()

    quote = client.get(f"/api/appointments/{appt['id']}/cancellation-quote", headers=auth).json()
    assert quote["kind"] == "late" and quote["fee"] > 0

    done = client.post(
        f"/api/appointments/{appt['id']}/cancel", json={"reason": "late"}, headers=auth
    ).json()
    assert done["cancellation_type"] == "late" and done["cancellation_fee"] > 0

    # cancelling twice is refused
    again = client.post(f"/api/appointments/{appt['id']}/cancel", json={}, headers=auth)
    assert again.status_code == 409


def test_search_pagination_and_sorting(client, auth):
    page = client.get(
        "/api/appointments",
        params={"q": "Bob", "page": 1, "page_size": 2, "sort_by": "start_at", "order": "desc"},
        headers=auth,
    ).json()
    assert page["page_size"] == 2 and page["order"] == "desc"
    assert all("Bob" in a["patient_name"] for a in page["items"])

    results = client.get("/api/search", params={"q": "Alice"}, headers=auth).json()
    assert results["patients"][0]["full_name"] == "Alice A"


def test_check_email_routes_new_users_to_register(client, auth):
    known = client.get("/api/auth/check-email", params={"email": "t@t.com"}).json()
    assert known["exists"] is True
    unknown = client.get("/api/auth/check-email", params={"email": "nobody@nowhere.com"}).json()
    assert unknown["exists"] is False


# --------------------------------------------------------------------------
# T6 — reschedule stays conflict-free and keeps the same doctor/patient
# --------------------------------------------------------------------------
def test_reschedule_rechecks_overlap_and_keeps_doctor_and_patient(client, auth):
    doctor = client.post(
        "/api/doctors", json={"name": "Dr. Resched", "work_start": "09:00", "work_end": "17:00"},
        headers=auth,
    ).json()
    p1 = client.post(
        "/api/patients", json={"full_name": "Resched One", "phone": "9991110001"}, headers=auth
    ).json()
    p2 = client.post(
        "/api/patients", json={"full_name": "Resched Two", "phone": "9991110002"}, headers=auth
    ).json()

    a1 = client.post(
        "/api/appointments",
        json={"doctor_id": doctor["id"], "patient_id": p1["id"],
              "start_at": tomorrow_at(10), "duration_minutes": 30},
        headers=auth,
    ).json()
    a2 = client.post(
        "/api/appointments",
        json={"doctor_id": doctor["id"], "patient_id": p2["id"],
              "start_at": tomorrow_at(11), "duration_minutes": 30},
        headers=auth,
    ).json()

    # moving a1 onto a2's slot must be refused
    clash = client.patch(
        f"/api/appointments/{a1['id']}/reschedule",
        json={"start_at": tomorrow_at(11)}, headers=auth,
    )
    assert clash.status_code == 409

    # moving a1 to a free slot succeeds, same doctor and patient
    moved = client.patch(
        f"/api/appointments/{a1['id']}/reschedule",
        json={"start_at": tomorrow_at(13)}, headers=auth,
    )
    assert moved.status_code == 200
    body = moved.json()
    assert body["doctor_id"] == doctor["id"]
    assert body["patient_id"] == p1["id"]

    # the reschedule endpoint doesn't accept a doctor_id — it's ignored/rejected, not honoured
    resp = client.patch(
        f"/api/appointments/{a2['id']}/reschedule",
        json={"start_at": tomorrow_at(14), "doctor_id": 999999}, headers=auth,
    )
    assert resp.status_code == 200
    assert resp.json()["doctor_id"] == doctor["id"]  # unchanged despite the extra field


# --------------------------------------------------------------------------
# T2 — automatic no-show sweep, 30 minutes past start
# --------------------------------------------------------------------------
def test_clock_auto_marks_no_show_after_grace_period(client, auth):
    doctor = client.post(
        "/api/doctors", json={"name": "Dr. Clock", "work_start": "00:00", "work_end": "23:59"},
        headers=auth,
    ).json()
    patient = client.post(
        "/api/patients", json={"full_name": "Noshow Patient", "phone": "9991110003"}, headers=auth
    ).json()

    # put the clock at a fixed, known time so the appointment is safely in the future
    base = client.post("/api/clock", json={"now": tomorrow_at(9)}, headers=auth).json()
    assert base["is_simulated"] is True

    appt = client.post(
        "/api/appointments",
        json={"doctor_id": doctor["id"], "patient_id": patient["id"],
              "start_at": tomorrow_at(9, 30), "duration_minutes": 15},
        headers=auth,
    ).json()

    # advance to 29 minutes past start — still within grace, still booked
    step1 = client.post("/api/clock", json={"now": tomorrow_at(9, 59)}, headers=auth).json()
    assert appt["id"] not in step1["no_shows_marked"]
    still_booked = client.get(f"/api/appointments/{appt['id']}", headers=auth).json()
    assert still_booked["status"] == "booked"

    # advance past the 30-minute grace period — now auto no-show
    step2 = client.post("/api/clock", json={"now": tomorrow_at(10, 1)}, headers=auth).json()
    assert appt["id"] in step2["no_shows_marked"]
    now_no_show = client.get(f"/api/appointments/{appt['id']}", headers=auth).json()
    assert now_no_show["status"] == "no_show"
    assert now_no_show["cancellation_fee"] > 0

    client.post("/api/clock", json={"reset": True}, headers=auth)


# --------------------------------------------------------------------------
# T1 — morning reminders via the Notification Service, checked through /outbox
# --------------------------------------------------------------------------
def test_clock_sends_morning_reminders_once_per_day(client, auth):
    doctor = client.post(
        "/api/doctors", json={"name": "Dr. Morning", "work_start": "00:00", "work_end": "23:59"},
        headers=auth,
    ).json()
    patient = client.post(
        "/api/patients", json={"full_name": "Morning Patient", "phone": "9991110004"}, headers=auth
    ).json()

    # set the clock to 07:00 (before the reminder hour) and book an appointment later that day
    client.post("/api/clock", json={"now": tomorrow_at(7)}, headers=auth)
    appt = client.post(
        "/api/appointments",
        json={"doctor_id": doctor["id"], "patient_id": patient["id"],
              "start_at": tomorrow_at(15), "duration_minutes": 30},
        headers=auth,
    ).json()

    # still 07:00-ish — no reminder yet
    early = client.post("/api/clock", json={"now": tomorrow_at(7, 30)}, headers=auth).json()
    assert early["reminders_sent"] == []

    # cross into the morning reminder hour (default 08:00) — reminder fires
    morning = client.post("/api/clock", json={"now": tomorrow_at(8, 5)}, headers=auth).json()
    assert len(morning["reminders_sent"]) == 1

    outbox = client.get(
        "/api/outbox", params={"appointment_id": appt["id"]}, headers=auth
    ).json()
    assert outbox["total"] == 1
    assert outbox["items"][0]["kind"] == "appointment_reminder"
    assert outbox["items"][0]["patient_name"] == "Morning Patient"

    # advancing again later the same simulated day must NOT double-send
    later = client.post("/api/clock", json={"now": tomorrow_at(9)}, headers=auth).json()
    assert later["reminders_sent"] == []
    outbox_again = client.get(
        "/api/outbox", params={"appointment_id": appt["id"]}, headers=auth
    ).json()
    assert outbox_again["total"] == 1

    client.post("/api/clock", json={"reset": True}, headers=auth)

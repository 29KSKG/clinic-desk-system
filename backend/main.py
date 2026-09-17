"""ClinicDesk API.

Booking invariants enforced here:
1. A doctor can never have two live (booked) appointments that overlap.
2. A patient can never have two live appointments that overlap.
3. Appointments must sit inside the doctor's working hours and cannot be in the past.
4. Cancelling with >= FREE_CANCEL_HOURS notice is free; anything later carries a flat fee.
"""
from datetime import date as date_cls
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from . import clock, jobs, notifications, schemas
from .config import settings
from .database import Base, engine, get_db
from .models import (
    STATUS_BOOKED,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_NO_SHOW,
    Appointment,
    Doctor,
    Outbox,
    Patient,
    User,
)
from .scheduling import (
    cancellation_outcome,
    free_slots,
    normalise,
    within_working_hours,
)
from .security import create_access_token, get_current_user, hash_password, verify_password

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="ClinicDesk API",
    version=settings.VERSION,
    description="Conflict-free appointment booking for a multi-doctor clinic.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def appt_out(a: Appointment) -> schemas.AppointmentOut:
    return schemas.AppointmentOut(
        id=a.id,
        doctor_id=a.doctor_id,
        patient_id=a.patient_id,
        doctor_name=a.doctor.name if a.doctor else None,
        patient_name=a.patient.full_name if a.patient else None,
        patient_phone=a.patient.phone if a.patient else None,
        start_at=a.start_at,
        end_at=a.end_at,
        status=a.status,
        reason=a.reason,
        created_at=a.created_at,
        cancelled_at=a.cancelled_at,
        cancellation_type=a.cancellation_type,
        cancellation_fee=a.cancellation_fee,
        cancellation_reason=a.cancellation_reason,
    )


def paginate(query, page: int, page_size: int):
    total = query.order_by(None).count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    total_pages = max(1, -(-total // page_size))
    return items, total, total_pages


def apply_sort(query, allowed: dict, sort_by: str, order: str):
    column = allowed.get(sort_by) or next(iter(allowed.values()))
    return query.order_by(column.desc() if order == "desc" else column.asc())


def get_doctor_or_404(db: Session, doctor_id: int) -> Doctor:
    doctor = db.get(Doctor, doctor_id)
    if not doctor:
        raise HTTPException(404, "Doctor not found")
    return doctor


def get_patient_or_404(db: Session, patient_id: int) -> Patient:
    patient = db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(404, "Patient not found")
    return patient


def assert_no_clash(
    db: Session,
    doctor_id: int,
    patient_id: int,
    start: datetime,
    end: datetime,
    exclude_id: Optional[int] = None,
) -> None:
    """Reject the booking if it overlaps a live appointment for this doctor or patient.

    Runs inside the request's transaction, which SQLite opened with BEGIN IMMEDIATE,
    so no other writer can slip a conflicting row in between this check and the insert.
    """
    base = db.query(Appointment).filter(
        Appointment.status == STATUS_BOOKED,
        Appointment.start_at < end,
        Appointment.end_at > start,
    )
    if exclude_id is not None:
        base = base.filter(Appointment.id != exclude_id)

    clash = base.filter(Appointment.doctor_id == doctor_id).first()
    if clash:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "error": "doctor_double_booked",
                "message": (
                    f"Dr. {clash.doctor.name} already has an appointment from "
                    f"{clash.start_at:%H:%M} to {clash.end_at:%H:%M} on {clash.start_at:%d %b %Y}."
                ),
                "conflicting_appointment_id": clash.id,
                "conflicting_patient": clash.patient.full_name,
            },
        )

    clash = base.filter(Appointment.patient_id == patient_id).first()
    if clash:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "error": "patient_double_booked",
                "message": (
                    f"{clash.patient.full_name} is already booked with Dr. {clash.doctor.name} "
                    f"from {clash.start_at:%H:%M} to {clash.end_at:%H:%M}."
                ),
                "conflicting_appointment_id": clash.id,
            },
        )


def validate_window(doctor: Doctor, start: datetime, end: datetime, now: datetime) -> None:
    if end <= start:
        raise HTTPException(422, "End time must be after start time")
    minutes = (end - start).total_seconds() / 60
    if not (settings.MIN_DURATION_MIN <= minutes <= settings.MAX_DURATION_MIN):
        raise HTTPException(
            422,
            f"Duration must be between {settings.MIN_DURATION_MIN} and "
            f"{settings.MAX_DURATION_MIN} minutes",
        )
    if start < now:
        raise HTTPException(422, "Cannot book an appointment in the past")
    if not doctor.is_active:
        raise HTTPException(422, f"Dr. {doctor.name} is not currently taking appointments")
    if not within_working_hours(doctor.work_start, doctor.work_end, start, end):
        raise HTTPException(
            422,
            f"Dr. {doctor.name} works {doctor.work_start}-{doctor.work_end}; "
            "the appointment must fit inside that window on a single day.",
        )


# --------------------------------------------------------------------------
# meta
# --------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def root():
    return f"""
    <html><head><title>{settings.APP_NAME} API</title>
    <style>body{{font-family:system-ui;margin:60px auto;max-width:640px;line-height:1.6}}</style>
    </head><body>
    <h1>{settings.APP_NAME} API v{settings.VERSION}</h1>
    <p>Conflict-free appointment booking for multi-doctor clinics.</p>
    <p><a href="/docs">Interactive API docs (Swagger)</a> &middot;
       <a href="/redoc">ReDoc</a> &middot;
       <a href="/api/health">Health</a></p>
    <p>The front-desk UI runs separately on Streamlit (default
       <code>http://localhost:8501</code>).</p>
    </body></html>
    """


@app.get("/api/health", tags=["meta"])
def health():
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.VERSION}


@app.get("/api/policy", tags=["meta"])
def policy():
    """The cancellation policy the UI displays to the desk."""
    return {
        "free_cancel_hours": settings.FREE_CANCEL_HOURS,
        "late_cancel_fee": settings.LATE_CANCEL_FEE,
        "no_show_fee": settings.NO_SHOW_FEE,
        "currency": settings.CURRENCY,
    }


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------
@app.post("/api/auth/register", response_model=schemas.UserOut, status_code=201, tags=["auth"])
def register(payload: schemas.UserCreate, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == payload.email.lower()).first():
        raise HTTPException(409, "An account with that email already exists")
    user = User(
        email=payload.email.lower(),
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.post("/api/auth/login", response_model=schemas.TokenOut, tags=["auth"])
def login(payload: schemas.LoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email.lower()).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Incorrect email or password")
    return schemas.TokenOut(
        access_token=create_access_token(user), user=schemas.UserOut.model_validate(user)
    )


@app.get("/api/auth/check-email", tags=["auth"])
def check_email(email: str = Query(..., description="Email to look up"), db: Session = Depends(get_db)):
    """Does an account exist for this email?

    The UI uses this after a failed sign-in to decide where to send the user: to the
    register form (no account yet) or back to sign-in (wrong password). This is a
    staff-only internal tool, so the small account-enumeration surface is an acceptable
    trade for the desk not getting stuck on a dead-end error.
    """
    exists = db.query(User).filter(User.email == email.strip().lower()).first() is not None
    return {"email": email.strip().lower(), "exists": exists}


@app.get("/api/auth/me", response_model=schemas.UserOut, tags=["auth"])
def me(current: User = Depends(get_current_user)):
    return current


# --------------------------------------------------------------------------
# doctors
# --------------------------------------------------------------------------
DOCTOR_SORTS = {
    "name": Doctor.name,
    "specialty": Doctor.specialty,
    "created_at": Doctor.created_at,
    "id": Doctor.id,
}


@app.post("/api/doctors", response_model=schemas.DoctorOut, status_code=201, tags=["doctors"])
def create_doctor(
    payload: schemas.DoctorCreate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    if payload.work_end <= payload.work_start:
        raise HTTPException(422, "Working hours must end after they start")
    doctor = Doctor(**payload.model_dump())
    db.add(doctor)
    db.commit()
    db.refresh(doctor)
    return doctor


@app.get("/api/doctors", response_model=schemas.Page[schemas.DoctorOut], tags=["doctors"])
def list_doctors(
    q: Optional[str] = Query(None, description="Search name, specialty or room"),
    active_only: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=settings.MAX_PAGE_SIZE),
    sort_by: str = Query("name", enum=list(DOCTOR_SORTS)),
    order: str = Query("asc", enum=["asc", "desc"]),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    query = db.query(Doctor)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(Doctor.name.ilike(like), Doctor.specialty.ilike(like), Doctor.room.ilike(like))
        )
    if active_only:
        query = query.filter(Doctor.is_active.is_(True))
    query = apply_sort(query, DOCTOR_SORTS, sort_by, order)
    items, total, total_pages = paginate(query, page, page_size)
    return schemas.Page[schemas.DoctorOut](
        items=[schemas.DoctorOut.model_validate(d) for d in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        sort_by=sort_by,
        order=order,
    )


@app.get("/api/doctors/{doctor_id}", response_model=schemas.DoctorOut, tags=["doctors"])
def get_doctor(doctor_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return get_doctor_or_404(db, doctor_id)


@app.patch("/api/doctors/{doctor_id}", response_model=schemas.DoctorOut, tags=["doctors"])
def update_doctor(
    doctor_id: int,
    payload: schemas.DoctorUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    doctor = get_doctor_or_404(db, doctor_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(doctor, field, value)
    if doctor.work_end <= doctor.work_start:
        raise HTTPException(422, "Working hours must end after they start")
    db.commit()
    db.refresh(doctor)
    return doctor


@app.get("/api/doctors/{doctor_id}/day", response_model=schemas.DayView, tags=["doctors"])
def doctor_day(
    doctor_id: int,
    date: date_cls = Query(..., description="YYYY-MM-DD"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """The whole day for one doctor: what is booked, what was cancelled, what is free."""
    doctor = get_doctor_or_404(db, doctor_id)
    day_start = datetime.combine(date, datetime.min.time())
    day_end = day_start + timedelta(days=1)

    rows = (
        db.query(Appointment)
        .options(joinedload(Appointment.doctor), joinedload(Appointment.patient))
        .filter(
            Appointment.doctor_id == doctor_id,
            Appointment.start_at >= day_start,
            Appointment.start_at < day_end,
        )
        .order_by(Appointment.start_at.asc())
        .all()
    )
    live = [a for a in rows if a.status in (STATUS_BOOKED, STATUS_COMPLETED)]
    cancelled = [a for a in rows if a.status in (STATUS_CANCELLED, STATUS_NO_SHOW)]

    busy = [(a.start_at, a.end_at) for a in live]
    slots = free_slots(
        day=date,
        work_start=doctor.work_start,
        work_end=doctor.work_end,
        slot_minutes=doctor.slot_minutes,
        busy=busy,
        now=clock.now(db) if date == clock.today(db) else None,
    )
    booked_minutes = sum((a.end_at - a.start_at).total_seconds() / 60 for a in live)
    work_minutes = (
        datetime.combine(date, datetime.strptime(doctor.work_end, "%H:%M").time())
        - datetime.combine(date, datetime.strptime(doctor.work_start, "%H:%M").time())
    ).total_seconds() / 60

    return schemas.DayView(
        doctor=schemas.DoctorOut.model_validate(doctor),
        date=date.isoformat(),
        work_start=doctor.work_start,
        work_end=doctor.work_end,
        booked=[appt_out(a) for a in live],
        cancelled=[appt_out(a) for a in cancelled],
        free_slots=[
            schemas.SlotOut(start_at=s, end_at=s + timedelta(minutes=doctor.slot_minutes))
            for s in slots
        ],
        utilisation_percent=round(100 * booked_minutes / work_minutes, 1) if work_minutes else 0.0,
    )


@app.get("/api/doctors/{doctor_id}/availability", response_model=List[schemas.SlotOut], tags=["doctors"])
def doctor_availability(
    doctor_id: int,
    date: date_cls = Query(...),
    duration_minutes: Optional[int] = Query(None, ge=5, le=240),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Bookable slot starts - this is what the booking screen offers the desk."""
    doctor = get_doctor_or_404(db, doctor_id)
    duration = duration_minutes or doctor.slot_minutes
    day_start = datetime.combine(date, datetime.min.time())
    busy = [
        (a.start_at, a.end_at)
        for a in db.query(Appointment)
        .filter(
            Appointment.doctor_id == doctor_id,
            Appointment.status == STATUS_BOOKED,
            Appointment.start_at >= day_start,
            Appointment.start_at < day_start + timedelta(days=1),
        )
        .all()
    ]
    slots = free_slots(
        day=date,
        work_start=doctor.work_start,
        work_end=doctor.work_end,
        slot_minutes=doctor.slot_minutes,
        busy=busy,
        now=clock.now(db) if date == clock.today(db) else None,
        duration_minutes=duration,
    )
    return [
        schemas.SlotOut(start_at=s, end_at=s + timedelta(minutes=duration)) for s in slots
    ]


# --------------------------------------------------------------------------
# patients
# --------------------------------------------------------------------------
PATIENT_SORTS = {
    "full_name": Patient.full_name,
    "created_at": Patient.created_at,
    "phone": Patient.phone,
    "id": Patient.id,
}


@app.post("/api/patients", response_model=schemas.PatientOut, status_code=201, tags=["patients"])
def create_patient(
    payload: schemas.PatientCreate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    if db.query(Patient).filter(Patient.phone == payload.phone).first():
        raise HTTPException(409, "A patient with that phone number already exists")
    patient = Patient(**payload.model_dump())
    db.add(patient)
    db.commit()
    db.refresh(patient)
    return patient


@app.get("/api/patients", response_model=schemas.Page[schemas.PatientOut], tags=["patients"])
def list_patients(
    q: Optional[str] = Query(None, description="Search name, phone or email"),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=settings.MAX_PAGE_SIZE),
    sort_by: str = Query("full_name", enum=list(PATIENT_SORTS)),
    order: str = Query("asc", enum=["asc", "desc"]),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    query = db.query(Patient)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(Patient.full_name.ilike(like), Patient.phone.ilike(like), Patient.email.ilike(like))
        )
    query = apply_sort(query, PATIENT_SORTS, sort_by, order)
    items, total, total_pages = paginate(query, page, page_size)
    return schemas.Page[schemas.PatientOut](
        items=[schemas.PatientOut.model_validate(p) for p in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        sort_by=sort_by,
        order=order,
    )


@app.get("/api/patients/{patient_id}", response_model=schemas.PatientOut, tags=["patients"])
def get_patient(patient_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return get_patient_or_404(db, patient_id)


@app.patch("/api/patients/{patient_id}", response_model=schemas.PatientOut, tags=["patients"])
def update_patient(
    patient_id: int,
    payload: schemas.PatientUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    patient = get_patient_or_404(db, patient_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(patient, field, value)
    db.commit()
    db.refresh(patient)
    return patient


# --------------------------------------------------------------------------
# appointments
# --------------------------------------------------------------------------
APPOINTMENT_SORTS = {
    "start_at": Appointment.start_at,
    "created_at": Appointment.created_at,
    "status": Appointment.status,
    "id": Appointment.id,
}


@app.post("/api/appointments", response_model=schemas.AppointmentOut, status_code=201, tags=["appointments"])
def create_appointment(
    payload: schemas.AppointmentCreate,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
):
    doctor = get_doctor_or_404(db, payload.doctor_id)
    get_patient_or_404(db, payload.patient_id)

    start = normalise(payload.start_at)
    end = start + timedelta(minutes=payload.duration_minutes or doctor.slot_minutes)

    validate_window(doctor, start, end, now=clock.now(db))
    assert_no_clash(db, payload.doctor_id, payload.patient_id, start, end)

    appointment = Appointment(
        doctor_id=payload.doctor_id,
        patient_id=payload.patient_id,
        start_at=start,
        end_at=end,
        reason=payload.reason,
        status=STATUS_BOOKED,
        created_by_id=current.id,
    )
    db.add(appointment)
    try:
        db.commit()
    except IntegrityError:
        # The partial unique index caught a race on the exact same slot.
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "error": "slot_taken",
                "message": "That slot was taken a moment ago. Please pick another one.",
            },
        )
    db.refresh(appointment)
    return appt_out(appointment)


@app.get("/api/appointments", response_model=schemas.Page[schemas.AppointmentOut], tags=["appointments"])
def list_appointments(
    q: Optional[str] = Query(None, description="Search by patient name/phone or doctor name"),
    doctor_id: Optional[int] = None,
    patient_id: Optional[int] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    date_from: Optional[date_cls] = None,
    date_to: Optional[date_cls] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=settings.MAX_PAGE_SIZE),
    sort_by: str = Query("start_at", enum=list(APPOINTMENT_SORTS)),
    order: str = Query("asc", enum=["asc", "desc"]),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Find a patient's appointment by name - the desk's most common lookup."""
    query = (
        db.query(Appointment)
        .join(Patient, Appointment.patient_id == Patient.id)
        .join(Doctor, Appointment.doctor_id == Doctor.id)
        .options(joinedload(Appointment.doctor), joinedload(Appointment.patient))
    )
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(Patient.full_name.ilike(like), Patient.phone.ilike(like), Doctor.name.ilike(like))
        )
    if doctor_id:
        query = query.filter(Appointment.doctor_id == doctor_id)
    if patient_id:
        query = query.filter(Appointment.patient_id == patient_id)
    if status_filter:
        query = query.filter(Appointment.status == status_filter)
    if date_from:
        query = query.filter(Appointment.start_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        query = query.filter(
            Appointment.start_at < datetime.combine(date_to, datetime.min.time()) + timedelta(days=1)
        )

    query = apply_sort(query, APPOINTMENT_SORTS, sort_by, order)
    items, total, total_pages = paginate(query, page, page_size)
    return schemas.Page[schemas.AppointmentOut](
        items=[appt_out(a) for a in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        sort_by=sort_by,
        order=order,
    )


@app.get("/api/appointments/{appointment_id}", response_model=schemas.AppointmentOut, tags=["appointments"])
def get_appointment(
    appointment_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    appointment = db.get(Appointment, appointment_id)
    if not appointment:
        raise HTTPException(404, "Appointment not found")
    return appt_out(appointment)


@app.get(
    "/api/appointments/{appointment_id}/cancellation-quote",
    response_model=schemas.CancellationQuote,
    tags=["appointments"],
)
def cancellation_quote(
    appointment_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    """What the fee *would* be - so the desk can tell the patient before confirming."""
    appointment = db.get(Appointment, appointment_id)
    if not appointment:
        raise HTTPException(404, "Appointment not found")
    outcome = cancellation_outcome(
        appointment.start_at, clock.now(db), settings.FREE_CANCEL_HOURS, settings.LATE_CANCEL_FEE
    )
    return schemas.CancellationQuote(
        appointment_id=appointment.id,
        starts_at=appointment.start_at,
        hours_notice=outcome.hours_notice,
        free_cancel_hours=settings.FREE_CANCEL_HOURS,
        kind=outcome.kind,
        fee=outcome.fee,
        currency=settings.CURRENCY,
    )


@app.post("/api/appointments/{appointment_id}/cancel", response_model=schemas.AppointmentOut, tags=["appointments"])
def cancel_appointment(
    appointment_id: int,
    payload: schemas.CancelIn,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    appointment = db.get(Appointment, appointment_id)
    if not appointment:
        raise HTTPException(404, "Appointment not found")
    if appointment.status != STATUS_BOOKED:
        raise HTTPException(409, f"Appointment is already {appointment.status}")

    now = clock.now(db)
    outcome = cancellation_outcome(
        appointment.start_at, now, settings.FREE_CANCEL_HOURS, settings.LATE_CANCEL_FEE
    )
    appointment.status = STATUS_CANCELLED
    appointment.cancelled_at = now
    appointment.cancellation_type = outcome.kind
    appointment.cancellation_fee = outcome.fee
    appointment.cancellation_reason = payload.reason
    db.commit()
    db.refresh(appointment)
    return appt_out(appointment)


@app.post("/api/appointments/{appointment_id}/complete", response_model=schemas.AppointmentOut, tags=["appointments"])
def complete_appointment(
    appointment_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    appointment = db.get(Appointment, appointment_id)
    if not appointment:
        raise HTTPException(404, "Appointment not found")
    if appointment.status != STATUS_BOOKED:
        raise HTTPException(409, f"Appointment is already {appointment.status}")
    appointment.status = STATUS_COMPLETED
    db.commit()
    db.refresh(appointment)
    return appt_out(appointment)


@app.post("/api/appointments/{appointment_id}/no-show", response_model=schemas.AppointmentOut, tags=["appointments"])
def mark_no_show(
    appointment_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    appointment = db.get(Appointment, appointment_id)
    if not appointment:
        raise HTTPException(404, "Appointment not found")
    if appointment.status != STATUS_BOOKED:
        raise HTTPException(409, f"Appointment is already {appointment.status}")
    appointment.status = STATUS_NO_SHOW
    appointment.cancelled_at = clock.now(db)
    appointment.cancellation_type = "no_show"
    appointment.cancellation_fee = settings.NO_SHOW_FEE
    db.commit()
    db.refresh(appointment)
    return appt_out(appointment)


@app.patch(
    "/api/appointments/{appointment_id}/reschedule",
    response_model=schemas.AppointmentOut,
    tags=["appointments"],
)
def reschedule_appointment(
    appointment_id: int,
    payload: schemas.AppointmentReschedule,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """T6: move an appointment to a new time. The doctor and patient are fixed — only the
    time (and optionally the duration) can change — and the new window is re-checked for
    overlap exactly as a fresh booking would be, with this appointment excluded from its
    own clash search."""
    appointment = db.get(Appointment, appointment_id)
    if not appointment:
        raise HTTPException(404, "Appointment not found")
    if appointment.status != STATUS_BOOKED:
        raise HTTPException(409, f"Cannot reschedule a {appointment.status} appointment")

    doctor = get_doctor_or_404(db, appointment.doctor_id)
    start = normalise(payload.start_at)
    duration = payload.duration_minutes or int(
        (appointment.end_at - appointment.start_at).total_seconds() / 60
    )
    end = start + timedelta(minutes=duration)

    validate_window(doctor, start, end, now=clock.now(db))
    assert_no_clash(db, doctor.id, appointment.patient_id, start, end, exclude_id=appointment.id)

    appointment.start_at = start
    appointment.end_at = end
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, detail={"error": "slot_taken", "message": "That slot was just taken."})
    db.refresh(appointment)
    return appt_out(appointment)


# --------------------------------------------------------------------------
# global search
# --------------------------------------------------------------------------
@app.get("/api/search", response_model=schemas.SearchResults, tags=["search"])
def global_search(
    q: str = Query(..., min_length=1, description="Patient name/phone, doctor name or specialty"),
    limit: int = Query(5, ge=1, le=25),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    like = f"%{q.strip()}%"
    patients = (
        db.query(Patient)
        .filter(or_(Patient.full_name.ilike(like), Patient.phone.ilike(like), Patient.email.ilike(like)))
        .order_by(Patient.full_name.asc())
        .limit(limit)
        .all()
    )
    doctors = (
        db.query(Doctor)
        .filter(or_(Doctor.name.ilike(like), Doctor.specialty.ilike(like)))
        .order_by(Doctor.name.asc())
        .limit(limit)
        .all()
    )
    appointments = (
        db.query(Appointment)
        .join(Patient, Appointment.patient_id == Patient.id)
        .join(Doctor, Appointment.doctor_id == Doctor.id)
        .options(joinedload(Appointment.doctor), joinedload(Appointment.patient))
        .filter(or_(Patient.full_name.ilike(like), Patient.phone.ilike(like), Doctor.name.ilike(like)))
        .order_by(Appointment.start_at.desc())
        .limit(limit)
        .all()
    )
    return schemas.SearchResults(
        query=q,
        patients=[schemas.PatientOut.model_validate(p) for p in patients],
        doctors=[schemas.DoctorOut.model_validate(d) for d in doctors],
        appointments=[appt_out(a) for a in appointments],
    )


# --------------------------------------------------------------------------
# dashboard stats
# --------------------------------------------------------------------------
@app.get("/api/stats/today", tags=["meta"])
def stats_today(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    today = clock.today(db)
    start = datetime.combine(today, datetime.min.time())
    end = start + timedelta(days=1)
    rows = db.query(Appointment).filter(
        Appointment.start_at >= start, Appointment.start_at < end
    ).all()
    return {
        "date": today.isoformat(),
        "booked": sum(1 for a in rows if a.status == STATUS_BOOKED),
        "completed": sum(1 for a in rows if a.status == STATUS_COMPLETED),
        "cancelled": sum(1 for a in rows if a.status == STATUS_CANCELLED),
        "no_show": sum(1 for a in rows if a.status == STATUS_NO_SHOW),
        "late_cancel_fees": round(
            sum(a.cancellation_fee for a in rows if a.cancellation_type == "late"), 2
        ),
        "currency": settings.CURRENCY,
        "doctors_active": db.query(Doctor).filter(Doctor.is_active.is_(True)).count(),
        "patients_total": db.query(Patient).count(),
    }


# --------------------------------------------------------------------------
# clock & notifications  (T1 — morning reminders, T2 — no-show sweep)
# --------------------------------------------------------------------------
@app.get("/api/clock", tags=["clock"])
def get_clock(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    state = clock.get_state(db)
    return {"now": clock.now(db), "is_simulated": state.simulated_now is not None}


@app.post("/api/clock", response_model=schemas.ClockOut, tags=["clock"])
def advance_clock(
    payload: schemas.ClockIn,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Move the clinic's simulated clock, then run whatever is due.

    Exactly one of ``now`` (set absolutely), ``advance_minutes``/``advance_hours``
    (move relative to the current clock), or ``reset`` (drop back to real time) should be
    given; an empty body just re-runs the due jobs against the current time. After moving
    the clock this always runs the no-show sweep (T2), and runs the morning reminder job
    (T1) if the clock has reached MORNING_REMINDER_HOUR on a simulated day it hasn't
    already run for. Check GET /api/outbox afterwards to see what the Notification
    Service sent.
    """
    if payload.reset:
        clock.reset(db)
    elif payload.now is not None:
        clock.set_now(db, normalise(payload.now))
    elif payload.advance_minutes is not None:
        clock.advance(db, minutes=payload.advance_minutes)
    elif payload.advance_hours is not None:
        clock.advance(db, hours=payload.advance_hours)

    no_shows = jobs.run_no_show_sweep(db)
    reminders = jobs.run_morning_reminders(db)

    state = clock.get_state(db)
    return schemas.ClockOut(
        now=clock.now(db),
        is_simulated=state.simulated_now is not None,
        no_shows_marked=[a.id for a in no_shows],
        reminders_sent=[o.id for o in reminders],
    )


OUTBOX_SORTS = {"sent_at": Outbox.sent_at, "kind": Outbox.kind, "id": Outbox.id}


@app.get("/api/outbox", response_model=schemas.Page[schemas.OutboxOut], tags=["clock"])
def list_outbox(
    patient_id: Optional[int] = None,
    appointment_id: Optional[int] = None,
    kind: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=settings.MAX_PAGE_SIZE),
    sort_by: str = Query("sent_at", enum=list(OUTBOX_SORTS)),
    order: str = Query("desc", enum=["asc", "desc"]),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """What the Notification Service has sent — reads T1's morning reminder job's output."""
    query = db.query(Outbox).options(joinedload(Outbox.patient))
    if patient_id:
        query = query.filter(Outbox.patient_id == patient_id)
    if appointment_id:
        query = query.filter(Outbox.appointment_id == appointment_id)
    if kind:
        query = query.filter(Outbox.kind == kind)
    query = apply_sort(query, OUTBOX_SORTS, sort_by, order)
    items, total, total_pages = paginate(query, page, page_size)
    return schemas.Page[schemas.OutboxOut](
        items=[
            schemas.OutboxOut(
                id=o.id,
                patient_id=o.patient_id,
                patient_name=o.patient.full_name if o.patient else None,
                appointment_id=o.appointment_id,
                kind=o.kind,
                channel=o.channel,
                message=o.message,
                sent_at=o.sent_at,
            )
            for o in items
        ],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        sort_by=sort_by,
        order=order,
    )

"""Database schema.

Appointments are stored as a half-open interval [start_at, end_at). Two
appointments overlap iff  a.start < b.end AND a.end > b.start.
All datetimes are naive *clinic-local* times.
"""
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import relationship

from .database import Base

STATUS_BOOKED = "booked"
STATUS_CANCELLED = "cancelled"
STATUS_COMPLETED = "completed"
STATUS_NO_SHOW = "no_show"


class User(Base):
    """A front-desk user. This is who logs in, not the patient."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    full_name = Column(String(120), nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="staff")  # staff | admin
    created_at = Column(DateTime, default=datetime.now, nullable=False)


class Doctor(Base):
    __tablename__ = "doctors"

    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False, index=True)
    specialty = Column(String(120), nullable=False, default="General")
    room = Column(String(40), nullable=True)
    work_start = Column(String(5), nullable=False, default="09:00")  # HH:MM
    work_end = Column(String(5), nullable=False, default="17:00")
    slot_minutes = Column(Integer, nullable=False, default=30)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    appointments = relationship("Appointment", back_populates="doctor")


class Patient(Base):
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True)
    full_name = Column(String(120), nullable=False, index=True)
    phone = Column(String(20), unique=True, nullable=False, index=True)
    email = Column(String(255), nullable=True)
    notes = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    appointments = relationship("Appointment", back_populates="patient")


class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True)
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)

    start_at = Column(DateTime, nullable=False, index=True)
    end_at = Column(DateTime, nullable=False)
    status = Column(String(20), nullable=False, default=STATUS_BOOKED, index=True)
    reason = Column(String(300), nullable=True)

    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    cancelled_at = Column(DateTime, nullable=True)
    cancellation_type = Column(String(20), nullable=True)  # free | late
    cancellation_fee = Column(Float, nullable=False, default=0.0)
    cancellation_reason = Column(String(300), nullable=True)

    doctor = relationship("Doctor", back_populates="appointments")
    patient = relationship("Patient", back_populates="appointments")

    __table_args__ = (
        Index("ix_appt_doctor_window", "doctor_id", "start_at", "end_at"),
        Index("ix_appt_patient_window", "patient_id", "start_at", "end_at"),
        # Second line of defence against a race on the exact same slot: two live
        # appointments can never share (doctor, start_at). Full overlap is checked
        # in application code inside an IMMEDIATE transaction.
        Index(
            "uq_doctor_live_slot",
            "doctor_id",
            "start_at",
            unique=True,
            sqlite_where=text("status = 'booked'"),
            postgresql_where=text("status = 'booked'"),
        ),
        UniqueConstraint("patient_id", "doctor_id", "start_at", name="uq_patient_doctor_slot"),
    )


class SystemState(Base):
    """One-row table holding the clinic's simulated clock.

    When ``simulated_now`` is NULL the system runs on the real wall clock. Setting it via
    POST /api/clock lets scheduled jobs (morning reminders, the no-show sweep) be driven
    deterministically instead of waiting in real time. ``last_reminder_run_date`` stops the
    morning job firing more than once for the same simulated day.
    """

    __tablename__ = "system_state"

    id = Column(Integer, primary_key=True, default=1)
    simulated_now = Column(DateTime, nullable=True)
    last_reminder_run_date = Column(String(10), nullable=True)  # 'YYYY-MM-DD'
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class Outbox(Base):
    """Every message the (mocked) Notification Service has sent.

    There is no real SMS/email provider in this environment, so ``notifications.send``
    plays the role a real integration would sit behind, and this table is the auditable
    record a provider's own dashboard would give you. GET /api/outbox reads it.
    """

    __tablename__ = "outbox"

    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    appointment_id = Column(Integer, ForeignKey("appointments.id"), nullable=True)
    kind = Column(String(40), nullable=False)  # e.g. "appointment_reminder"
    channel = Column(String(20), nullable=False, default="sms")
    message = Column(String(500), nullable=False)
    sent_at = Column(DateTime, nullable=False, index=True)

    patient = relationship("Patient")
    appointment = relationship("Appointment")


"""Pydantic request/response models."""
from datetime import datetime
from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field

T = TypeVar("T")


# ---------- auth ----------
class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=6, max_length=128)
    role: str = Field(default="staff", pattern="^(staff|admin)$")


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: EmailStr
    full_name: str
    role: str
    created_at: datetime


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ---------- doctors ----------
class DoctorCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    specialty: str = "General"
    room: Optional[str] = None
    work_start: str = Field(default="09:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    work_end: str = Field(default="17:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    slot_minutes: int = Field(default=30, ge=5, le=240)


class DoctorUpdate(BaseModel):
    name: Optional[str] = None
    specialty: Optional[str] = None
    room: Optional[str] = None
    work_start: Optional[str] = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    work_end: Optional[str] = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    slot_minutes: Optional[int] = Field(default=None, ge=5, le=240)
    is_active: Optional[bool] = None


class DoctorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    specialty: str
    room: Optional[str]
    work_start: str
    work_end: str
    slot_minutes: int
    is_active: bool


# ---------- patients ----------
class PatientCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=6, max_length=20)
    email: Optional[EmailStr] = None
    notes: Optional[str] = None


class PatientUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    notes: Optional[str] = None


class PatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    full_name: str
    phone: str
    email: Optional[EmailStr]
    notes: Optional[str]
    created_at: datetime


# ---------- appointments ----------
class AppointmentCreate(BaseModel):
    doctor_id: int
    patient_id: int
    start_at: datetime
    duration_minutes: Optional[int] = Field(default=None, ge=5, le=240)
    reason: Optional[str] = Field(default=None, max_length=300)


class AppointmentReschedule(BaseModel):
    """T6: move an appointment to a new time. The patient and doctor never change here —
    only start_at (and optionally duration) — and the new time is re-checked for conflicts
    exactly as a fresh booking would be."""

    start_at: datetime
    duration_minutes: Optional[int] = Field(default=None, ge=5, le=240)


class CancelIn(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=300)


class AppointmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    doctor_id: int
    patient_id: int
    doctor_name: Optional[str] = None
    patient_name: Optional[str] = None
    patient_phone: Optional[str] = None
    start_at: datetime
    end_at: datetime
    status: str
    reason: Optional[str]
    created_at: datetime
    cancelled_at: Optional[datetime]
    cancellation_type: Optional[str]
    cancellation_fee: float
    cancellation_reason: Optional[str]


class CancellationQuote(BaseModel):
    appointment_id: int
    starts_at: datetime
    hours_notice: float
    free_cancel_hours: float
    kind: str
    fee: float
    currency: str


# ---------- shared ----------
class Page(BaseModel, Generic[T]):
    items: List[T]
    total: int
    page: int
    page_size: int
    total_pages: int
    sort_by: Optional[str] = None
    order: Optional[str] = None


class SlotOut(BaseModel):
    start_at: datetime
    end_at: datetime


class DayView(BaseModel):
    doctor: DoctorOut
    date: str
    work_start: str
    work_end: str
    booked: List[AppointmentOut]
    cancelled: List[AppointmentOut]
    free_slots: List[SlotOut]
    utilisation_percent: float


class SearchResults(BaseModel):
    query: str
    patients: List[PatientOut]
    doctors: List[DoctorOut]
    appointments: List[AppointmentOut]


# ---------- clock & notifications (T1 / T2) ----------
class ClockIn(BaseModel):
    """Exactly one of these should be set. All are optional — an empty body just re-runs
    the due jobs against the current time, which is handy for polling."""

    now: Optional[datetime] = Field(default=None, description="Set the clock to this exact time")
    advance_minutes: Optional[int] = Field(default=None, ge=1, le=100_000)
    advance_hours: Optional[float] = Field(default=None, ge=0, le=10_000)
    reset: bool = Field(default=False, description="Drop the override and return to real time")


class ClockOut(BaseModel):
    now: datetime
    is_simulated: bool
    no_shows_marked: List[int]
    reminders_sent: List[int]


class OutboxOut(BaseModel):
    id: int
    patient_id: int
    patient_name: Optional[str] = None
    appointment_id: Optional[int]
    kind: str
    channel: str
    message: str
    sent_at: datetime

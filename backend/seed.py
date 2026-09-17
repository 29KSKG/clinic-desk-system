"""Populate the database with demo data:  python -m backend.seed"""
from datetime import date, datetime, time, timedelta

from .database import Base, SessionLocal, engine
from .models import Appointment, Doctor, Patient, User
from .security import hash_password

DOCTORS = [
    ("Dr. Asha Menon", "General Medicine", "R1", "09:00", "17:00", 30),
    ("Dr. Rahul Verma", "Pediatrics", "R2", "10:00", "18:00", 20),
    ("Dr. Neha Kapoor", "Dermatology", "R3", "09:30", "15:30", 30),
    ("Dr. Imran Sheikh", "Orthopaedics", "R4", "08:00", "14:00", 45),
]

PATIENTS = [
    ("Ananya Rao", "9800000001", "ananya@example.com"),
    ("Vikram Singh", "9800000002", "vikram@example.com"),
    ("Meera Joshi", "9800000003", None),
    ("Karthik Iyer", "9800000004", "karthik@example.com"),
    ("Sana Qureshi", "9800000005", None),
    ("Dev Patel", "9800000006", "dev@example.com"),
    ("Riya Sharma", "9800000007", None),
    ("Tarun Bose", "9800000008", "tarun@example.com"),
    ("Fatima Khan", "9800000009", None),
    ("Joseph Thomas", "9800000010", "joseph@example.com"),
    ("Priya Nair", "9800000011", None),
    ("Aditya Gupta", "9800000012", "aditya@example.com"),
]


def run() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            db.add(
                User(
                    email="desk@clinic.com",
                    full_name="Front Desk",
                    password_hash=hash_password("desk1234"),
                    role="admin",
                )
            )

        if db.query(Doctor).count() == 0:
            for name, spec, room, ws, we, slot in DOCTORS:
                db.add(
                    Doctor(
                        name=name, specialty=spec, room=room,
                        work_start=ws, work_end=we, slot_minutes=slot,
                    )
                )

        if db.query(Patient).count() == 0:
            for full_name, phone, email in PATIENTS:
                db.add(Patient(full_name=full_name, phone=phone, email=email))

        db.commit()

        if db.query(Appointment).count() == 0:
            doctors = db.query(Doctor).order_by(Doctor.id).all()
            patients = db.query(Patient).order_by(Patient.id).all()
            tomorrow = date.today() + timedelta(days=1)
            n = 0
            for d_index, doctor in enumerate(doctors):
                hh, mm = map(int, doctor.work_start.split(":"))
                cursor = datetime.combine(tomorrow, time(hh, mm))
                for _ in range(3):
                    patient = patients[n % len(patients)]
                    db.add(
                        Appointment(
                            doctor_id=doctor.id,
                            patient_id=patient.id,
                            start_at=cursor,
                            end_at=cursor + timedelta(minutes=doctor.slot_minutes),
                            reason="Follow-up" if n % 2 else "Consultation",
                        )
                    )
                    cursor += timedelta(minutes=doctor.slot_minutes * 2)
                    n += 1
            db.commit()

        print("Seeded. Login with  desk@clinic.com / desk1234")
    finally:
        db.close()


if __name__ == "__main__":
    run()

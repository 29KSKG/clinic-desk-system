"""Central configuration. Everything is overridable through environment variables."""
import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    APP_NAME = "ClinicDesk"
    VERSION = "1.0.0"

    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./clinicdesk.db")

    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
    ALGORITHM = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "720"))

    # Cancellation policy
    FREE_CANCEL_HOURS = float(os.getenv("FREE_CANCEL_HOURS", "24"))
    LATE_CANCEL_FEE = float(os.getenv("LATE_CANCEL_FEE", "300"))
    NO_SHOW_FEE = float(os.getenv("NO_SHOW_FEE", "500"))
    CURRENCY = os.getenv("CURRENCY", "INR")

    # Booking guardrails
    MIN_DURATION_MIN = 5
    MAX_DURATION_MIN = 240
    MAX_PAGE_SIZE = 100

    # Scheduled jobs (T1 / T2) — driven by the simulated clock, see backend/clock.py
    MORNING_REMINDER_HOUR = int(os.getenv("MORNING_REMINDER_HOUR", "8"))
    NO_SHOW_GRACE_MINUTES = int(os.getenv("NO_SHOW_GRACE_MINUTES", "30"))


settings = Settings()

"""SQLAlchemy engine/session setup.

Two SQLite pragmas matter for correctness here:

* ``foreign_keys=ON``  - SQLite ignores FK constraints unless asked.
* ``BEGIN IMMEDIATE``  - pysqlite's default is to start transactions lazily, which
  means two concurrent "check the slot, then insert" flows can interleave and both
  win. Emitting BEGIN IMMEDIATE takes the write lock at the *start* of the
  transaction, so the read-then-write in the booking endpoint is atomic.
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings

IS_SQLITE = settings.DATABASE_URL.startswith("sqlite")

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if IS_SQLITE else {},
    future=True,
)

if IS_SQLITE:

    @event.listens_for(engine, "connect")
    def _sqlite_on_connect(dbapi_connection, _record):
        # Disable pysqlite's implicit transaction handling so we control BEGIN.
        dbapi_connection.isolation_level = None
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    @event.listens_for(engine, "begin")
    def _sqlite_begin(conn):
        conn.exec_driver_sql("BEGIN IMMEDIATE")


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

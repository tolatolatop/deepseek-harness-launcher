from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlmodel import Session, SQLModel, create_engine, select

from launcher.models import Platform, User
from launcher.settings import settings

_engine = None


def engine():
    global _engine
    if _engine is None:
        url = settings().database_url
        if url.startswith("sqlite"):
            if url.startswith("sqlite:///") and ":memory:" not in url:
                Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
            _engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

            @event.listens_for(_engine, "connect")
            def sqlite_settings(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA journal_mode=WAL")
        else:
            parsed = make_url(url)
            options = {"pool_pre_ping": True}
            if parsed.get_backend_name() == "mysql":
                if parsed.drivername == "mysql":
                    parsed = parsed.set(drivername="mysql+pymysql")
                parsed = parsed.update_query_dict({"charset": "utf8mb4"})
                # Admission queries must see commits made while waiting for the platform lock.
                options.update(isolation_level="READ COMMITTED", pool_recycle=1800)
            _engine = create_engine(parsed, **options)
    return _engine


def init_db():
    from launcher.security import password_hash

    SQLModel.metadata.create_all(engine())
    with Session(engine()) as db:
        if not db.get(Platform, 1):
            db.add(Platform())
        if not db.exec(select(User).where(User.role == "admin")).first():
            cfg = settings()
            if len(cfg.bootstrap_password) < 12:
                raise RuntimeError("Set LAUNCHER_BOOTSTRAP_PASSWORD to at least 12 characters")
            db.add(
                User(
                    username=cfg.bootstrap_username,
                    display_name="管理员",
                    password_hash=password_hash(cfg.bootstrap_password),
                    role="admin",
                )
            )
        db.commit()


def get_db():
    with Session(engine()) as db:
        yield db

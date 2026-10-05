from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import config

if config.DATABASE_URL.startswith("sqlite"):
    # Make sure the folder for the SQLite file exists.
    db_path = config.DATABASE_URL.split("///", 1)[-1]
    if db_path and db_path != ":memory:":
        from pathlib import Path

        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(config.DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(config.DATABASE_URL, pool_pre_ping=True)

Session = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    from app.models import Base

    Base.metadata.create_all(engine)

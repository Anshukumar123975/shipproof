import os
from datetime import datetime, timezone
from sqlalchemy import create_engine, String, Integer, Float, JSON, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

def now(): return datetime.now(timezone.utc).timestamp()
class Base(DeclarativeBase): pass
engine = create_engine(os.getenv('DATABASE_URL', 'sqlite:////tmp/shipproof-test.db'), pool_pre_ping=True)
Session = sessionmaker(engine, expire_on_commit=False)
class Record(Base):
    __tablename__ = 'records'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String, index=True)
    run_id: Mapped[str] = mapped_column(String, index=True, default='')
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created: Mapped[float] = mapped_column(Float, default=now)
class Run(Base):
    __tablename__ = 'runs'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(String, default='DRAFT', index=True)
    data: Mapped[dict] = mapped_column(JSON)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    created: Mapped[float] = mapped_column(Float, default=now)
    updated: Mapped[float] = mapped_column(Float, default=now)
    lease_until: Mapped[float] = mapped_column(Float, default=0)
    owner: Mapped[str] = mapped_column(String, default='')
class Event(Base):
    __tablename__ = 'events'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
    kind: Mapped[str] = mapped_column(String)
    data: Mapped[dict] = mapped_column(JSON)
    created: Mapped[float] = mapped_column(Float, default=now)
class Setting(Base):
    __tablename__ = 'settings'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text)

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

Base = declarative_base()


def create_db_engine(url: str, echo: bool = False) -> Engine:
    """建立資料庫引擎。"""

    return create_engine(url, echo=echo, future=True)


def create_session_factory(engine: Engine) -> sessionmaker:
    """依據引擎建立 Session Factory。"""

    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


@contextmanager
def session_scope(session_factory: sessionmaker) -> Iterator[Session]:
    """產生具備自動提交／回滾的資料庫交易範圍。"""

    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def upgrade_schema(engine: Engine) -> None:
    """確保資料表包含最新欄位（適用 SQLite）。"""

    with engine.begin() as connection:
        def has_column(table: str, column: str) -> bool:
            result = connection.execute(text(f"PRAGMA table_info({table})"))
            return any(row[1] == column for row in result)

        def add_column(table: str, definition: str) -> None:
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {definition}"))

        if not has_column("simulated_trades", "buy_time_local"):
            add_column("simulated_trades", "buy_time_local TIMESTAMP")
        if not has_column("simulated_trades", "sell_time_local"):
            add_column("simulated_trades", "sell_time_local TIMESTAMP")

        if not has_column("run_history", "executed_at_local"):
            add_column("run_history", "executed_at_local TIMESTAMP")

        if not has_column("signal_summaries", "generated_at_local"):
            add_column("signal_summaries", "generated_at_local TIMESTAMP")

        if not has_column("executed_trades", "integrator_fee_usdc"):
            add_column("executed_trades", "integrator_fee_usdc FLOAT")

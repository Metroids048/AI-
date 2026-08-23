from sqlalchemy import create_engine, inspect

from services.agents.telegram_kol.persistence.models import (
    Base,  # noqa: F401
    TelegramRawMessage,
)


def test_telegram_tables_are_registered_and_raw_revision_is_unique() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    tables = set(inspect(engine).get_table_names())

    assert "telegram_raw_messages" in tables
    assert "telegram_candidate_inbox" in tables
    constraints = {item["name"] for item in inspect(engine).get_unique_constraints("telegram_raw_messages")}
    assert "uq_telegram_raw_message_revision" in constraints
    assert TelegramRawMessage.__tablename__ == "telegram_raw_messages"

import sqlite3

from scripts.audit_execution_cost_data_availability import inspect_database


def test_missing_order_book_is_an_explicit_block(tmp_path) -> None:
    database = tmp_path / "history.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE ohlcv_bars (time TEXT)")
    connection.commit()
    connection.close()

    report = inspect_database(database)

    assert report["read_only"] is True
    assert report["maker_limit_model_status"] == "BLOCKED_NO_HISTORICAL_ORDER_BOOK"

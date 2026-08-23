from datetime import UTC, datetime

from services.agents.telegram_kol.ingestion.collector import TelegramCollector
from services.agents.telegram_kol.ingestion.folder_resolver import resolve_folder_chats
from services.agents.telegram_kol.ingestion.media_store import MediaStore
from services.agents.telegram_kol.ingestion.storage import RawMessageLedger


def test_folder_resolver_discovers_chats_without_hardcoded_names() -> None:
    filters = [
        {"title": "其他", "chat_ids": ["-9"]},
        {"title": "搬运脚本分组", "chat_ids": ["-1", "-2"]},
    ]
    dialogs = [
        {"chat_id": "-1", "title": "飞扬", "chat_type": "channel"},
        {"chat_id": "-2", "title": "军长", "chat_type": "group"},
        {"chat_id": "-9", "title": "无关", "chat_type": "group"},
    ]

    sources = resolve_folder_chats("搬运脚本分组", filters=filters, dialogs=dialogs)

    assert [source["title"] for source in sources] == ["飞扬", "军长"]


def test_raw_ledger_is_append_only_and_deduplicates_same_revision() -> None:
    ledger = RawMessageLedger()
    now = datetime(2026, 8, 23, tzinfo=UTC)

    first = ledger.append(chat_id="-1", message_id=8, revision=0, received_at=now, text="BTC 多")
    duplicate = ledger.append(chat_id="-1", message_id=8, revision=0, received_at=now, text="BTC 多")
    edited = ledger.append(chat_id="-1", message_id=8, revision=1, received_at=now, text="BTC 空")

    assert first.created is True
    assert duplicate.created is False
    assert edited.created is True
    assert [row.revision for row in ledger.list_message("-1", 8)] == [0, 1]


def test_media_store_deduplicates_content(tmp_path) -> None:
    store = MediaStore(tmp_path)

    first = store.put(b"image-bytes", suffix=".jpg")
    second = store.put(b"image-bytes", suffix=".jpg")

    assert first.media_hash == second.media_hash
    assert first.path == second.path
    assert first.path.exists()


def test_collector_persists_before_parsing_and_handles_delete() -> None:
    ledger = RawMessageLedger()
    collector = TelegramCollector(ledger=ledger)
    now = datetime(2026, 8, 23, tzinfo=UTC)

    event = collector.ingest(
        source_id="fei-yang",
        chat_id="-1",
        message_id=10,
        posted_at=now,
        received_at=now,
        text="BTC 多 77000 SL75000 TP79000",
    )
    collector.delete(chat_id="-1", message_id=10, received_at=now)

    assert event is not None
    assert len(ledger.list_message("-1", 10)) == 2
    assert ledger.list_message("-1", 10)[-1].deleted is True
    assert ledger.list_message("-1", 10)[0].source_id == "fei-yang"
    assert ledger.list_message("-1", 10)[0].raw_hash


def test_collector_runs_ocr_only_after_raw_persist() -> None:
    ledger = RawMessageLedger()

    class FakeOcr:
        def extract(self, media_path: str) -> str:
            assert ledger.list_message("-1", 11)
            return "ETH 多 3500 SL3400 TP3700"

    collector = TelegramCollector(ledger=ledger, ocr=FakeOcr())
    event = collector.ingest(
        source_id="fei-yang",
        chat_id="-1",
        message_id=11,
        posted_at=datetime(2026, 8, 23, tzinfo=UTC),
        received_at=datetime(2026, 8, 23, tzinfo=UTC),
        media_path=".local/telegram_kol/media/example.jpg",
    )

    assert event is not None
    assert event.symbol == "ETH/USDT"

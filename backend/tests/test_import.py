import pytest
import json
import uuid
from io import BytesIO
from httpx import AsyncClient
from sqlalchemy import select, delete
from app.models import Card, Deck, DeckCard, ImportJob, ImportRowResult, DataQualityIssue
from app.utils import get_card_fingerprint
from conftest import TestingSessionLocal

pytestmark = pytest.mark.asyncio

async def test_csv_upload_and_preview(client: AsyncClient):
    # Upload a valid CSV file
    csv_data = b"english,chinese,pos\nfatigue,pl,n\nsymptom,sz,n"
    files = {"file": ("test.csv", csv_data, "text/csv")}
    
    res = await client.post("/api/v1/imports/upload", files=files)
    assert res.status_code == 200
    data = res.json()
    assert "import_job_id" in data
    assert data["headers"] == ["english", "chinese", "pos"]
    assert data["suggested_mapping"]["english"] == "english"
    assert data["suggested_mapping"]["chinese_meaning"] == "chinese"
    assert len(data["preview_rows"]) == 2

async def test_csv_upload_bom_handling(client: AsyncClient):
    # UTF-8 BOM prefix: \xef\xbb\xbf
    csv_data = b"\xef\xbb\xbfenglish,chinese,pos\nfatigue,pl,n"
    files = {"file": ("test.csv", csv_data, "text/csv")}
    
    res = await client.post("/api/v1/imports/upload", files=files)
    assert res.status_code == 200
    data = res.json()
    assert data["headers"] == ["english", "chinese", "pos"]


async def test_txt_upload_normalizes_to_english_chinese_preview(client: AsyncClient):
    txt_data = (
        "a board member\t委員會/董事會成員\n"
        "a dedicated and talented team\t專注且具備才能的團隊\n"
        "abandon 放棄\n"
    ).encode("utf-8")
    files = {"file": ("TOEIC-Vocabulary.txt", txt_data, "text/plain")}

    res = await client.post("/api/v1/imports/upload", files=files)
    assert res.status_code == 200
    data = res.json()
    assert data["headers"] == ["english", "chinese"]
    assert data["suggested_mapping"]["english"] == "english"
    assert data["suggested_mapping"]["chinese_meaning"] == "chinese"
    assert data["preview_rows"] == [
        ["a board member", "委員會/董事會成員"],
        ["a dedicated and talented team", "專注且具備才能的團隊"],
        ["abandon", "放棄"],
    ]

    analyze_res = await client.post(
        f"/api/v1/imports/{data['import_job_id']}/analyze",
        json={"field_mapping": {"english": "english", "chinese_meaning": "chinese"}, "deck_selection": "TOEIC"},
    )
    assert analyze_res.status_code == 200
    assert analyze_res.json()["new_cards"] == 3

    commit_res = await client.post(
        f"/api/v1/imports/{data['import_job_id']}/commit",
        json={"idempotency_key": str(uuid.uuid4()), "request_hash": "txt-import"},
    )
    assert commit_res.status_code == 200
    assert commit_res.json()["new_cards"] == 3

async def test_csv_analyze_and_row_classification(client: AsyncClient):
    # 1. Seed existing card
    async with TestingSessionLocal() as session:
        # Clean first
        await session.execute(delete(Card))
        await session.execute(delete(Deck))
        await session.execute(delete(DeckCard))
        
        # Add card "apple" to a deck "Deck A"
        deck_a = Deck(id="deck-a-id", name="Deck A", enabled=True)
        card = Card(
            id="apple-id",
            english="apple",
            english_normalized="apple",
            chinese_meaning="蘋果",
            chinese_normalized="蘋果",
            part_of_speech="noun",
            fingerprint=get_card_fingerprint("apple", "蘋果", "noun"),
            fingerprint_version=1,
            active=True
        )
        session.add(deck_a)
        session.add(card)
        await session.commit()
        
        # Link card to deck
        link = DeckCard(deck_id="deck-a-id", card_id="apple-id")
        session.add(link)
        await session.commit()

    # 2. Upload CSV with duplicate scenarios
    # Row 0: Exact same card, target deck A -> exact_duplicate (action: skip)
    # Row 1: Exact same card, target deck B -> cross_deck_duplicate (action: link)
    # Row 2: Same term "apple", different pos "verb" -> same_term_variant (action: create)
    # Row 3: Same term "apple", same pos "noun", different meaning "手機", no hint -> potential_ambiguity (action: flag_ambiguous)
    # Row 4: Multi meaning "banana", "香蕉，芭蕉" -> multi_meaning_candidate (action: create)
    csv_data = (
        b"word,meaning,part_of_speech,hint,deck\n"
        b"apple,\xE8\x98\x8B\xE6\x9E\x9C,noun,,Deck A\n"
        b"apple,\xE8\x98\x8B\xE6\x9E\x9C,noun,,Deck B\n"
        b"apple,apple-verb,verb,,Deck A\n"
        b"apple,handphone,noun,,Deck A\n"
        b"banana,\xE9\xA6\x99\xE8\x95\x89\xEF\xBC\x8C\xE8\x8A\xAD\xE9\xAE\x8A,noun,,Deck A"
    )
    
    files = {"file": ("test.csv", csv_data, "text/csv")}
    upload_res = await client.post("/api/v1/imports/upload", files=files)
    assert upload_res.status_code == 200
    job_id = upload_res.json()["import_job_id"]
    
    # Analyze mapping
    mapping = {
        "english": "word",
        "chinese_meaning": "meaning",
        "part_of_speech": "part_of_speech",
        "sense_hint": "hint",
        "deck": "deck"
    }
    
    analyze_res = await client.post(
        f"/api/v1/imports/{job_id}/analyze",
        json={"field_mapping": mapping, "deck_selection": "Deck A"}
    )
    assert analyze_res.status_code == 200
    stats = analyze_res.json()
    assert stats["skipped_duplicates"] == 1
    assert stats["linked_existing_cards"] == 1
    assert stats["new_cards"] == 3 # Apple (verb), Apple (ambiguous), Banana (multi)
    
    # 3. Verify row paginated results
    rows_res = await client.get(f"/api/v1/imports/{job_id}/rows?page=1&limit=10")
    assert rows_res.status_code == 200
    rows_data = rows_res.json()
    assert rows_data["total"] == 5
    rows = rows_data["rows"]
    
    assert rows[0]["classification"] == "exact_duplicate"
    assert rows[0]["action"] == "skipped"
    
    assert rows[1]["classification"] == "cross_deck_duplicate"
    assert rows[1]["action"] == "linked"
    
    assert rows[2]["classification"] == "same_term_variant"
    assert rows[2]["action"] == "created"
    
    assert rows[3]["classification"] == "potential_ambiguity"
    assert rows[3]["action"] == "flagged_ambiguous"
    
    assert rows[4]["classification"] == "multi_meaning_candidate"
    assert rows[4]["action"] == "created"
    
    # 4. Commit Import
    idemp_key = str(uuid.uuid4())
    req_hash = "some_request_hash"
    commit_res = await client.post(
        f"/api/v1/imports/{job_id}/commit",
        json={"idempotency_key": idemp_key, "request_hash": req_hash}
    )
    assert commit_res.status_code == 200
    commit_data = commit_res.json()
    assert commit_data["new_cards"] == 3
    assert commit_data["linked_existing_cards"] == 1
    
    # Verify Database State
    async with TestingSessionLocal() as session:
        # Check Decks created
        decks_res = await session.execute(select(Deck))
        decks = decks_res.scalars().all()
        deck_names = {d.name for d in decks}
        assert "Deck A" in deck_names
        assert "Deck B" in deck_names
        
        # Check Cards created
        cards_res = await session.execute(select(Card))
        cards = cards_res.scalars().all()
        assert len(cards) == 4 # apple, apple(verb), apple(handphone), banana
        
        # Apple (handphone) should be active but study ineligible and status ambiguous
        apple_phone = next(c for c in cards if c.chinese_meaning == "handphone")
        assert apple_phone.active is True
        assert apple_phone.study_eligible is False
        assert apple_phone.data_quality_status == "ambiguous"
        
        # Check DataQualityIssue created for Apple (handphone)
        dq_res = await session.execute(select(DataQualityIssue).where(DataQualityIssue.card_id == apple_phone.id))
        dq_issue = dq_res.scalars().first()
        assert dq_issue is not None
        assert dq_issue.issue_type == "potential_ambiguity"
        
    # Test Idempotent Retry
    commit_res_2 = await client.post(
        f"/api/v1/imports/{job_id}/commit",
        json={"idempotency_key": idemp_key, "request_hash": req_hash}
    )
    assert commit_res_2.status_code == 200
    assert commit_res_2.json() == commit_data

async def test_csv_probable_duplicate_conflict_and_fallback_deck(client: AsyncClient):
    # 1. Seed existing card
    async with TestingSessionLocal() as session:
        await session.execute(delete(Card))
        await session.execute(delete(Deck))
        await session.execute(delete(DeckCard))
        
        deck = Deck(id="deck-fruit", name="Fruit Deck", enabled=True)
        card = Card(
            id="banana-id",
            english="banana",
            english_normalized="banana",
            chinese_meaning="香蕉",
            chinese_normalized="香蕉",
            part_of_speech="noun",
            sense_hint="yellow fruit",
            fingerprint=get_card_fingerprint("banana", "香蕉", "noun"),
            fingerprint_version=1,
            active=True
        )
        session.add(deck)
        session.add(card)
        await session.commit()
        
        link = DeckCard(deck_id="deck-fruit", card_id="banana-id")
        session.add(link)
        await session.commit()

    # 2. Upload CSV
    # Row 0: Similar meaning (banana / 香蕉、甘蕉 / noun / yellow fruit) -> probable_duplicate (action: skipped)
    # Row 1: Same English, POS, hint, but conflicting meaning (banana / 芭蕉 / noun / yellow fruit) -> potential_conflict (action: rejected)
    # Row 2: Empty deck -> fallback deck usage
    csv_data = (
        b"word,meaning,pos,hint,deck_col\n"
        b"banana,\xE9\xA6\x99\xE8\x95\x89\xE3\x80\x81\xE7\x94\x98\xE8\x95\x89,noun,yellow fruit,Fruit Deck\n"
        b"banana,\xE8\x8A\xAD\xE9\xAE\x8A,noun,yellow fruit,Fruit Deck\n"
        b"banana,\xE9\xA6\x99\xE8\x95\x89,noun,yellow fruit,\n"
    )
    
    files = {"file": ("test.csv", csv_data, "text/csv")}
    upload_res = await client.post("/api/v1/imports/upload", files=files)
    assert upload_res.status_code == 200
    job_id = upload_res.json()["import_job_id"]
    
    mapping = {
        "english": "word",
        "chinese_meaning": "meaning",
        "part_of_speech": "pos",
        "sense_hint": "hint",
        "deck": "deck_col"
    }
    
    # Empty fallback deck now means the default single vocabulary deck.
    analyze_ok = await client.post(
        f"/api/v1/imports/{job_id}/analyze",
        json={"field_mapping": mapping, "deck_selection": ""}
    )
    assert analyze_ok.status_code == 200
    stats = analyze_ok.json()
    assert stats["conflict_count"] == 1 # potential_conflict
    assert stats["fallback_deck_usage_count"] == 1
    
    # Verify rows
    rows_res = await client.get(f"/api/v1/imports/{job_id}/rows?page=1&limit=10")
    assert rows_res.status_code == 200
    rows = rows_res.json()["rows"]
    
    # Row 0: similar meaning banana -> probable_duplicate -> action skipped
    assert rows[0]["classification"] == "probable_duplicate"
    assert rows[0]["action"] == "skipped"
    
    # Row 1: banana contradiction -> potential_conflict -> action rejected
    assert rows[1]["classification"] == "potential_conflict"
    assert rows[1]["action"] == "rejected"
    
    # Row 2: fallback deck row -> cross_deck_duplicate -> action linked
    assert rows[2]["classification"] == "cross_deck_duplicate"
    assert rows[2]["action"] == "linked"


async def test_import_path_traversal(client: AsyncClient):
    # Test invalid UUID format (should return 400 Bad Request)
    bad_id = "not-a-uuid"
    
    # 1. Analyze
    res1 = await client.post(
        f"/api/v1/imports/{bad_id}/analyze",
        json={"field_mapping": {}, "deck_selection": "Test"}
    )
    assert res1.status_code == 400
    assert "Invalid job ID format" in res1.json()["detail"]

    # 2. Rows
    res2 = await client.get(f"/api/v1/imports/{bad_id}/rows?page=1&limit=10")
    assert res2.status_code == 400
    assert "Invalid job ID format" in res2.json()["detail"]

    # 3. Commit
    res3 = await client.post(
        f"/api/v1/imports/{bad_id}/commit",
        json={"idempotency_key": "some_key", "request_hash": "some_hash"}
    )
    assert res3.status_code == 400
    assert "Invalid job ID format" in res3.json()["detail"]

    # Test explicit path traversal attempt (should return 400 or 404 due to normalization)
    bad_id_traversal = "../../../etc/passwd"
    res_trav = await client.post(
        f"/api/v1/imports/{bad_id_traversal}/analyze",
        json={"field_mapping": {}, "deck_selection": "Test"}
    )
    assert res_trav.status_code in (400, 404)


async def test_import_validation_limits(client: AsyncClient):
    # 1. File size limit test (exceeding 10MB)
    huge_data = b"x" * (10 * 1024 * 1024 + 10)
    files = {"file": ("test.csv", huge_data, "text/csv")}
    res = await client.post("/api/v1/imports/upload", files=files)
    assert res.status_code == 400
    assert "exceeds the 10MB limit" in res.json()["detail"]

    # 2. Row count limit test (>10000 rows)
    too_many_rows = "english,chinese,pos\n" + "\n".join([f"w{i},c{i},n" for i in range(10001)])
    files = {"file": ("test.csv", too_many_rows.encode("utf-8"), "text/csv")}
    res = await client.post("/api/v1/imports/upload", files=files)
    assert res.status_code == 400
    assert "row count exceeds the limit" in res.json()["detail"]

    # 3. Field length limits (English > 100, Chinese > 1000, etc.)
    long_eng = "e" * 101
    long_chi = "c" * 1001
    csv_data = f"english,chinese,pos\n{long_eng},meaning,n\nfatigue,{long_chi},n\n"
    files = {"file": ("test.csv", csv_data.encode("utf-8"), "text/csv")}
    upload_res = await client.post("/api/v1/imports/upload", files=files)
    assert upload_res.status_code == 200
    job_id = upload_res.json()["import_job_id"]

    mapping = {"english": "english", "chinese_meaning": "chinese", "part_of_speech": "pos"}
    analyze_res = await client.post(
        f"/api/v1/imports/{job_id}/analyze",
        json={"field_mapping": mapping, "deck_selection": "Test Deck"}
    )
    assert analyze_res.status_code == 200
    stats = analyze_res.json()
    assert stats["invalid_rows"] == 2
    assert stats["valid_rows"] == 0

    # 4. Deck count limit test (> 50 unique decks in a single import)
    deck_rows = ["word,meaning,pos,deck"]
    for i in range(52):
        deck_rows.append(f"word{i},meaning{i},noun,Deck {i}")
    csv_data = "\n".join(deck_rows)
    files = {"file": ("test.csv", csv_data.encode("utf-8"), "text/csv")}
    upload_res = await client.post("/api/v1/imports/upload", files=files)
    assert upload_res.status_code == 200
    job_id = upload_res.json()["import_job_id"]

    mapping = {"english": "word", "chinese_meaning": "meaning", "part_of_speech": "pos", "deck": "deck"}
    analyze_res = await client.post(
        f"/api/v1/imports/{job_id}/analyze",
        json={"field_mapping": mapping, "deck_selection": "Fallback"}
    )
    assert analyze_res.status_code == 400
    assert "unique deck count in uploaded file exceeds the limit" in analyze_res.json()["detail"].lower()


async def test_import_expiration_and_cleanup(client: AsyncClient):
    import os
    from datetime import datetime, timedelta, timezone
    from app.models import ImportJob
    from conftest import TestingSessionLocal
    from app.services.import_files import UPLOAD_DIR

    # 1. Create a dummy CSV file on the server
    job_id = str(uuid.uuid4())
    filepath = os.path.join(UPLOAD_DIR, f"{job_id}.csv")
    with open(filepath, "w") as f:
        f.write("english,chinese,pos\nword,meaning,noun\n")

    # 2. Insert an expired job in DB (expires_at is 1 hour ago)
    async with TestingSessionLocal() as session:
        expired_job = ImportJob(
            id=job_id,
            original_filename="expired.csv",
            status="pending",
            detected_encoding="utf-8",
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
            created_at=datetime.now(timezone.utc) - timedelta(hours=2)
        )
        session.add(expired_job)
        await session.commit()

    # 3. Accessing endpoints should fail with 410 IMPORT_PREVIEW_EXPIRED and delete the file
    mapping = {"english": "english", "chinese_meaning": "chinese", "part_of_speech": "pos"}
    res1 = await client.post(
        f"/api/v1/imports/{job_id}/analyze",
        json={"field_mapping": mapping, "deck_selection": "Test"}
    )
    assert res1.status_code == 410
    assert "IMPORT_PREVIEW_EXPIRED" in res1.json()["detail"]
    assert not os.path.exists(filepath)

    # Re-create file for checking Rows endpoint
    with open(filepath, "w") as f:
        f.write("english,chinese,pos\nword,meaning,noun\n")
    res2 = await client.get(f"/api/v1/imports/{job_id}/rows?page=1&limit=10")
    assert res2.status_code == 410
    assert "IMPORT_PREVIEW_EXPIRED" in res2.json()["detail"]
    assert not os.path.exists(filepath)

    # Re-create file for checking Commit endpoint
    with open(filepath, "w") as f:
        f.write("english,chinese,pos\nword,meaning,noun\n")
    res3 = await client.post(
        f"/api/v1/imports/{job_id}/commit",
        json={"idempotency_key": "some_key", "request_hash": "some_hash"}
    )
    assert res3.status_code == 410
    assert "IMPORT_PREVIEW_EXPIRED" in res3.json()["detail"]
    assert not os.path.exists(filepath)


async def test_import_commit_retry_does_not_run_an_active_transaction(client: AsyncClient):
    from datetime import datetime, timedelta, timezone
    from app.models import ImportJob
    from conftest import TestingSessionLocal

    job_id = str(uuid.uuid4())
    async with TestingSessionLocal() as session:
        session.add(
            ImportJob(
                id=job_id,
                original_filename="active.csv",
                status="committing",
                idempotency_key="active-key",
                request_hash="active-hash",
                detected_encoding="utf-8",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )
        await session.commit()

    response = await client.post(
        f"/api/v1/imports/{job_id}/commit",
        json={"idempotency_key": "active-key", "request_hash": "active-hash"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Import job transaction is already in progress"

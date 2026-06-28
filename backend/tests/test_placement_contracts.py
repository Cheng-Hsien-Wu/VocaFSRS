import json

import pytest
from httpx import AsyncClient


pytestmark = pytest.mark.asyncio


async def test_placement_answer_contract_rejects_internal_skip(
    client: AsyncClient,
):
    session_response = await client.post(
        "/api/v1/placement-sessions",
        json={"requested_count": 1},
    )
    session = session_response.json()
    card = json.loads(session["manifest_json"])[0]

    response = await client.post(
        f"/api/v1/placement-sessions/{session['id']}/events/batch",
        json={
            "events": [{
                "idempotency_key": "internal-skip-is-not-an-answer",
                "position": card["position"],
                "card_id": card["card_id"],
                "result": "skipped",
                "answered_at": "2024-01-01T00:00:00Z",
            }],
        },
    )

    assert response.status_code == 422


async def test_placement_session_exposes_checkpoint_policy(
    client: AsyncClient,
):
    response = await client.post(
        "/api/v1/placement-sessions",
        json={"requested_count": 1},
    )

    assert response.status_code == 200
    assert response.json()["checkpoint_size"] == 100


async def test_placement_event_must_match_manifest(
    client: AsyncClient,
):
    session_response = await client.post(
        "/api/v1/placement-sessions",
        json={"requested_count": 2},
    )
    session = session_response.json()
    manifest = json.loads(session["manifest_json"])

    response = await client.post(
        f"/api/v1/placement-sessions/{session['id']}/events/batch",
        json={
            "events": [{
                "idempotency_key": "mismatched-card",
                "position": manifest[0]["position"],
                "card_id": manifest[1]["card_id"],
                "result": "known",
                "answered_at": "2024-01-01T00:00:00Z",
            }],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Placement event does not match the session manifest"


async def test_placement_undo_cannot_target_another_session(
    client: AsyncClient,
):
    first_response = await client.post(
        "/api/v1/placement-sessions",
        json={"requested_count": 1},
    )
    first = first_response.json()
    first_card = json.loads(first["manifest_json"])[0]
    await client.post(
        f"/api/v1/placement-sessions/{first['id']}/events/batch",
        json={
            "events": [{
                "idempotency_key": "first-session-answer",
                "position": first_card["position"],
                "card_id": first_card["card_id"],
                "result": "known",
                "answered_at": "2024-01-01T00:00:00Z",
            }],
        },
    )
    await client.post(f"/api/v1/placement-sessions/{first['id']}/abandon")

    second_response = await client.post(
        "/api/v1/placement-sessions",
        json={"requested_count": 1},
    )
    second = second_response.json()
    response = await client.post(
        f"/api/v1/placement-sessions/{second['id']}/events/batch",
        json={
            "events": [{
                "idempotency_key": "cross-session-undo",
                "event_type": "undo",
                "position": 0,
                "target_event_id": "first-session-answer",
                "answered_at": "2024-01-01T00:01:00Z",
            }],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Undo target event not found"

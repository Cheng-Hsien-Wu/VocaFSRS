import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_admin_login_route_is_removed(client: AsyncClient):
    res = await client.post("/api/v1/admin/login", json={"pin": "1234"})
    assert res.status_code == 404


async def test_backup_restore_routes_are_removed(client: AsyncClient):
    list_res = await client.get("/api/v1/backups")
    create_res = await client.post("/api/v1/backups")
    restore_res = await client.post("/api/v1/backups/restore", json={"filename": "backup.db"})

    assert list_res.status_code == 404
    assert create_res.status_code == 404
    assert restore_res.status_code == 404

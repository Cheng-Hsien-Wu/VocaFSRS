import pytest
from httpx import AsyncClient

from main import frontend_dist


async def test_api_responses_are_not_cached(client: AsyncClient):
    res = await client.get("/api/v1/health")

    assert res.status_code == 200
    assert res.headers["cache-control"] == "no-cache"


@pytest.mark.skipif(not frontend_dist.is_dir(), reason="frontend dist is not built")
async def test_frontend_shell_is_not_cached(client: AsyncClient):
    res = await client.get("/")

    assert res.status_code == 200
    assert res.headers["cache-control"] == "no-cache"


@pytest.mark.skipif(not (frontend_dist / "manifest.webmanifest").is_file(), reason="frontend manifest is not built")
async def test_root_static_metadata_keeps_default_cache_headers(client: AsyncClient):
    res = await client.get("/manifest.webmanifest")

    assert res.status_code == 200
    assert "cache-control" not in res.headers

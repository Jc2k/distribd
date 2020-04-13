import asyncio
import copy
import logging

import aiohttp
from distribd import config
from distribd.service import main
import pytest

logger = logging.getLogger(__name__)


@pytest.fixture
async def fake_cluster(loop, tmp_path, monkeypatch):
    test_config = copy.deepcopy(config.config)
    for port in ("8080", "8081", "8082"):
        test_config[f"{port}"]["images_directory"] = tmp_path / port
    monkeypatch.setattr(config, "config", test_config)

    servers = asyncio.ensure_future(
        asyncio.gather(main(["8080"]), main(["8081"]), main(["8082"]),)
    )
    await asyncio.sleep(0)

    async with aiohttp.ClientSession() as session:
        for i in range(100):
            async with session.get("http://localhost:8080/status") as resp:
                assert resp.status == 200
                payload = await resp.json()
                if payload["consensus"]:
                    break
            await asyncio.sleep(1)
        else:
            raise RuntimeError("No consensus")

        async with session.get("http://localhost:9081/v2/") as resp:
            assert resp.status == 200

        async with session.get("http://localhost:9082/v2/") as resp:
            assert resp.status == 200

    yield

    # Cancel servers. Ignore CancelledError.
    servers.cancel()
    try:
        await servers
    except asyncio.CancelledError:
        pass


async def test_list_tags_404(fake_cluster):
    async with aiohttp.ClientSession() as session:
        async with session.get("http://localhost:9080/v2/alpine/tags/list") as resp:
            assert resp.status == 404
            assert await resp.json() == {
                "errors": [
                    {
                        "code": "NAME_UNKNOWN",
                        "detail": {"repository": "alpine"},
                        "message": "repository name not known to registry",
                    }
                ]
            }

        async with session.get("http://localhost:9081/v2/alpine/tags/list") as resp:
            assert resp.status == 404
            assert await resp.json() == {
                "errors": [
                    {
                        "code": "NAME_UNKNOWN",
                        "detail": {"repository": "alpine"},
                        "message": "repository name not known to registry",
                    }
                ]
            }

        async with session.get("http://localhost:9082/v2/alpine/tags/list") as resp:
            assert resp.status == 404
            assert await resp.json() == {
                "errors": [
                    {
                        "code": "NAME_UNKNOWN",
                        "detail": {"repository": "alpine"},
                        "message": "repository name not known to registry",
                    }
                ]
            }


async def get_blob(port, hash):
    for i in range(100):
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"http://localhost:{port}/v2/alpine/blobs/sha256:{hash}"
            ) as resp:
                if resp.status == 404:
                    # Eventual consistency...
                    await asyncio.sleep(0.1)
                    continue
                assert resp.headers["Docker-Content-Digest"] == f"sha256:{hash}"
                return resp.headers["Content-Length"], await resp.read()

    raise RuntimeError("Didn't achieve consistency in time")


async def get_manifest(port, hash):
    for i in range(100):
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"http://localhost:{port}/v2/alpine/manifests/sha256:{hash}"
            ) as resp:
                if resp.status == 404:
                    # Eventual consistency...
                    await asyncio.sleep(0.1)
                    continue
                assert resp.headers["Docker-Content-Digest"] == f"sha256:{hash}"
                return resp.headers["Content-Length"], await resp.json()

    raise RuntimeError("Didn't achieve consistency in time")


async def get_manifest_byt_tag(port, tag):
    for i in range(100):
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"http://localhost:{port}/v2/alpine/manifests/{tag}"
            ) as resp:
                if resp.status == 404:
                    # Eventual consistency...
                    await asyncio.sleep(0.1)
                    continue
                digest = resp.headers["Docker-Content-Digest"].split(":", 1)[1]
                return digest, await resp.json()

    raise RuntimeError("Didn't achieve consistency in time")


async def assert_blob(hash):
    for port in (9080, 9081, 9082):
        content_length, body = await get_blob(port, hash)
        assert content_length == "4"
        assert body == b"9080"


async def assert_manifest(hash, expected_body):
    for port in (9080, 9081, 9082):
        logger.critical("Getting manifest for port %s", port)
        content_length, body = await get_manifest(port, hash)
        assert body == expected_body


async def test_put_blob_fail_invalid_hash(fake_cluster):
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "http://localhost:9080/v2/alpine/blobs/uploads/"
        ) as resp:
            assert resp.status == 202
            assert resp.headers["Location"].startswith("/v2/alpine/blobs/uploads/")
            location = resp.headers["Location"]

        async with session.patch(
            f"http://localhost:9080{location}", data=b"9080"
        ) as resp:
            assert resp.status == 202

        async with session.put(
            f"http://localhost:9080{location}?digest=sha256:invalid_hash_here"
        ) as resp:
            assert resp.status == 400


async def test_put_blob(fake_cluster):
    digest = "bd2079738bf102a1b4e223346f69650f1dcbe685994da65bf92d5207eb44e1cc"

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "http://localhost:9080/v2/alpine/blobs/uploads/"
        ) as resp:
            assert resp.status == 202
            assert resp.headers["Location"].startswith("/v2/alpine/blobs/uploads/")
            location = resp.headers["Location"]

        async with session.patch(
            f"http://localhost:9080{location}", data=b"9080"
        ) as resp:
            assert resp.status == 202

        async with session.put(
            f"http://localhost:9080{location}?digest=sha256:{digest}"
        ) as resp:
            assert resp.status == 201
            assert resp.headers["Location"] == f"/v2/alpine/blobs/sha256:{digest}"
            assert resp.headers["Docker-Content-Digest"] == f"sha256:{digest}"

        await assert_blob(digest)


async def test_put_blob_without_patches(fake_cluster):
    digest = "bd2079738bf102a1b4e223346f69650f1dcbe685994da65bf92d5207eb44e1cc"

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "http://localhost:9080/v2/alpine/blobs/uploads/"
        ) as resp:
            assert resp.status == 202
            assert resp.headers["Location"].startswith("/v2/alpine/blobs/uploads/")
            location = resp.headers["Location"]

        async with session.put(
            f"http://localhost:9080{location}?digest=sha256:{digest}", data=b"9080"
        ) as resp:
            assert resp.status == 201
            assert resp.headers["Location"] == f"/v2/alpine/blobs/sha256:{digest}"
            assert resp.headers["Docker-Content-Digest"] == f"sha256:{digest}"

        await assert_blob(digest)


async def test_full_manifest_round_trip(fake_cluster):
    manifest = {
        "manifests": [],
        "mediaType": "application/vnd.docker.distribution.manifest.list.v2+json",
        "schemaVersion": 2,
    }

    url = f"http://localhost:9080/v2/alpine/manifests/3.11"

    logger.critical("Starting put")
    async with aiohttp.ClientSession() as session:
        async with session.put(url, json=manifest) as resp:
            assert resp.status == 200
            hash = resp.headers["Docker-Content-Digest"].split(":", 1)[1]
    logger.critical("Finished put")

    await assert_manifest(hash, manifest)

    digest, body = await get_manifest_byt_tag(9080, "3.11")
    assert digest == hash
    assert body == manifest

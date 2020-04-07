import pathlib
import hashlib
import logging
import uuid
import os

from aiofile import AIOFile, Writer
from aiohttp import web

from .utils.web import run_server

images_directory = pathlib.Path("images")

manifests_by_hash = {}
manifests_by_path = {}
tags = {}
blobs = {}


def scan_images():
    for path in images_directory.glob("**/manifest.json"):
        with open(path, "rb") as fp:
            hash = hashlib.sha256(fp.read()).hexdigest()

        manifests_by_path[str(path)] = hash
        manifests_by_hash[hash] = path

        repository = str(path.parent.parent.relative_to(images_directory))
        tag = path.parent.name
        tags.setdefault(repository, []).append(tag)


scan_images()

routes = web.RouteTableDef()

@routes.get('/v2')
async def handle_bare_v2(request):
    raise web.HTTPFound('/v2/')


@routes.get('/v2/')
async def handle_v2_root(request):
    return web.Response(text="")


@routes.get('/v2/{repository:[^{}]+}/tags/list')
async def list_images_in_repository(request):
    repository = request.match_info["repository"]

    if repository not in tags:
        raise web.HTTPNotFound(
            headers={"Content-Type": "application/json"},
            text='{"errors": [{"message": "manifest tag did not match URI", "code": "TAG_INVALID", "detail": ""}]}',
        )

    return web.json_response({
        "name": repository,
        "tags": tags[repository],
    })


@routes.get('/v2/{repository:[^{}]+}/manifests/{tag}')
async def get_manifest_by_tag(request):
    print("!!!!")
    # Return images/repository/tag/manifest.json
    repository = request.match_info["repository"]
    tag = request.match_info["tag"]

    manifest_path = images_directory / repository / tag / "manifest.json"
    if not manifest_path.is_file():
        raise web.HTTPNotFound(
            headers={"Content-Type": "application/json"},
            text='{"errors": [{"message": "manifest tag did not match URI", "code": "TAG_INVALID", "detail": ""}]}',
        )

    hash = manifests_by_path[str(manifest_path)]

    return web.FileResponse(
        headers={
            "Docker-Content-Digest": f"sha256:{hash}"
        },
        path=manifest_path,
    )


@routes.get('/v2/{repository:[^{}]+}/manifests/sha256:{hash}')
async def get_manifest_by_hash(request):
    repository = request.match_info["repository"]
    hash = request.match_info["hash"]

    if hash not in manifests_by_hash:
        raise web.HTTPNotFound(
            headers={"Content-Type": "application/json"},
            text='{"errors": [{"message": "manifest tag did not match URI", "code": "TAG_INVALID", "detail": ""}]}',
        )

    manifest_path = manifests_by_hash[hash]

    return web.FileResponse(
        headers={
            "Docker-Content-Digest": f"sha256:{hash}"
        },
        path=manifest_path,
    )



@routes.get('/v2/{repository:[^{}]+}/blobs/sha255:{hash}')
async def get_blob_by_hash(request):
    repository = request.match_info["repository"]
    hash = request.match_info["hash"]

    repository_dir = images_directory / repository
    if not repository_dir.is_dir():
        raise web.HTTPNotFound(
            headers={"Content-Type": "application/json"},
            text='{"errors": [{"message": "manifest tag did not match URI", "code": "TAG_INVALID", "detail": ""}]}',
        )

    hash_path = repository_dir / hash
    if not hash_path.is_file():
        raise web.HTTPNotFound(
            headers={"Content-Type": "application/json"},
            text='{"errors": [{"message": "manifest tag did not match URI", "code": "TAG_INVALID", "detail": ""}]}',
        )

    return web.FileResponse(
        headers={},
        path=hash_path,
    )


@routes.post('/v2/{repository:[^{}]+}/blobs/uploads/')
async def start_upload(request):
    repository = request.match_info["repository"]

    session_id = str(uuid.uuid4())

    return web.json_response(
        {},
        status=202,
        headers={
            "Location": f"/v2/{repository}/blobs/uploads/{session_id}",
            "Range": "0-0",
            "Blob-Upload-Session-ID": session_id,
        }
    )


@routes.patch('/v2/{repository:[^{}]+}/blobs/uploads/{session_id}')
async def upload_chunk_by_patch(request):
    repository = request.match_info["repository"]
    session_id = request.match_info["session_id"]

    uploads = images_directory / "uploads"
    if not uploads.exists():
        os.makedirs(uploads)

    upload_path = uploads / session_id

    async with AIOFile(upload_path, "ab") as fp:
        writer = Writer(fp)
        body = await request.read()
        await writer(body)
        await fp.fsync()

    info = os.stat(upload_path)

    return web.json_response(
        {},
        status=202,
        headers={
            "Location": f"/v2/{repository}/blobs/uploads/{session_id}",
            "Blob-Upload-Session-ID": session_id,
            "Range": f"0-{info.st_size}",
        }
    )


@routes.put('/v2/{repository:[^{}]+}/blobs/uploads/{session_id}')
async def upload_finish(request):
    repository = request.match_info["repository"]
    session_id = request.match_info["session_id"]

    uploads = images_directory / "uploads"
    if not uploads.exists():
        os.makedirs(uploads)

    upload_path = uploads / session_id

    # FIXME: This PUT might have some payload associated with it

    # FIXME: Do on each upload incrementally
    with open(upload_path, "rb") as fp:
        hash = hashlib.sha256(fp.read()).hexdigest()
        digest = f"sha256:{hash}"

    # FIXME: Read digest out of URL and make sure it matches

    blob_dir = images_directory / "blobs"
    if not blob_dir.exists():
        os.makedirs(blob_dir)

    blob_path = blob_dir / hash

    os.rename(upload_path, blob_path)

    return web.json_response(
        {},
        status=201,
        headers={
            "Location": f"/v2/{repository}/blobs/{digest}",
            "Docker-Content-Digest": digest,
        }
    )


@routes.head('/v2/{repository:[^{}]+}/blobs/sha256:{hash}')
async def head_blob(request):
    repository = request.match_info["repository"]
    hash = request.match_info["hash"]

    blob_path = images_directory / "blobs" / hash
    if not blob_path.exists():
        return web.json_response(
            {},
            status=404,
            headers={
                "Docker-Content-Digest": hash,
            }
        )

    return web.json_response(
        {},
        status=200,
        headers={
            "Content-Length": "0",
            "Docker-Content-Digest": hash,
        }
    )


@routes.put('/v2/{repository:[^{}]+}/manifests/{tag}')
async def put_manifest(request):
    repository = request.match_info["repository"]
    tag = request.match_info["tag"]

    manifest = await request.read()
    hash = hashlib.sha256(manifest).hexdigest()
    prefixed_hash = f"sha256:{hash}"

    manifests_dir = images_directory / "manifests"
    if not os.path.exists(manifests_dir):
        os.makedirs(manifests_dir)

    manifest_path = manifests_dir / hash

    async with AIOFile(manifest_path, "wb") as fp:
        writer = Writer(fp)
        await writer(manifest)
        await fp.fsync()

    return web.json_response(
        {},
        status=200,
        headers={
            "Content-Length": "0",
            "Docker-Content-Digest": prefixed_hash,
        }
    )


async def run_registry(port):
    return await run_server(
        "0.0.0.0",
        port,
        routes,
    )

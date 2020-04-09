import logging
import os
import random

import aiohttp

from .actions import RegistryActions
from . import config
from .raft import invoke
from .state import Reducer

logger = logging.getLogger(__name__)


class Mirrorer(Reducer):
    def __init__(self, image_directory, identifier, send_action):
        self.image_directory = image_directory
        self.identifier = identifier
        self.send_action = send_action

        self.blob_locations = {}
        self.blob_repos = {}
        self.manifest_locations = {}
        self.manifest_repos = {}

    async def _do_transfer(self, urls, destination):
        if destination.exists():
            logger.debug("%s already exists, not requesting", destination)
            return

        url = random.choice(urls)
        logger.critical("Starting download from %s to %s", url, destination)

        if not destination.parent.exists():
            os.makedirs(destination.parent)

        # FIXME - use a temp file rather than writing directly to uploads
        # FIXME - confirm hash of download
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.error("Failed to retrieve: %s, status %s", url, resp.status)
                    return False
                # FIXME: Use aiofile
                with open(destination, "wb") as fp:
                    chunk = await resp.content.read(1024 * 1024)
                    while chunk:
                        fp.write(chunk)
                        chunk = await resp.content.read(1024 * 1024)

        return True

    def should_download_blob(self, hash):
        if hash not in self.blob_repos:
            return False

        if hash not in self.blob_locations:
            return False

        locations = self.blob_locations[hash]

        if len(locations) == 0:
            return False

        if self.identifier in locations:
            return False

        return True

    def urls_for_blob(self, hash):
        repo = next(iter(self.blob_repos[hash]))
        locations = [config.config[l]["registry_url"] for l in self.blob_locations[hash]]

        return [f"{location}/v2/{repo}/blobs/sha256:{hash}" for location in locations]

    async def do_download_blob(self, hash):
        if not self.should_download_blob(hash):
            return

        destination = self.image_directory / "blobs" / hash
        await self._do_transfer(self.urls_for_blob(hash), destination)

        await self.send_action(
            {
                "type": RegistryActions.BLOB_STORED,
                "hash": hash,
                "location": self.identifier,
            }
        )

    def should_download_manifest(self, hash):
        if hash not in self.manifest_repos:
            return False

        if hash not in self.manifest_locations:
            return False

        locations = self.manifest_locations[hash]

        if len(locations) == 0:
            return False

        if self.identifier in locations:
            return False

        return True

    def urls_for_manifest(self, hash):
        repo = next(iter(self.manifest_repos[hash]))
        locations = [config.config[l]["registry_url"] for l in self.manifest_locations[hash]]
        return [f"{location}/v2/{repo}/blobs/sha256:{hash}" for location in locations]

    async def do_download_manifest(self, hash):
        if not self.should_download_manifest(hash):
            return

        destination = self.image_directory / "manifests" / hash
        await self._do_transfer(self.urls_for_manifest(hash), destination)

        await self.send_action(
            {
                "type": RegistryActions.MANIFEST_STORED,
                "hash": hash,
                "location": self.identifier,
            }
        )

    def dispatch(self, entry):
        if entry["type"] == RegistryActions.BLOB_STORED:
            blob = self.blob_locations.setdefault(entry["hash"], set())
            blob.add(entry["location"])

            if self.should_download_blob(entry["hash"]):
                invoke(self.do_download_blob(entry["hash"]))

        elif entry["type"] == RegistryActions.BLOB_MOUNTED:
            blob = self.blob_repos.setdefault(entry["hash"], set())
            blob.add(entry["repository"])

            if self.should_download_blob(entry["hash"]):
                invoke(self.do_download_blob(entry["hash"]))

        elif entry["type"] == RegistryActions.MANIFEST_STORED:
            manifest = self.manifest_locations.setdefault(entry["hash"], set())
            manifest.add(entry["location"])

            if self.should_download_manifest(entry["hash"]):
                invoke(self.do_download_manifest(entry["hash"]))

        elif entry["type"] == RegistryActions.MANIFEST_MOUNTED:
            manifest = self.manifest_repos.setdefault(entry["hash"], set())
            manifest.add(entry["repository"])

            if self.should_download_manifest(entry["hash"]):
                invoke(self.do_download_manifest(entry["hash"]))
import enum


class RegistryActions(enum.Enum):

    # A given sha256 blob was committed to disk and should be replicated
    BLOB_STORED = "blob-stored"

    # A given sha256 blob hash was deleted and is safe to delete from disk
    BLOB_DELETED = "blob-deleted"

    # Associate a blob hash with a repository
    BLOB_MOUNTED = "blob-mounted"

    # A given sha256 hash was stored on a node
    MANIFEST_STORED = "manifest-stored"

    # A given sha256 hash was deleted from the cluster and is safe to garbage collect
    MANIFEST_DELETED = "manifest-deleted"

    # Associate a manifest hash with a repository.
    MANIFEST_MOUNTED = "manifest-mounted"

    # A given sha256 manifest hash was tagged with a repository and a tag
    HASH_TAGGED = "hash-tagged"
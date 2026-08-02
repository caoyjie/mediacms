from files.models.domain import StorageBackend


class LegacyProcessingNotAllowed(RuntimeError):
    """The requested local processing operation is unavailable for AWS media."""


def uses_aws_pipeline(media):
    return media.storage_backend == StorageBackend.AWS


def legacy_processing_allowed(media):
    return not uses_aws_pipeline(media)

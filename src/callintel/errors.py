"""Error taxonomy. Workers decide retry vs. park purely on these types (see workers/policy.py)."""


class PipelineError(Exception):
    """Base class for errors raised by pipeline code."""


class TransientError(PipelineError):
    """Might succeed if retried later (dependency down, timeout, injected fault)."""


class PermanentError(PipelineError):
    """Retrying cannot help; the message goes straight to the parking lot."""


class CorruptAudioError(PermanentError):
    pass


class UnsupportedAudioError(PermanentError):
    pass


class CallNotFound(PermanentError):
    pass


class ObjectNotFound(PermanentError):
    """An object the pipeline wrote earlier is missing from storage."""

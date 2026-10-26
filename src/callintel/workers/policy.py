from collections.abc import Sequence
from dataclasses import dataclass

from callintel.errors import PermanentError


@dataclass(frozen=True)
class Retry:
    delay_s: int


@dataclass(frozen=True)
class Park:
    reason: str


def decide(attempt: int, error: BaseException, delays: Sequence[int]) -> Retry | Park:
    """What to do after `attempt` (1-based) failed with `error`.

    Permanent errors are parked immediately: retrying a corrupt file only burns capacity.
    Anything else is assumed transient (timeouts, dependency outages, unknown bugs) and
    retried with increasing delays until the budget is spent.
    """
    if isinstance(error, PermanentError):
        return Park("permanent_error")
    if attempt > len(delays):
        return Park("retries_exhausted")
    return Retry(delays[attempt - 1])

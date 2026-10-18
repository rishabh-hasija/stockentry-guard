"""Storage-independent errors exposed by repository contracts."""


class ConcurrentProductUpdateError(Exception):
    """The product is missing or no longer has the expected revision."""


class TransactionAbortedError(Exception):
    """A failed write prevents the unit of work from committing."""


class EventReservationError(RuntimeError):
    """An event identity could not be reserved; retry in a fresh unit of work."""

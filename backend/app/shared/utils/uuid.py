"""UUID utility functions for application-side ID generation."""

import uuid
import uuid6


def generate_uuid7() -> uuid.UUID:
    """Generate an RFC 9562 monotonic time-ordered UUIDv7.

    Guarantees millisecond time-ordering in the most significant 48 bits,
    providing high B-tree insert locality and deterministic sorting.
    """
    return uuid6.uuid7()

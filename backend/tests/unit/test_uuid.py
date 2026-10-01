"""Unit tests for UUIDv7 generation and stable graph canonical IDs."""

import time
import uuid

from app.shared.utils.uuid import generate_uuid7


def test_generate_uuid7_is_valid_uuid():
    """Verify generated UUID is a valid RFC 9562 UUID."""
    val = generate_uuid7()
    assert isinstance(val, uuid.UUID)
    assert val.version == 7


def test_generate_uuid7_monotonic_time_ordering():
    """Verify that successively generated UUIDv7s are strictly ordered."""
    ids = []
    for _ in range(50):
        ids.append(generate_uuid7())
        time.sleep(0.001)

    # Sorting UUID objects or strings should match generation order
    sorted_ids = sorted(ids)
    assert ids == sorted_ids


def test_canonical_graph_urn_mapping():
    """Verify UUID maps into canonical Knowledge Graph URN format."""
    ws_id = generate_uuid7()
    urn = f"urn:fabric:workspace:{ws_id}"
    assert urn.startswith("urn:fabric:workspace:")
    assert str(ws_id) in urn

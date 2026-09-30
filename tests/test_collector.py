from transit.collector import flatten


def test_flatten_extracts_attributes_and_relationships():
    vehicle = {
        "id": "y1897",
        "attributes": {"latitude": 42.36, "longitude": -71.1, "current_status": "STOPPED_AT",
                       "current_stop_sequence": 20, "direction_id": 0, "revenue": "REVENUE",
                       "updated_at": "2026-09-30T15:05:16-04:00"},
        "relationships": {"route": {"data": {"id": "1"}}, "trip": {"data": {"id": "78181053"}},
                          "stop": {"data": None}},
    }
    row = flatten(vehicle, "2026-09-30T19:05:20+00:00")
    assert row["vehicle_id"] == "y1897"
    assert row["route_id"] == "1" and row["trip_id"] == "78181053"
    assert row["stop_id"] is None
    assert row["current_stop_sequence"] == 20
    assert row["bearing"] is None  # missing attributes become explicit nulls

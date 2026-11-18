from datetime import datetime, timezone
import pytest
from outage_model.contracts import RowKey, OutageEvent


def test_row_horizon_and_identity():
    with pytest.raises(ValueError):
        RowKey(archive_id='a', grid_id='g', row=0, col=0, horizon_id='hourly')
    with pytest.raises(ValueError):
        RowKey(archive_id='a', grid_id='g', row=-1, col=0, horizon_id='0_24')


def test_naive_event_times_rejected():
    with pytest.raises(ValueError):
        OutageEvent(canonical_id='x', source='s', utility_id='u', original_event_id='1',
                    first_seen=datetime(2026, 1, 1), last_seen=datetime.now(timezone.utc),
                    snapshot_ids=['s'])


def test_nonfinite_calibration_rejected():
    from outage_model.contracts import CalibrationBundle
    with pytest.raises(ValueError):CalibrationBundle(slope=float('nan'),intercept=0)

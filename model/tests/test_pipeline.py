"""
Tests for the feature engineering pipeline.

Covers:
  - build_feature_vector (smoke test, wind-dominated scenario)
  - Label validation (exclusion of planned/PSPS/unknown/incomplete-coverage negatives)
  - Inference batch smoke test (fixture mode)
  - API contract tests (all three windows present, status values, FIPS validation)
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Allow imports from model/ root
sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.features import (
    CountyPhysicalFeatures,
    GFSHour,
    NWSAlert,
    WindowFeatureVector,
    build_feature_vector,
    build_three_windows,
)
from shared.types import ForecastStatus
from training.model import OutageLabel, validate_labels


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_physical(fips: str = "06037", **kwargs) -> CountyPhysicalFeatures:
    defaults = dict(
        tree_cover_pct=20.0,
        overhead_line_share=0.55,
        terrain_roughness=0.2,
        fire_zone="tier2",
        past_unplanned_outages_3yr=3,
        median_unplanned_hours=5.0,
    )
    defaults.update(kwargs)
    return CountyPhysicalFeatures(county_fips=fips, **defaults)


def _make_gfs_hours(
    fips: str,
    start: datetime,
    n: int = 72,
    gust: float = 15.0,
    temp: float = 72.0,
    soil: float = 0.1,
) -> list[GFSHour]:
    return [
        GFSHour(
            valid_time=start + timedelta(hours=i),
            county_fips=fips,
            wind_gust_mph=gust,
            temp_f=temp,
            soil_moisture=soil,
        )
        for i in range(n)
    ]


T0 = datetime(2024, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Feature engineering tests
# ---------------------------------------------------------------------------

class TestBuildFeatureVector:
    def test_no_gfs_returns_incomplete(self):
        physical = _make_physical()
        vec = build_feature_vector([], [], physical, T0, T0 + timedelta(hours=24))
        assert not vec.feature_complete
        assert vec.max_gust_mph == 0.0

    def test_calm_conditions_low_probability(self):
        physical = _make_physical()
        gfs = _make_gfs_hours("06037", T0, gust=5.0, temp=65.0, soil=0.0)
        vec = build_feature_vector(gfs, [], physical, T0, T0 + timedelta(hours=24))
        assert vec.wind_score < 0.01
        assert vec.heat_score == 0.0
        assert vec.feature_complete

    def test_high_wind_scores(self):
        physical = _make_physical()
        gfs = _make_gfs_hours("06037", T0, gust=60.0, temp=75.0, soil=0.0)
        vec = build_feature_vector(gfs, [], physical, T0, T0 + timedelta(hours=24))
        assert vec.wind_score > 0.7
        assert vec.max_gust_mph == 60.0

    def test_wet_soil_amplifies_wind_score(self):
        physical = _make_physical()
        gfs_dry = _make_gfs_hours("06037", T0, gust=40.0, soil=0.0)
        gfs_wet = _make_gfs_hours("06037", T0, gust=40.0, soil=0.4)
        vec_dry = build_feature_vector(gfs_dry, [], physical, T0, T0 + timedelta(hours=24))
        vec_wet = build_feature_vector(gfs_wet, [], physical, T0, T0 + timedelta(hours=24))
        assert vec_wet.wind_score > vec_dry.wind_score
        assert vec_wet.wet_soil == 1.0

    def test_red_flag_warning_flag(self):
        physical = _make_physical()
        gfs = _make_gfs_hours("06037", T0)
        alert = NWSAlert(
            county_fips="06037",
            event="Red Flag Warning",
            onset=T0,
            expires=T0 + timedelta(hours=10),
        )
        vec = build_feature_vector(gfs, [alert], physical, T0, T0 + timedelta(hours=24))
        assert vec.has_red_flag_warning

    def test_three_windows_non_overlapping(self):
        physical = _make_physical()
        gfs = _make_gfs_hours("06037", T0, n=72)
        vecs = build_three_windows(gfs, [], physical, T0)
        assert len(vecs) == 3
        assert vecs[0].window_end == vecs[1].window_start
        assert vecs[1].window_end == vecs[2].window_start

    def test_exposure_without_canopy(self):
        physical = _make_physical(canopy_available=False)
        gfs = _make_gfs_hours("06037", T0)
        vec = build_feature_vector(gfs, [], physical, T0, T0 + timedelta(hours=24))
        assert vec.tree_cover_pct == 0.0
        assert not vec.feature_complete


# ---------------------------------------------------------------------------
# Label validation tests
# ---------------------------------------------------------------------------

class TestValidateLabels:
    def _label(self, **kwargs) -> OutageLabel:
        defaults = dict(
            county_fips="06037",
            window_start="2023-01-01",
            window_end="2023-01-02",
            outage_occurred=True,
            outage_type="unplanned",
            coverage_complete=True,
            source="test",
        )
        defaults.update(kwargs)
        return OutageLabel(**defaults)

    def test_valid_unplanned_label_passes(self):
        labels = [self._label()]
        assert len(validate_labels(labels)) == 1

    def test_planned_label_excluded(self):
        labels = [self._label(outage_type="planned")]
        assert len(validate_labels(labels)) == 0

    def test_psps_label_excluded(self):
        labels = [self._label(outage_type="psps")]
        assert len(validate_labels(labels)) == 0

    def test_unknown_type_excluded(self):
        labels = [self._label(outage_type="unknown")]
        assert len(validate_labels(labels)) == 0

    def test_negative_without_coverage_excluded(self):
        labels = [self._label(outage_occurred=False, coverage_complete=False)]
        assert len(validate_labels(labels)) == 0

    def test_negative_with_coverage_passes(self):
        labels = [self._label(outage_occurred=False, coverage_complete=True)]
        assert len(validate_labels(labels)) == 1

    def test_mixed_batch(self):
        labels = [
            self._label(outage_type="unplanned"),
            self._label(outage_type="planned"),
            self._label(outage_type="psps"),
            self._label(outage_occurred=False, coverage_complete=False),
        ]
        assert len(validate_labels(labels)) == 1


# ---------------------------------------------------------------------------
# Model stub tests
# ---------------------------------------------------------------------------

class TestModelStub:
    def test_predict_proba_unfitted_raises(self):
        from training.model import OutageOnsetModel
        import numpy as np
        model = OutageOnsetModel()
        with pytest.raises(RuntimeError):
            model.predict_proba(np.zeros((1, 13)))

    def test_predict_proba_output_range(self):
        from training.model import OutageOnsetModel, FEATURE_COLS
        import numpy as np
        model = OutageOnsetModel()
        # Bypass the sample-count assertion for the stub test
        model._fitted = True
        row = np.zeros((5, len(FEATURE_COLS)))
        row[:, FEATURE_COLS.index("wind_score")] = [0.0, 0.3, 0.5, 0.8, 1.0]
        probs = model.predict_proba(row)
        assert all(0.02 <= p <= 0.97 for p in probs), f"Out-of-range probabilities: {probs}"

    def test_duration_model_wind(self):
        from training.model import ConditionalDurationModel
        dm = ConditionalDurationModel()
        low, high = dm.predict_range("wind", 6.0)
        assert low == max(2.0, 0.5 * 6.0)
        assert high == max(8.0, 1.5 * 6.0)

    def test_duration_model_heat(self):
        from training.model import ConditionalDurationModel
        dm = ConditionalDurationModel()
        low, high = dm.predict_range("heat", 5.0)
        assert low == 1.0 and high == 6.0


# ---------------------------------------------------------------------------
# Inference batch smoke test (fixture mode)
# ---------------------------------------------------------------------------

class TestBatchInference:
    def test_batch_runs_without_error(self):
        from inference.batch import run_batch
        results = asyncio.run(run_batch(use_fixtures=True))
        # Should return at least one CountyForecast
        assert isinstance(results, list)
        for fc in results:
            assert hasattr(fc, "county_fips")
            assert hasattr(fc, "next_24h")
            assert hasattr(fc, "next_24_48h")
            assert hasattr(fc, "next_48_72h")

    def test_batch_three_windows_always_present(self):
        from inference.batch import run_batch
        results = asyncio.run(run_batch(use_fixtures=True))
        for fc in results:
            assert fc.next_24h.window_label == "Next 24h"
            assert fc.next_24_48h.window_label == "24–48h"
            assert fc.next_48_72h.window_label == "48–72h"


# ---------------------------------------------------------------------------
# FastAPI contract tests
# ---------------------------------------------------------------------------

class TestAPIContracts:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from service.main import app
        return TestClient(app)

    def test_healthz(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_forecast_invalid_fips_rejected(self, client):
        resp = client.get("/v1/forecasts/ABCDE")
        assert resp.status_code == 422

    def test_forecast_short_fips_rejected(self, client):
        resp = client.get("/v1/forecasts/123")
        assert resp.status_code == 422

    def test_forecast_unknown_fips_returns_unavailable(self, client):
        resp = client.get("/v1/forecasts/99999")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == ForecastStatus.UNAVAILABLE
        assert "next_24h" in data
        assert "next_24_48h" in data
        assert "next_48_72h" in data

    def test_planned_outages_invalid_fips_rejected(self, client):
        resp = client.get("/v1/planned-outages/BAD")
        assert resp.status_code == 422

    def test_planned_outages_no_feed_returns_structured_response(self, client):
        resp = client.get("/v1/planned-outages/06037")
        assert resp.status_code == 200
        data = resp.json()
        assert "no_feed_reason" in data or "notices" in data

    def test_coverage_endpoint_returns_list(self, client):
        resp = client.get("/v1/coverage")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

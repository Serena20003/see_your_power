from datetime import datetime, timedelta, timezone
import pytest
from raster_pipeline.config import PipelineConfig
from raster_pipeline.contracts import GridSpec, SourceField
from raster_pipeline.source import discover_run, ceil_hour

T = datetime(2026, 11, 16, 6, tzinfo=timezone.utc)

def catalog(run=T, hours=80, seen=T):
    return [SourceField(run=run, end_step=h, start_step=0 if field=='TOT_PREC' else h,
        field=field, url=f'https://example.test/{run:%Y%m%d%H}/{field}/{h}',
        first_seen=seen, grid_id='icon-test', units=unit, level=level,
        level_type=kind, packing='grid_simple', vector_reference='earth')
        for h in range(hours+1) for field,unit,level,kind in [
        ('TOT_PREC','kg m-2',0,'surface'),('T_2M','K',2,'heightAboveGround'),
        ('U_10M','m s-1',10,'heightAboveGround'),('V_10M','m s-1',10,'heightAboveGround')]]

def test_grid_and_utc_contract():
    g=GridSpec()
    assert (g.width,g.height)==(472,208)
    assert g.center(0,0)==(-124.9375,49.9375)
    assert ceil_hour(T+timedelta(seconds=1))==T+timedelta(hours=1)
    assert ceil_hour(T)==T
    with pytest.raises(ValueError): ceil_hour(T.replace(tzinfo=None))

@pytest.mark.parametrize('hours',[-1,25])
def test_lookback_rejected(hours):
    with pytest.raises(ValueError): PipelineConfig(lookback_hours=hours)

def test_newest_eligible_and_prior_history():
    cfg=PipelineConfig(catalog=catalog(T-timedelta(hours=6),90)+catalog(),lookback_hours=5)
    selection=discover_run(T,T,cfg)
    assert selection.selected_run==T
    assert len(selection.intervals)==77
    assert selection.intervals[0].run==T-timedelta(hours=6)
    assert selection.intervals[-1].end==T+timedelta(hours=72)

def test_missing_last_or_future_files_never_selected():
    for records in [catalog(hours=71),catalog(seen=T+timedelta(seconds=1))]:
        with pytest.raises(ValueError,match='72'):
            discover_run(T,T,PipelineConfig(catalog=records,lookback_hours=0))

def test_late_cutoff_and_sparse_cadence_rejected():
    records=catalog(hours=78)
    with pytest.raises(ValueError):
        discover_run(T+timedelta(hours=7),T+timedelta(hours=7),PipelineConfig(catalog=records))
    with pytest.raises(ValueError):
        discover_run(T,T,PipelineConfig(catalog=[r for r in records if r.end_step!=20]))

def test_layout_is_configurable_not_a_filename_guarantee():
    for root in ['https://example.test/legacy/','https://example.test/v1/']:
        records=[r.model_copy(update={'url':root+r.url.rsplit('/',1)[-1]}) for r in catalog()]
        assert discover_run(T,T,PipelineConfig(catalog=records,lookback_hours=0)).fields[0].url.startswith(root)
    bad=catalog(); bad[4]=bad[4].model_copy(update={'units':'unknown'})
    with pytest.raises(ValueError,match='units'): discover_run(T,T,PipelineConfig(catalog=bad,lookback_hours=0))

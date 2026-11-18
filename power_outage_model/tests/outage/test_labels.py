from datetime import datetime, timezone, timedelta
import pytest
from raster_pipeline.contracts import GridSpec
from outage_model.contracts import *
from outage_model.labels import *
T=datetime(2026,1,1,tzinfo=timezone.utc)
GRID=GridSpec(grid_id='test',west=-120,east=-117,south=32,north=35,resolution=1)
POLICY=LabelPolicy(version='test',reporting_lag_hours=24)

def event(**kw):
    return OutageEvent(**dict(dict(canonical_id='e',source='s',utility_id='u',original_event_id='e',onset=T,
        onset_quality='actual',first_seen=T,last_seen=T,outage_type='unplanned',type_evidence='audit',
        longitude=-119.5,latitude=34.5,geometry_meaning='qualified_incident_marker',location_quality='audited',snapshot_ids=('s',)),**kw))

def make_labels(events,complete=True,freeze=T+timedelta(days=5)):
    keys=tuple(RowKey(archive_id='a',grid_id='test',row=0,col=0,horizon_id=h) for h in HORIZONS)
    cov=CoverageInterval(coverage_id='c',source='s',domain_id='union',start=T,end=T+timedelta(days=3),
        footprint_version='f',cells=(('test',0,0),),expected_polls=288,received_polls=288,
        complete=complete,whole_cell=complete,ascertainment_audited=complete,evidence=('audit',))
    return build_labels(reconcile_events([events],POLICY),[cov],keys,freeze,POLICY,grid=GRID,windows={'a':T},
        row_metadata={k.id:dict(state='CA',region='west',block_id='storm',season='Q1') for k in keys})

def test_half_open_windows_and_repeated_events():
    e=event(onset=T+timedelta(hours=24))
    assert [x.label for x in make_labels([e,e])]==[0,1,0]
    assert sum(x.new_event_count for x in make_labels([e,e]))==1

def test_incomplete_positive_is_null_and_immature():
    assert make_labels([event()],False)[0].label is None
    assert make_labels([],freeze=T)[0].label is None

@pytest.mark.parametrize('kind',['planned','psps','unknown'])
def test_type_exclusions(kind):
    assert make_labels([event(outage_type=kind)])[0].label==0

def test_uncertain_onset_and_reused_id_fail_closed():
    assert make_labels([event(onset_quality='reported_system_time')])[0].label is None
    assert make_labels([event(),event(onset=T+timedelta(hours=1))])[0].label is None

def test_spatial_edges():
    assert assign_cell(GRID,-119,34)==(1,1)
    assert assign_cell(GRID,-120,35)==(0,0)
    assert assign_cell(GRID,-117,34) is None
    assert assign_cell(GRID,-119,32) is None


def test_late_revision_cannot_silently_enter_older_freeze():
    e=event(first_seen=T+timedelta(days=7),last_seen=T+timedelta(days=7))
    assert make_labels([e],freeze=T+timedelta(days=5))[0].label is None


def test_unrelated_quarantine_does_not_disable_whole_domain():
    from outage_model.contracts import EventLedger
    keys=[RowKey(archive_id='a',grid_id='test',row=0,col=0,horizon_id='0_24')]
    other=event(longitude=-117.5,latitude=32.5)
    ledger=EventLedger(events=(),quarantine=({'id':'other','reason':'conflict','events':[other.model_dump(mode='json')]},),policy_hash='test')
    cov=CoverageInterval(coverage_id='c',source='s',domain_id='union',start=T,end=T+timedelta(days=3),footprint_version='f',
        cells=(('test',0,0),),expected_polls=1,received_polls=1,complete=True,whole_cell=True,ascertainment_audited=True,evidence=('audit',))
    result=build_labels(ledger,[cov],keys,T+timedelta(days=5),POLICY,grid=GRID,windows={'a':T},row_metadata={keys[0].id:dict(state='CA',region='West',block_id='storm',season='Q1')})
    assert result[0].label==0


def test_boundary_uncertainty_is_reported_without_moving_marker():
    report=boundary_sensitivity([event(longitude=-119,latitude=34)],GRID)[0]
    assert report['distance_to_cell_edge_m']==0
    assert report['sensitivity']['100'] is True

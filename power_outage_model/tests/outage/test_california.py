from datetime import datetime, timezone
import json
import pytest
import httpx
from urllib.parse import parse_qs
from raster_pipeline.storage import LocalStore
from outage_model.adapters.california import CaliforniaAdapter, PIN

class Response:
    def __init__(self,data):self.data=data
    def raise_for_status(self):pass
    def json(self):return self.data
class Client:
    def __init__(self,missing=False):self.missing=missing
    def get(self,url,params,timeout):
        if not url.endswith('/query'):return Response(PIN)
        if params.get('returnIdsOnly'):return Response({'objectIds':[1]})
        return Response({'spatialReference':{'wkid':4326},'features':[] if self.missing else [
            {'attributes':{'OBJECTID':1,'IncidentId':'i','UtilityCompany':'SDGE','OutageType':'Not Planned','StartDate':1794844800000},'geometry':{'x':-117,'y':33}}]})
    def post(self,url,data,timeout):
        return self.get(url,data,timeout)


def test_large_inventory_uses_post_to_avoid_arcgis_url_limit(tmp_path):
    ids=list(range(43950998,43951499))
    batches=[]

    def respond(request):
        # ArcGIS rejects long query URLs with an HTML 404 response.
        if len(str(request.url))>2048:
            return httpx.Response(404,text='Not Found')
        if not request.url.path.endswith('/query'):
            return httpx.Response(200,json=PIN)
        if request.url.params.get('returnIdsOnly'):
            return httpx.Response(200,json={'objectIds':ids})
        assert request.method=='POST'
        assert not request.url.query
        params=parse_qs(request.content.decode())
        assert params['f']==['json'] and params['outSR']==['4326']
        chunk=list(map(int,params['objectIds'][0].split(',')))
        batches.append(chunk)
        return httpx.Response(200,json={'spatialReference':{'wkid':4326},'features':[
            {'attributes':{'OBJECTID':oid,'IncidentId':str(oid),'UtilityCompany':'SDGE'},
             'geometry':{'x':-117,'y':33}} for oid in chunk]})

    adapter=CaliforniaAdapter(LocalStore(tmp_path),test_mode=True)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        snap=adapter.fetch_snapshot(client,datetime.now(timezone.utc))
    assert batches==[ids[:500],ids[500:]]
    assert snap.complete_pages and snap.evidence['object_count']==501
    assert len(adapter.normalize(snap))==501

def test_collection_never_asserts_complete_coverage(tmp_path):
    adapter=CaliforniaAdapter(LocalStore(tmp_path),test_mode=True)
    snap=adapter.fetch_snapshot(Client(),datetime.now(timezone.utc))
    assert snap.complete_pages and not snap.evidence['coverage_complete']
    event=adapter.normalize(snap)[0]
    assert event.outage_type=='unknown' and event.onset_quality!='actual'
    assert event.onset.utcoffset().total_seconds()==0
    assert adapter.audit_snapshot(snap,None,{})==[]

def test_missing_page_fails(tmp_path):
    with pytest.raises(ValueError,match='missing page'):
        CaliforniaAdapter(LocalStore(tmp_path)).fetch_snapshot(Client(True),datetime.now(timezone.utc))


def test_unaudited_or_stale_snapshot_cannot_yield_qualified_onsets(tmp_path):
    adapter=CaliforniaAdapter(LocalStore(tmp_path),test_mode=True,policy={
        'type_mappings':{'SDGE':{'Not Planned':'unplanned'}},'type_audit_refs':{'SDGE':'reviewed'},
        'onset_audit_refs':{'SDGE':'reviewed'},'qualified_marker_utilities':['SDGE'],'geometry_audit_refs':{'SDGE':'reviewed'}})
    snap=adapter.fetch_snapshot(Client(),datetime.now(timezone.utc))
    assert not snap.evidence['source_fresh']
    assert all(e.onset_quality!='actual' for e in adapter.normalize(snap))

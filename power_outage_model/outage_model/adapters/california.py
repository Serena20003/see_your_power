"""Cal OES incident points. No historic backfill or inferred whole-cell coverage."""
from datetime import datetime, timezone
import json
from pathlib import Path
import time as clock
import httpx
from raster_pipeline.contracts import canonical, digest
from ..contracts import RawSnapshot, OutageEvent, time
from ..storage import checked_read, put_bytes

URL='https://services.arcgis.com/BLN4oKB0N1YSgvY8/arcgis/rest/services/Power_Outages_(View)/FeatureServer/0'
PIN=json.loads((Path(__file__).parents[1]/'configs/california-layer-v1.json').read_text())


def schema_signature(meta):
    return digest({'geometryType':meta['geometryType'],'objectIdField':meta['objectIdField'],
        'fields':[{k:f.get(k) for k in ('name','type','domain','description')} for f in meta['fields']]})


class CaliforniaAdapter:
    def __init__(self,store,*,policy=None,test_mode=False,page_size=500):
        self.store=store; self.policy=policy or {}; self.test_mode=test_mode; self.page_size=page_size
    def fetch_snapshot(self,client,observed_at):
        observed_at=time(observed_at)
        def get(path,params):
            for attempt in range(3):
                try:
                    response=client.get(URL+path,params={'f':'json',**params},timeout=30)
                    response.raise_for_status(); data=response.json()
                    put_bytes(self.store,'outage/v1/sources/california/requests',canonical({'uri':URL+path,'query':params,'response':data}),'json')
                    if 'error' in data: raise ValueError(f"ArcGIS error: {data['error']}")
                    return data
                except (OSError,ValueError,httpx.HTTPError) as exc:
                    if attempt==2: raise
                    clock.sleep(.2*(attempt+1))
        meta=get('',{})
        if schema_signature(meta)!=schema_signature(PIN): raise ValueError('California schema drift')
        oid=meta['objectIdField']
        before=get('/query',{'where':'1=1','returnIdsOnly':'true'})
        ids=before.get('objectIds')
        if ids is None or len(ids)!=len(set(ids)): raise ValueError('missing/duplicate object-ID inventory')
        if len(ids)>100000: raise ValueError('source inventory exceeds reviewed collection bound')
        records=[]; pages=[]
        for start in range(0,len(ids),self.page_size):
            chunk=sorted(ids)[start:start+self.page_size]
            page=get('/query',{'objectIds':','.join(map(str,chunk)),'outFields':'*','returnGeometry':'true','outSR':4326})
            if page.get('exceededTransferLimit'): raise ValueError('incomplete page')
            features=page.get('features',[])
            if sorted(f['attributes'][oid] for f in features)!=chunk: raise ValueError('missing page object IDs')
            wkid=page.get('spatialReference',{}).get('latestWkid',page.get('spatialReference',{}).get('wkid'))
            if wkid!=4326: raise ValueError('WGS84 conversion not confirmed')
            records.extend(features); pages.append(page)
        after=get('/query',{'where':'1=1','returnIdsOnly':'true'})
        if sorted(after.get('objectIds') or [])!=sorted(ids): raise ValueError('source changed during pagination; retry collection')
        updated=meta.get('editingInfo',{}).get('lastEditDate')
        updated_at=datetime.fromtimestamp(updated/1000,timezone.utc) if updated else None
        payload={'metadata':meta,'before':before,'pages':pages,'after':after}
        ref=put_bytes(self.store,'outage/v1/sources/california/raw',canonical(payload),'json')
        evidence={'object_count':len(ids),'enumerated_all_ids':True,'stable_ids_during_read':True,
            'source_fresh':bool(updated_at and 0<=(observed_at-updated_at).total_seconds()<=1800),
            'coverage_complete':False,'reason':'footprints, incident ID/onset and omission audits required'}
        snap=RawSnapshot(source='caloes',uri=URL,retrieved_at=observed_at,source_updated_at=updated_at,
            snapshot_id=digest({'raw':ref,'retrieved_at':observed_at.isoformat()}),raw_ref=ref,
            schema_hash=schema_signature(meta),adapter_version='caloes/v1',complete_pages=True,
            fixture=self.test_mode,evidence=evidence)
        self.store.put(f'outage/v1/sources/california/snapshots/{snap.snapshot_id}.json',canonical(snap))
        return snap
    def normalize(self,snapshot):
        if snapshot.fixture!=self.test_mode: raise ValueError('fixture/live source mixing forbidden')
        payload=json.loads(checked_read(self.store,snapshot.raw_ref)); result=[]
        for page in payload['pages']:
            for feature in page['features']:
                a=feature['attributes']; g=feature.get('geometry') or {}
                utility=a.get('UtilityCompany') or 'unknown'; event_id=a.get('IncidentId')
                mapping=self.policy.get('type_mappings',{}).get(utility,{})
                # Never approve Not Planned without utility-specific semantics audit.
                outage_type=mapping.get(a.get('OutageType'),'unknown')
                if outage_type=='unplanned' and not self.policy.get('type_audit_refs',{}).get(utility):
                    raise ValueError('unplanned mapping requires source audit')
                if a.get('OutageType')=='Planned': outage_type='planned'
                cause=(a.get('Cause') or '').lower()
                if 'public safety' in cause or 'psps' in cause: outage_type='psps'
                onset=datetime.fromtimestamp(a['StartDate']/1000,timezone.utc) if a.get('StartDate') is not None else None
                audited=self.policy.get('onset_audit_refs',{}).get(utility)
                quality='actual' if audited and snapshot.evidence.get('source_fresh') and snapshot.complete_pages else 'reported_system_time'
                geometry_ok=snapshot.evidence.get('source_fresh',False) and utility in self.policy.get('qualified_marker_utilities',[]) and bool(self.policy.get('geometry_audit_refs',{}).get(utility))
                result.append(OutageEvent(canonical_id=f'caloes:{utility}:{event_id or "missing-"+str(a.get("OBJECTID"))}',
                    source='caloes',utility_id=utility,original_event_id=event_id or '',onset=onset,
                    onset_quality=quality,first_seen=snapshot.retrieved_at,last_seen=snapshot.retrieved_at,
                    outage_type=outage_type,type_evidence=self.policy.get('type_audit_refs',{}).get(utility,''),
                    longitude=g.get('x'),latitude=g.get('y'),county=a.get('County'),
                    geometry_meaning='qualified_incident_marker' if geometry_ok else 'approximate_marker',
                    location_quality='audited' if geometry_ok else 'unverified',snapshot_ids=(snapshot.snapshot_id,)))
        return result
    def audit_snapshot(self,snapshot,previous,policy):
        # Transport checks alone can never establish omission/footprint/onset audits.
        # Reviewed CoverageIntervals enter through the separately pinned label policy.
        return []

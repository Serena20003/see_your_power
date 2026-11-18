"""Outcome-independent labels with indexed event/coverage history."""
from bisect import bisect_left,bisect_right
from collections import defaultdict
from datetime import timedelta,datetime,timezone
import math
from .contracts import EventLedger,LabelRow,DataReadinessError,time
from raster_pipeline.contracts import digest


def assign_cell(grid,lon,lat):
    if lon is None or lat is None or not (grid.west<=lon<grid.east and grid.south<lat<=grid.north):return None
    return math.floor((grid.north-lat)/grid.resolution),math.floor((lon-grid.west)/grid.resolution)


def _location_shift_m(prior,event):
    """Geodesic distance in metres between two events' coordinates, or None if either is missing."""
    if prior.longitude is None or prior.latitude is None or event.longitude is None or event.latitude is None:
        return None
    if prior.longitude==event.longitude and prior.latitude==event.latitude:
        return 0.0
    from pyproj import Geod
    _,_,dist=Geod(ellps='WGS84').inv(prior.longitude,prior.latitude,event.longitude,event.latitude)
    return abs(dist)


def reconcile_events(snapshots,policy):
    by_id={};bad=set();quarantine=[]
    tol=policy.location_revision_tolerance_m
    def quarantine_events(eid,reason,events):
        bad.add(eid)
        quarantine.append({'id':eid,'reason':reason,'events':[e.model_dump(mode='json') for e in events]})
    for events in snapshots:
        for event in events:
            eid=policy.duplicate_aliases.get(event.canonical_id,event.canonical_id);prior=by_id.get(eid)
            reason=None
            if not event.original_event_id:reason='missing_event_id'
            if prior:
                if prior.utility_id!=event.utility_id or prior.onset!=event.onset:reason='ambiguous_id_reuse_or_onset_revision'
                elif (prior.longitude,prior.latitude)!=(event.longitude,event.latitude):
                    shift=_location_shift_m(prior,event)
                    # tol==0 means strict equality (original behaviour): any shift quarantines.
                    # tol>0: only quarantine if shift is unknown (None) or meets/exceeds tolerance.
                    if tol==0 or shift is None or shift>=tol:reason='location_revision_requires_audit'
                elif prior.outage_type!=event.outage_type:reason='classification_revision_requires_audit'
            if reason:
                quarantine_events(eid,reason,[prior,event] if prior else [event]);continue
            if prior:
                priority={s:i for i,s in enumerate(policy.source_priority)}
                chosen=min((prior,event),key=lambda e:priority.get(e.source,999))
                # When coordinates shifted but stayed within tolerance, always
                # adopt the latest-seen location (event > prior by definition
                # since we iterate snapshots in order).
                location_updated=(prior.longitude,prior.latitude)!=(event.longitude,event.latitude)
                loc_override={'longitude':event.longitude,'latitude':event.latitude} if location_updated else {}
                by_id[eid]=chosen.model_copy(update={'canonical_id':eid,'first_seen':min(prior.first_seen,event.first_seen),
                    'last_seen':max(prior.last_seen,event.last_seen),'snapshot_ids':tuple(sorted(set(prior.snapshot_ids+event.snapshot_ids))),
                    **loc_override})
            else:by_id[eid]=event.model_copy(update={'canonical_id':eid})
    signatures=defaultdict(list)
    for eid,event in by_id.items():
        signatures[(event.utility_id,event.onset,event.longitude,event.latitude)].append((eid,event))
    for group in signatures.values():
        if len({e.source for _,e in group})>1:
            for eid,event in group:quarantine_events(eid,'ambiguous_cross_feed_duplicate',[event])
    return EventLedger(events=tuple(e for eid,e in by_id.items() if eid not in bad),quarantine=tuple(quarantine),policy_hash=digest(policy))


def build_labels(ledger,coverage,row_keys,freeze_time,policy,*,grid,windows,row_metadata):
    freeze_time=time(freeze_time);labels=[];coverage_by_cell=defaultdict(list);events_by_cell=defaultdict(list)
    unknown_by_cell=defaultdict(list);quarantine_by_cell=defaultdict(list)
    for c in coverage:
        if c.complete and c.whole_cell and c.ascertainment_audited and c.evidence:
            for cell in c.cells:coverage_by_cell[cell].append(c)
    for e in ledger.events:
        cell=assign_cell(grid,e.longitude,e.latitude)
        if cell is None or e.outage_type!='unplanned':continue
        (events_by_cell if e.onset is not None else unknown_by_cell)[cell].append(e)
    indexed={}
    for cell,events in events_by_cell.items():
        events.sort(key=lambda e:e.onset)
        maximum_span=max((max(timedelta(0),(e.onset_upper or e.onset)-e.onset) for e in events),default=timedelta(0))
        indexed[cell]=([e.onset for e in events],events,maximum_span)
    for q in ledger.quarantine:
        if not q.get('events'):raise DataReadinessError('quarantine without space/time context requires explicit review')
        for record in q['events']:
            cell=assign_cell(grid,record.get('longitude'),record.get('latitude'))
            if cell is None:raise DataReadinessError('unlocated quarantined incident requires explicit review')
            onset=time(record['onset']) if record.get('onset') else None
            upper=time(record['onset_upper']) if record.get('onset_upper') else onset
            quarantine_by_cell[cell].append((onset,upper,q['reason']))
    coverage_cache={}
    for key in row_keys:
        start=time(windows[key.archive_id])+timedelta(hours={'0_24':0,'24_48':24,'48_72':48}[key.horizon_id]);end=start+timedelta(hours=24)
        mature=end+timedelta(hours=policy.reporting_lag_hours);cell=(key.row,key.col);identity=(key.grid_id,*cell)
        cache_key=(identity,start,end)
        if cache_key not in coverage_cache:
            intervals=[c for c in coverage_by_cell[identity] if c.end>start and c.start<end];complete=[]
            for domain in sorted({c.domain_id for c in intervals}):
                cursor=start;used=[]
                for c in sorted((c for c in intervals if c.domain_id==domain),key=lambda c:c.start):
                    if c.start>cursor:break
                    cursor=max(cursor,c.end);used.append(c)
                if cursor>=end:complete.append((domain,used))
            coverage_cache[cache_key]=complete
        complete=coverage_cache[cache_key];reason=None
        if freeze_time<mature:reason='immature_label'
        elif len(complete)!=1:reason='incomplete_or_ambiguous_whole_cell_coverage'
        qualified=[];uncertain=False;events=list(unknown_by_cell[cell])
        if cell in indexed:
            times,known,span=indexed[cell]
            events.extend(known[bisect_left(times,start-span):bisect_left(times,end)])
        for e in events:
            if e.first_seen>freeze_time:uncertain=True;continue
            if not e.type_evidence or e.geometry_meaning not in policy.accepted_geometry_meanings or e.location_quality!='audited':uncertain=True;continue
            if e.onset_quality not in ('actual','bounded_after_complete_snapshot') or e.onset is None:uncertain=True;continue
            upper=e.onset_upper or e.onset
            if e.onset_quality=='bounded_after_complete_snapshot' and (upper>=end or e.onset<start<=upper):
                if e.onset<end and upper>=start:uncertain=True
                continue
            if start<=e.onset<end and upper<end:qualified.append(e.canonical_id)
        if uncertain and reason is None:reason='uncertain_incident_semantics'
        if any(onset is None or (onset<end and (upper or onset)>=start) for onset,upper,_ in quarantine_by_cell[cell]):
            reason=reason or 'unresolved_event_quarantine'
        meta=row_metadata[key.id];used=complete[0][1] if len(complete)==1 else []
        labels.append(LabelRow(key=key,start=start,end=end,label=None if reason else int(bool(qualified)),
            new_event_count=len(set(qualified)),coverage_ids=tuple(c.coverage_id for c in used),event_ids=tuple(sorted(set(qualified))),
            exclusion_reason=reason,mature_at=mature,freeze_time=freeze_time,domain_id=complete[0][0] if len(complete)==1 else 'unknown',
            state=meta['state'],region=meta['region'],block_id=meta['block_id'],season=meta['season']))
    return labels


def boundary_sensitivity(events,grid,radii_m=(100,500,1000)):
    """Location sensitivity only: never relocate an event or manufacture labels."""
    from pyproj import Geod
    geod=Geod(ellps='WGS84');result=[]
    for e in events:
        cell=assign_cell(grid,e.longitude,e.latitude)
        if cell is None:continue
        row,col=cell;x=e.longitude;y=e.latitude
        west=grid.west+col*grid.resolution;north=grid.north-row*grid.resolution
        distances=[abs(geod.inv(x,y,xx,yy)[2]) for xx,yy in ((west,y),(west+grid.resolution,y),(x,north),(x,north-grid.resolution))]
        nearest=min(distances)
        result.append({'event_id':e.canonical_id,'distance_to_cell_edge_m':nearest,
            'sensitivity':{str(radius):nearest<=radius for radius in radii_m},'policy':'sensitivity only; source marker retained'})
    return result

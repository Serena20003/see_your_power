"""Keep the latest complete batch and one recovery batch, with shared dependencies."""
from datetime import datetime,timezone,timedelta
import hashlib
import json
from .publish import LATEST,freshness,validate_batch
from .contracts import canonical

# This grace period applies only to incomplete staging files and small metadata,
# not to superseded complete forecast batches.
STAGING_GRACE=timedelta(days=7)


def _read_manifest(store,reference):
    raw,_=store.read(reference['manifest_key'])
    if raw is None:raise ValueError('retained batch manifest is missing')
    if hashlib.sha256(raw).hexdigest()!=reference['sha256']:
        raise ValueError('retained batch manifest checksum mismatch')
    manifest=json.loads(raw)
    if manifest['batch_id']!=reference['batch_id'] or manifest['quality_status']!='complete':
        raise ValueError('invalid retained batch identity/status')
    return manifest


def _legacy_previous(store,current,current_key,manifest_keys):
    """Old pointers did not record their predecessor. Verify an older fallback.

    Only legacy pointers use this path. New pointers record the actual previous
    successful publication atomically, so unpublished attempts cannot take it.
    """
    candidates=[]
    for key in manifest_keys:
        if key==current_key:continue
        raw,_=store.read(key)
        if raw is None:continue
        try:
            m=json.loads(raw)
            if m.get('quality_status')!='complete':continue
            if freshness(m)>freshness(current):continue
            published=datetime.fromisoformat(m['published_at'])
            if published>datetime.fromisoformat(current['published_at']):continue
            candidates.append((freshness(m),published,key,m))
        except (ValueError,KeyError,TypeError):continue
    for _,_,key,m in sorted(candidates,key=lambda c:c[:3],reverse=True):
        try:validate_batch(m,store)
        except (ValueError,KeyError,TypeError,OSError):continue
        return key,m
    return None


def collect_garbage(store,*,now=None,dry_run=True):
    now=now or datetime.now(timezone.utc)
    with store.reference_lock():
        keys=store.keys('raster/v1')
        raw,_=store.read(LATEST)
        # Without an established good publication, keep interrupted work intact.
        if raw is None:return []
        pointer=json.loads(raw)
        current=_read_manifest(store,pointer)
        manifest_keys={k for k in keys if k.startswith('raster/v1/runs/') and k.endswith('/manifest.json')}
        retained=[(pointer['manifest_key'],current)]
        previous=pointer.get('previous')
        if previous is not None:
            if previous['batch_id']==pointer['batch_id']:raise ValueError('duplicate recovery batch')
            retained.append((previous['manifest_key'],_read_manifest(store,previous)))
        elif pointer.get('schema_version')=='raster-latest/v1':
            fallback=_legacy_previous(store,current,pointer['manifest_key'],manifest_keys)
            if fallback is not None:retained.append(fallback)

        protected={LATEST,'raster/v1/source-catalog.json','raster/v1/eligible-run.json'}
        keep_static=set()
        for key,manifest in retained:
            protected.update((key,manifest['inputs']['key'],manifest['diagnostics_key']))
            keep_static.add(manifest['static_manifest']['key'])
            for item in manifest['intervals']+manifest.get('supporting_intervals',[]):
                protected.add(item['weather']['key'])
                if 'risk' in item:protected.add(item['risk']['key'])
        for key in keep_static:
            static_raw,_=store.read(key)
            if static_raw is None:raise ValueError('retained static manifest is missing')
            static=json.loads(static_raw);protected.add(key)
            protected.update(obj['key'] for obj in static['objects'].values())
            protected.add(f"raster/v1/grids/{static['grid']['grid_id']}/grid.json")

        def run_prefix(key):return '/'.join(key.split('/')[:4])+'/'
        completed_prefixes={key.rsplit('/',1)[0]+'/' for key in manifest_keys}
        retained_prefixes={key.rsplit('/',1)[0]+'/' for key,_ in retained}
        retired={}
        for key in keys:
            if key.startswith('raster/v1/retired/') and key.endswith('.json'):
                marker_raw,_=store.read(key)
                if marker_raw is None:raise ValueError('retirement record disappeared')
                marker=json.loads(marker_raw);retired[marker['prefix']]=(key,marker)
                protected.add(key)
        # Record each retired run before deleting its manifest. This preserves
        # shared-weather ownership and allows interrupted cleanup to resume
        # without mistaking leftover files for new staging attempts.
        new_markers=[]
        for prefix in sorted(completed_prefixes-retained_prefixes):
            if prefix in retired:continue
            key=f"raster/v1/retired/{prefix.rstrip('/').split('/')[-1]}.json"
            marker={'prefix':prefix,'retired_at':now.isoformat()}
            retired[prefix]=(key,marker);new_markers.append((key,marker))

        removed=[]
        for key in keys:
            if key in protected or key.endswith('.lock'):continue
            prefix=run_prefix(key)
            # Complete, unretained runs go immediately. Incomplete attempts have
            # a grace period so another worker can finish or retry. Shared COGs
            # are protected by object reference even inside an evicted run folder.
            immediate=key.startswith('raster/v1/raw/') or prefix in completed_prefixes
            if not immediate and prefix in retired:
                retired_at=datetime.fromisoformat(retired[prefix][1]['retired_at'])
                # New files in a retired folder may belong to a concurrent
                # retry. Only old, no-longer-referenced files go immediately.
                immediate=store.modified(key)<=retired_at
            if not immediate and now-store.modified(key)<=STAGING_GRACE:continue
            removed.append(key)
        remaining_prefixes={run_prefix(key) for key in keys if key.startswith('raster/v1/runs/') and key not in removed}
        transient_markers=[]
        for prefix,(key,_) in retired.items():
            if prefix in retained_prefixes or prefix not in remaining_prefixes:
                if key in keys:removed.append(key)
                else:transient_markers.append(key)
        if not dry_run:
            # Persist ownership records before deleting the original manifests,
            # so an interrupted cleanup remains safe to resume.
            for key,marker in new_markers:store.put(key,canonical(marker))
            for key in removed:store.delete(key)
            # These records were created above only for crash recovery. Remove
            # them last, after every file in the retired run was deleted.
            for key in transient_markers:store.delete(key)
        return removed

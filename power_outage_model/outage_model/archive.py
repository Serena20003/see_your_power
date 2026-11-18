"""Copy verified dependencies outside upstream GC before recording an archive."""
from datetime import timedelta
import hashlib
import io
import json
from pathlib import Path
import tempfile
import numpy as np
from raster_pipeline.contracts import (GridSpec, StaticManifest, WEATHER_BANDS,
    WEATHER_UNITS, WEATHER_SEMANTICS, NODATA, canonical, digest)
from raster_pipeline.publish import validate_batch
from raster_pipeline.rasters import validate_cog
from raster_pipeline.static import load_static
from .contracts import ArchiveRef, ArchivedBatch, time
from .storage import checked_read, put_bytes


def archive_batch(store, manifest_key: str, *, test_mode=False, raw_root=None, normalization_assets=None) -> ArchiveRef:
    raw,_=store.read(manifest_key)
    if raw is None: raise ValueError('missing upstream manifest')
    m=json.loads(raw)
    if m.get('fixture',False)!=test_mode: raise ValueError('fixture/live mixing forbidden')
    grid=GridSpec.model_validate(m['grid'])
    if not test_mode and grid!=GridSpec(): raise ValueError('incompatible CONUS grid')
    expected=[{'name':n,'unit':u,'temporal_semantics':s} for n,u,s in zip(WEATHER_BANDS,WEATHER_UNITS,WEATHER_SEMANTICS)]
    if m.get('bands',{}).get('weather')!=expected: raise ValueError('incompatible weather units/semantics')
    validate_batch(m,store)
    inv=json.loads(checked_read(store,m['inputs']))
    cutoff=time(m['cutoff'])
    for f in inv.get('fields',[]):
        if time(f['first_seen'])>cutoff or time(f['run'])>cutoff:
            raise ValueError('post-cutoff input availability')
    remapping={}
    def copy_ref(ref):
        original=ref['key']; data=checked_read(store,ref)
        new=put_bytes(store,'outage/v1/archive/objects',data,original.rsplit('.',1)[-1])
        remapping[original]=new
        return {**ref,**new}
    # Preserve original immutable bytes as well as the rewritten manifests.
    copy_ref({'key':manifest_key,'sha256':hashlib.sha256(raw).hexdigest()})
    static=json.loads(checked_read(store,m['static_manifest']))
    for name,obj in static['objects'].items(): static['objects'][name]=copy_ref(obj)
    static_ref=put_bytes(store,'outage/v1/archive/objects',canonical(static),'json')
    remapping[m['static_manifest']['key']]=static_ref
    rewritten=json.loads(raw)
    rewritten['static_manifest']=static_ref
    rewritten['inputs']=copy_ref(m['inputs'])
    for group in ('intervals','supporting_intervals'):
        for item in rewritten.get(group,[]):
            for kind in ('weather','risk'):
                if kind in item: item[kind]=copy_ref(item[kind])
    raw_inputs=[]; missing=[]
    for f in inv.get('fields',[]):
        sha=f.get('sha256'); key=f.get('archive_key')
        if key:
            raw_inputs.append(copy_ref({'key':key,'sha256':sha})); continue
        # Explicitly authorized local cache only, constrained below raw_root.
        path=Path(f['path']).resolve() if f.get('path') and raw_root else None
        if path and path.is_relative_to(Path(raw_root).resolve()) and path.is_file():
            data=path.read_bytes()
            if hashlib.sha256(data).hexdigest()!=sha: raise ValueError('raw checksum mismatch')
            raw_inputs.append(put_bytes(store,'outage/v1/archive/objects',data,'grib2'))
        else: missing.append({'sha256':sha,'uri':f.get('url')})
    assets=[];asset_gaps=[]
    specs=normalization_assets or []
    for spec in specs:
        if 'key' in spec:
            obj=copy_ref(spec)
        else:
            path=Path(spec['path']).resolve()
            if not raw_root or not path.is_relative_to(Path(raw_root).resolve()):
                raise ValueError('normalization asset outside explicit raw root')
            data=path.read_bytes()
            if hashlib.sha256(data).hexdigest()!=spec['sha256']:raise ValueError('normalization asset checksum mismatch')
            obj=put_bytes(store,'outage/v1/archive/objects',data,path.suffix.lstrip('.') or 'bin')
        assets.append({**obj,'role':spec['role']})
    hashes={x['sha256'] for x in assets}
    expected_assets={r['weights_sha256'] for r in inv.get('remaps',[]) if r.get('weights_sha256')}
    native=m.get('weather_recipe',{}).get('native_grid_sha256')
    if native:expected_assets.add(native)
    asset_gaps=sorted(expected_assets-hashes)
    # Upstream no longer retains raw inputs. Reconstruction from normalized
    # COGs is possible; normalization reproducibility must remain a separate flag.
    archive={'schema_version':'outage-archive/v1','original_manifest_sha256':hashlib.sha256(raw).hexdigest(),
        'original_manifest_key':manifest_key,'upstream':rewritten,'remapping':remapping,
        'raw_inputs':raw_inputs,'missing_raw_inputs':missing,
        'normalization_assets':assets,'missing_normalization_asset_hashes':asset_gaps,
        'feature_reconstruction_available':True,
        'normalization_reproducible':bool(inv.get('fields')) and not missing and not asset_gaps,
        'fixture':test_mode}
    aid=digest(archive); key=f'outage/v1/archive/manifests/{aid}.json'
    data=canonical(archive);store.put(key,data)
    return ArchiveRef(archive_id=aid,manifest_key=key,sha256=hashlib.sha256(data).hexdigest(),
        fixture=test_mode,normalization_reproducible=archive['normalization_reproducible'])


def load_archive(store,ref: ArchiveRef,*,test_mode=False) -> ArchivedBatch:
    if ref.fixture!=test_mode: raise ValueError('fixture/live archive mixing forbidden')
    a=json.loads(checked_read(store,{'key':ref.manifest_key,'sha256':ref.sha256}))
    identity=dict(a)
    if digest(identity)!=ref.archive_id or a['fixture']!=test_mode: raise ValueError('archive identity mismatch')
    if ref.normalization_reproducible!=a['normalization_reproducible']: raise ValueError('archive reproducibility mismatch')
    for obj in a.get('raw_inputs',[])+a.get('normalization_assets',[]):checked_read(store,obj)
    m=a['upstream']; context=validate_batch(m,store); grid=GridSpec.model_validate(m['grid'])
    static_manifest=json.loads(checked_read(store,m['static_manifest']))
    land_ref=static_manifest['objects'].get('analysis-land.geojson')
    land=json.loads(checked_read(store,land_ref)) if land_ref else None
    values=[]; scores=[]
    with tempfile.TemporaryDirectory() as tmp:
        for item in m['intervals']:
            for kind,bands,units,dest in [('weather',WEATHER_BANDS,WEATHER_UNITS,values),('risk',['risk_score'],['1'],scores)]:
                p=Path(tmp)/(kind+'.tif');p.write_bytes(checked_read(store,item[kind]))
                data,_=validate_cog(p,grid,bands,units);dest.append(data)
    return ArchivedBatch(ref,m,grid,np.stack(values),context,np.stack(scores)[:,0],
        {'archive':a,'analysis_land_geometry':land,'inputs':json.loads(checked_read(store,m['inputs']))})

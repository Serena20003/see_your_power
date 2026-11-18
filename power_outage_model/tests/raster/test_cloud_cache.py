import json
from raster_pipeline.cloud_job import restore_catalog_observations,persist_catalog_observations
from raster_pipeline.storage import LocalStore
from raster_pipeline.contracts import canonical,digest
from test_source import catalog


def test_cloud_catalog_persists_only_metadata_and_restores_legacy_observations(tmp_path):
    store=LocalStore(tmp_path/'store')
    field=catalog()[0].model_copy(update={'sha256':'a'*64,'path':'/gone/source.grib2'})
    store.put('raster/v1/source-catalog.json',canonical([
        {**field.model_dump(mode='json'),'raw_key':'raster/v1/raw/removed.grib2','cache_name':'old.grib2'}]))
    root=tmp_path/'cache';restore_catalog_observations(store,root)
    side=root/(digest({'url':field.url})+'.json')
    saved=json.loads(side.read_text())
    assert saved['first_seen']==field.model_dump(mode='json')['first_seen']
    assert not list(root.glob('*.grib2'))
    persist_catalog_observations([field],store)
    records=json.loads(store.read('raster/v1/source-catalog.json')[0])
    assert 'raw_key' not in records[0] and 'cache_name' not in records[0]
    assert store.keys('raster/v1/raw')==[]

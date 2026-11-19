from datetime import timedelta
import numpy as np
import pytest
from raster_pipeline.weather import normalize_interval, precipitation_amount
from raster_pipeline.remap import geographic_order
from raster_pipeline.decode import inspect_grib


def test_units_vector_and_precipitation_boundaries():
    a=np.full((2,2),10.,dtype='float64'); b=a+2
    result,valid=normalize_interval(a,b,np.full_like(a,273.15),np.full_like(a,3),np.full_like(a,4))
    np.testing.assert_allclose(result[:,0,0],[2,0,5],atol=1e-5)
    assert valid.all()


def test_precipitation_reset_and_missing_are_not_dry():
    assert precipitation_amount(np.array([1.]),np.array([.99995]))[0]==0
    with pytest.raises(ValueError,match='negative'): precipitation_amount(np.array([10.]),np.array([2.]))
    _,valid=normalize_interval(np.array([[np.nan]]),np.array([[12.]]),np.array([[280.]]),np.zeros((1,1)),np.zeros((1,1)))
    assert not valid.any()


def test_negative_accumulation_rejected_even_if_difference_positive():
    with pytest.raises(ValueError,match='negative'): precipitation_amount(np.array([-10.]),np.array([-9.]))


def test_precipitation_uses_bounded_grib_packing_uncertainty():
    # Measured ICON steps 1 and 2: quantization error bounds from ecCodes.
    tolerance=.00048828125+.0009765625
    before=np.array([.05984563246892705]);after=np.array([.058869069968865095])
    assert precipitation_amount(before,after,tolerance=tolerance)[0]==0
    with pytest.raises(ValueError,match='negative'):
        precipitation_amount(np.array([1.]),np.array([.99]),tolerance=tolerance)
    with pytest.raises(ValueError):precipitation_amount(before,after,tolerance=float('inf'))


def test_longitude_wrapping_and_north_up():
    a=np.array([[1,2,3],[4,5,6]])
    values,lons,lats=geographic_order(a,np.array([235,236,237]),np.array([24,25]))
    assert lons.tolist()==[-125,-124,-123]
    assert lats.tolist()==[25,24]
    assert values.tolist()==[[4,5,6],[1,2,3]]


def test_grib_decode_real_format(tmp_path):
    import eccodes as e
    g=e.codes_grib_new_from_samples('regular_ll_sfc_grib2')
    e.codes_set(g,'shortName','2t'); e.codes_set(g,'dataDate',20261116); e.codes_set(g,'dataTime',0)
    e.codes_set(g,'forecastTime',1)
    path=tmp_path/'temperature.grib2'
    with path.open('wb') as out:e.codes_write(g,out)
    e.codes_release(g)
    m=inspect_grib(path)
    assert m['field']=='T_2M' and m['level']==2 and m['end_step']==1
    assert m['units']=='K'
    assert np.isfinite(m['metadata']['packingError']) and m['metadata']['packingError']>=0


def test_cdo_native_remap_and_grid_specific_weights(tmp_path):
    import shutil,os
    if not shutil.which('cdo'):
        if os.environ.get('RASTER_REQUIRE_NATIVE')=='1':pytest.fail('CDO required in native verification')
        pytest.skip('native CDO integration runs in the dedicated container')
    import eccodes as e
    from raster_pipeline.contracts import GridSpec,SourceField
    from raster_pipeline.remap import CDORemapper
    from datetime import datetime,timezone
    grid=GridSpec(grid_id='tiny-remap',west=-120,south=34,east=-118,north=35,resolution=1)
    g=e.codes_grib_new_from_samples('regular_ll_sfc_grib2')
    for key,value in [('Ni',4),('Nj',3),('latitudeOfFirstGridPointInDegrees',35.5),('latitudeOfLastGridPointInDegrees',33.5),
        ('longitudeOfFirstGridPointInDegrees',239.5),('longitudeOfLastGridPointInDegrees',242.5),
        ('iDirectionIncrementInDegrees',1),('jDirectionIncrementInDegrees',1),('paramId',167),('dataDate',20261116),('dataTime',0),('forecastTime',1)]:e.codes_set(g,key,value)
    e.codes_set_values(g,np.array([10,11,12,13,20,21,22,23,30,31,32,33.]))
    path=tmp_path/'native.grib2'
    with path.open('wb') as f:e.codes_write(g,f)
    e.codes_release(g)
    m=inspect_grib(path)
    f=SourceField(**m,url='fixture://native',first_seen=datetime(2026,11,16,6,tzinfo=timezone.utc),path=str(path))
    r=CDORemapper(grid,tmp_path/'weights')
    np.testing.assert_allclose(r(f),[[21,22]],atol=.01)
    assert len(r.records)==1 and r.records[0]['weights_sha256']


def test_icon_global_wind_orientation_requires_verified_product_metadata():
    import json
    from pathlib import Path
    from raster_pipeline.decode import wind_reference
    meta=json.loads((Path(__file__).parents[2]/'fixtures/raster/icon-wind-metadata.json').read_text())
    assert wind_reference(meta)=='earth'
    with pytest.raises(ValueError):wind_reference({**meta,'uuidOfHGrid':'unknown'})
    with pytest.raises(ValueError):wind_reference({**meta,'centre':'unknown'})
    with pytest.raises(ValueError):wind_reference({**meta,'uvRelativeToGrid':1})


def test_zero_minute_precipitation_boundary_is_valid_but_subhour_endpoint_is_not(tmp_path):
    import eccodes as e
    g=e.codes_grib_new_from_samples('regular_ll_sfc_grib2');e.codes_set(g,'paramId',228228)
    e.codes_set(g,'dataDate',20261116);e.codes_set(g,'dataTime',0);e.codes_set(g,'stepUnits',0)
    e.codes_set(g,'startStep',0);e.codes_set(g,'endStep',0)
    zero=tmp_path/'zero.grib2'
    with zero.open('wb') as out:e.codes_write(g,out)
    assert inspect_grib(zero)['end_step']==0
    e.codes_set(g,'endStep',90)
    partial=tmp_path/'partial.grib2'
    with partial.open('wb') as out:e.codes_write(g,out)
    e.codes_release(g)
    with pytest.raises(ValueError,match='hour'):inspect_grib(partial)


def test_barycentric_interpolation_preserves_linear_fields_and_invalids():
    from raster_pipeline.remap import triangle_weights,apply_triangle_weights
    points=np.array([[0,0],[2,0],[0,2],[2,2]],dtype=float)
    targets=np.array([[.5,.5],[1.5,1.5]])
    indices,weights=triangle_weights(points,targets)
    a=apply_triangle_weights(np.array([0,2,4,6.]),indices,weights)
    np.testing.assert_allclose(a,[1.5,4.5])
    with pytest.raises(ValueError,match='coverage'):triangle_weights(points,np.array([[3.,3.]]))
    with pytest.raises(ValueError,match='missing'):
        apply_triangle_weights(np.full(4,np.nan),indices,weights)

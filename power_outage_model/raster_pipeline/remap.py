"""Conservative precipitation and topology-appropriate scalar interpolation."""
import os
from pathlib import Path
import subprocess
import tempfile
import numpy as np
from .contracts import digest
from .download import file_sha


def _text(value):
    return value.decode('ascii') if isinstance(value,bytes) else str(value)


def geographic_order(values,longitudes,latitudes):
    lons=(np.asarray(longitudes)+180)%360-180
    xs=np.argsort(lons);ys=np.argsort(latitudes)[::-1]
    return values[...,ys,:][...,xs],lons[xs],np.asarray(latitudes)[ys]


def triangle_weights(points,targets):
    from scipy.spatial import Delaunay
    points=np.asarray(np.ma.filled(points,np.nan),dtype='float64')
    targets=np.asarray(np.ma.filled(targets,np.nan),dtype='float64')
    if not np.isfinite(points).all() or not np.isfinite(targets).all():raise ValueError('invalid triangular grid coordinates')
    mesh=Delaunay(points)
    simplex=mesh.find_simplex(targets)
    if (simplex<0).any():raise ValueError('triangular remap lacks target coverage')
    transform=mesh.transform[simplex]
    bary=np.einsum('nij,nj->ni',transform[:,:2],targets-transform[:,2])
    weights=np.c_[bary,1-bary.sum(axis=1)]
    if not np.isfinite(weights).all() or (weights < -1e-10).any():raise ValueError('invalid triangular remap weights')
    return mesh.simplices[simplex],weights


def apply_triangle_weights(values,indices,weights):
    samples=np.asarray(values)[indices]
    # Missing required source vertices are never silently zero-filled.
    if not np.isfinite(samples).all():raise ValueError('missing triangular source values')
    return np.sum(samples*weights,axis=1,dtype='float64')


class CDORemapper:
    def __init__(self,grid,cache_dir,native_grid=None,native_grid_sha256=None):
        self.grid=grid;self.root=Path(cache_dir);self.root.mkdir(parents=True,exist_ok=True)
        self.native_grid=native_grid;self.native_sha=native_grid_sha256
        self.records=[];self._recorded=set();self._geometry_signature=None
        self.version=subprocess.run(['cdo','-V'],capture_output=True,text=True,check=True).stderr
        self.target=self.root/(digest(grid)+'.txt')
        self.target.write_text(f'gridtype = lonlat\nxsize = {grid.width}\nysize = {grid.height}\nxfirst = {grid.west+grid.resolution/2}\nxinc = {grid.resolution}\nyfirst = {grid.north-grid.resolution/2}\nyinc = {-grid.resolution}\n')

    def _verify_geometry(self,meta):
        if not self.native_grid or not self.native_sha:raise ValueError('pinned ICON native geometry and checksum required')
        path=Path(self.native_grid);stat=path.stat();signature=(stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)
        if self._geometry_signature is None:
            if file_sha(path)!=self.native_sha:raise ValueError('ICON geometry checksum mismatch')
            self._geometry_signature=signature
        elif self._geometry_signature!=signature:raise ValueError('ICON geometry changed during run')
        from scipy.io import netcdf_file
        with netcdf_file(path,mmap=True) as ds:
            uuid=_text(ds.uuidOfHGrid).replace('-','').lower()
            if uuid!=meta['metadata']['uuidOfHGrid'].replace('-','').lower():raise ValueError('ICON geometry UUID mismatch')
            if ds.variables['clon'].shape[0]!=meta['metadata']['numberOfDataPoints']:raise ValueError('ICON geometry point-count mismatch')

    def _record(self,weights,identity):
        if str(weights) not in self._recorded:
            self.records.append({**identity,'weights_sha256':file_sha(weights),'weights_path':str(weights)})
            self._recorded.add(str(weights))

    def _triangular(self,field):
        import scipy
        from scipy.io import netcdf_file
        from pyproj import Transformer
        from .decode import read_values
        identity={'source_grid':field.grid_id,'target':self.grid.model_dump(),
            'geometry_sha256':self.native_sha,'method':'triangle_barycentric_v1',
            'projection':'EPSG:5070','padding_degrees':2,'scipy':scipy.__version__}
        weights=self.root/(digest(identity)+'.npz')
        if not weights.exists():
            with netcdf_file(self.native_grid,mmap=True) as ds:
                if not _text(ds.variables['clon'].units).startswith('radian') or not _text(ds.variables['clat'].units).startswith('radian'):
                    raise ValueError('unsupported ICON geometry coordinate units')
                lons=(np.rad2deg(ds.variables['clon'][:])+180)%360-180;lats=np.rad2deg(ds.variables['clat'][:])
            g=self.grid
            selected=np.flatnonzero((lons>=g.west-2)&(lons<=g.east+2)&(lats>=g.south-2)&(lats<=g.north+2))
            project=Transformer.from_crs(4326,5070,always_xy=True)
            x,y=project.transform(lons[selected],lats[selected]);points=np.c_[x,y]
            tx,ty=np.meshgrid(g.west+(np.arange(g.width)+.5)*g.resolution,g.north-(np.arange(g.height)+.5)*g.resolution)
            x,y=project.transform(tx.ravel(),ty.ravel())
            indices,bary=triangle_weights(points,np.c_[x,y]);indices=selected[indices]
            with tempfile.TemporaryDirectory(dir=self.root) as tmp:
                candidate=Path(tmp)/'weights.npz';np.savez(candidate,indices=indices,weights=bary)
                os.replace(candidate,weights)
        self._record(weights,identity)
        with np.load(weights) as data:
            out=apply_triangle_weights(read_values(field.path),data['indices'],data['weights'])
        return out.reshape(self.grid.shape)

    def __call__(self,field):
        from .decode import inspect_grib,verify_metadata
        meta=inspect_grib(field.path);verify_metadata(field,meta)
        native=meta['metadata']['gridType']
        if native not in ('unstructured_grid','regular_ll'):raise ValueError('unsupported native grid orientation')
        source=[]
        if native=='unstructured_grid':
            self._verify_geometry(meta)
            if field.field!='TOT_PREC':return self._triangular(field)
            source=[f'-setgrid,{self.native_grid}']
        method='con' if field.field=='TOT_PREC' else 'bil'
        identity={'source_grid':field.grid_id,'target':self.grid.model_dump(),
            'geometry_sha256':self.native_sha,'method':method,'cdo':self.version}
        weights=self.root/(digest(identity)+'.nc')
        env={**os.environ,'REMAP_EXTRAPOLATE':'off','CDO_REMAP_NORM':'destarea'}
        def execute(command):
            result=subprocess.run(command,env=env,capture_output=True,text=True)
            if result.returncode:raise ValueError(f'CDO remap failed: {result.stderr.strip()}')
        if not weights.exists():
            with tempfile.TemporaryDirectory(dir=self.root) as tmp:
                candidate=Path(tmp)/'weights.nc'
                execute(['cdo','-s',f'gen{method},{self.target}',*source,field.path,str(candidate)])
                os.replace(candidate,weights)
        self._record(weights,identity)
        with tempfile.TemporaryDirectory(dir=self.root) as tmp:
            output=Path(tmp)/'remapped.nc'
            # Float64 NetCDF avoids another lossy GRIB packing round before
            # cumulative precipitation is differenced within its source run.
            execute(['cdo','-s','-b','F64','-f','nc2',f'remap,{self.target},{weights}',*source,field.path,str(output)])
            from scipy.io import netcdf_file
            with netcdf_file(output,mmap=False) as ds:
                data=[v for name,v in ds.variables.items() if len(v.shape)>=2 and v.shape[-2:]==self.grid.shape and name not in ('lat','lon')]
                if len(data)!=1:raise ValueError('ambiguous remapped field dimensions')
                result=np.array(data[0][:],dtype='float64',copy=True).reshape(self.grid.shape)
                for name in ('_FillValue','missing_value'):
                    missing=getattr(data[0],name,None)
                    if missing is not None:result[result==missing]=np.nan
        if not np.isfinite(result).all():raise ValueError('remapping left invalid target coverage')
        return result

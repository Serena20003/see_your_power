from pathlib import Path
import json
import numpy as np
from pydantic import model_validator
from .contracts import Contract,RiskResult,NODATA


class RiskConfig(Contract):
    method: str = 'environmental_heuristic_v2'
    wind: tuple[float,float] = (10,25)
    rain: tuple[float,float] = (1,10)
    heat: tuple[float,float] = (30,45)
    cold: tuple[float,float] = (-15,0)
    weights: tuple[float,float,float] = (.5,.3,.2)
    canopy_weight: float = .1

    @model_validator(mode='after')
    def validate_config(self):
        for lo,hi in (self.wind,self.rain,self.heat,self.cold):
            if not np.isfinite([lo,hi]).all() or lo>=hi:raise ValueError('invalid ramp bounds')
        if not np.isfinite(self.weights).all() or min(self.weights)<0 or not np.isclose(sum(self.weights),1,atol=1e-10,rtol=0):
            raise ValueError('nonnegative weights must sum to one')
        if not np.isfinite(self.canopy_weight) or self.canopy_weight<0:raise ValueError('invalid canopy weight')
        if self.method!='environmental_heuristic_v2':raise ValueError('unsupported risk method')
        return self


def load_risk(path=None):
    return RiskConfig.model_validate(json.loads(Path(path).read_text())) if path else RiskConfig()


class RiskCalculator:
    def calculate(self,weather_window,static_context,config):
        config=RiskConfig.model_validate(config)
        a=np.asarray(weather_window.values,dtype='float64');s=static_context
        if a.ndim!=4 or a.shape[1:]!=(3,*s.grid.shape):raise ValueError('weather/static shape mismatch')
        valid=np.all(weather_window.valid & np.isfinite(a) & (a!=NODATA),axis=(0,1))
        valid &= s.analysis_mask & np.isfinite(s.canopy) & (s.canopy>=0)&(s.canopy<=1)
        valid &= np.isfinite(s.valid_land_fraction)&(s.valid_land_fraction>=.95)&(s.valid_land_fraction<=1)
        valid &= s.coverage[1]==1
        # Negative precipitation or speed is invalid, never a negative driver.
        valid &= np.all((a[:,0]>=0)&(a[:,2]>=0),axis=0)
        safe=np.where(np.isfinite(a)&(a!=NODATA),a,0)
        def ramp(x,bounds):return np.clip((x-bounds[0])/(bounds[1]-bounds[0]),0,1)
        wind=ramp(np.max(safe[:,2],axis=0),config.wind)
        rain=ramp(np.mean(safe[:,0],axis=0),config.rain)
        heat=ramp(np.max(safe[:,1],axis=0),config.heat)
        cold=1-ramp(np.min(safe[:,1],axis=0),config.cold)
        temperature=np.maximum(heat,cold)
        base=sum(w*d for w,d in zip(config.weights,(wind,rain,temperature)))
        canopy=config.canopy_weight*wind*np.where(valid,s.canopy,0)
        score=np.where(valid,np.clip(base+canopy,0,1),NODATA).astype('float32')
        diagnostics={k:np.where(valid,v,NODATA).astype('float32') for k,v in {
            'wind_driver':wind,'rain_driver':rain,'temperature_driver':temperature,
            'base_score':base,'canopy_contribution':canopy,
            'cumulative_precipitation_mm':safe[:,0].sum(axis=0)}.items()}
        return RiskResult(score,valid,diagnostics)


def calculate_batch(cube,windows,static_context,calculator,config=None):
    if cube.grid!=static_context.grid:raise ValueError('static grid mismatch')
    for window in windows:
        yield calculator.calculate(window,static_context,config or RiskConfig())

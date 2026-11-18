from datetime import date
from pathlib import Path
import numpy as np
from pydantic import BaseModel,ConfigDict,Field
from ..storage import BudgetStore,LocalStore,tree_bytes

ROOT=Path(__file__).resolve().parents[2]
PREFIX='outage/experimental-random-points/v1'
FEATURES=('longitude','latitude','season_sin','season_cos','weekday_sin','weekday_cos','log_county_area_km2','horizon_days')
SEMANTICS={
    'experimental':True,'locations_simulated':True,
    'target':'allocated_customer_impact_conditional_on_available_county_record',
    'output_unit':'customer-equivalent weight per simulated representative point',
    'physical_outage_probability':False,'incident_location_validation':False,
    'sampling_support':'generalized_county_polygon_including_unclipped_inland_water',
    'training_weather':False,'training_canopy':False,
    'negative_label_policy':'no missing county records are imputed as zero',
}

class Frozen(BaseModel):
    model_config=ConfigDict(frozen=True,extra='forbid',allow_inf_nan=False)

class CountyDay(Frozen):
    county_fips:str
    day:date
    reported_peak_customers:float=Field(ge=0)
    received_records:int=Field(ge=1)

class SamplingPolicy(Frozen):
    version:str='county-impact-uniform-equal-area/v1'
    seed:int=42
    points_per_parent:int=Field(default=8,ge=1,le=32)
    max_attempt_batches:int=Field(default=10000,ge=1)


def budget_store(root=ROOT):
    return BudgetStore(LocalStore(Path(root)/'output'),root,limit=10_000_000_000)


def check_headroom(root=ROOT,reserve=100_000_000):
    used=tree_bytes(root)
    if used+reserve>10_000_000_000:raise ValueError(f'insufficient local storage headroom: {used} bytes already present')
    return used

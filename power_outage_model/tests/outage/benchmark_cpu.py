"""Run from power_outage_model: python -m tests.outage.benchmark_cpu.

CPU mechanics benchmark with generated TEST features/models only; not accuracy,
weather integration, a trained production model or a deployment benchmark.
"""
import json
import resource
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import numpy as np
import lightgbm as lgb
from outage_model.contracts import HORIZONS
from outage_model.train import raw_margins
from .test_explain import model_bundle


def main():
    dataset,bundle=model_bundle();rng=np.random.default_rng(42)
    cells=472*208;X=rng.normal(size=(cells,17)).astype('float32')
    scoring={};explanations={}
    for h in HORIZONS:
        model=lgb.Booster(model_str=bundle.trees[h]);start=time.perf_counter()
        margins=model.predict(X,raw_score=True,num_threads=4,num_iteration=model.current_iteration())
        scoring[h]=time.perf_counter()-start;start=time.perf_counter()
        contrib=model.predict(X,pred_contrib=True,num_threads=4,num_iteration=model.current_iteration())
        explanations[h]=time.perf_counter()-start
        if not np.allclose(contrib.sum(1),margins,atol=1e-6,rtol=0):raise RuntimeError('benchmark reconstruction failed')
    return {'fixture':True,'purpose':'test-only native CPU mechanics; not live forecast/accuracy evidence',
        'cells_per_horizon':cells,'features':17,'threads':4,'native_scoring_seconds':scoring,
        'full_contribution_seconds':explanations,'peak_rss_native_units':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        'rss_unit':'bytes on macOS; KiB on Linux','excludes':'archive/COG IO, feature/area extraction and support nearest-neighbor queries'}

if __name__=='__main__':print(json.dumps(main(),sort_keys=True))

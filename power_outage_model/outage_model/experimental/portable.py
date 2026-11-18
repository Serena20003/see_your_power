"""Small inference-only package, excluding source CSV and training matrices."""
import io,json,zipfile,hashlib
from pathlib import Path
from raster_pipeline.contracts import canonical
from ..storage import checked_read,put_bytes
from .contracts import PREFIX
from .train import load_model


def export_bundle(store,model_ref,*,test_mode=False):
    _,meta=load_model(store,model_ref,test_mode=test_mode)
    refs=[model_ref,meta['native'],meta['geometry_ref'],meta['evaluation_ref']]
    dataset=json.loads(checked_read(store,meta['dataset_ref']))
    history_ref=dataset.get('history_ref')
    if history_ref:history=json.loads(checked_read(store,history_ref))
    elif test_mode:history={'fixture':True,'source':'test-only; no real observations'}
    else:raise ValueError('portable real model requires actual source provenance')
    assumptions=(Path(__file__).parents[1]/'docs/EXPERIMENTAL_RANDOM_POINTS.md').read_bytes()
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        archive.writestr('model-ref.json',canonical(model_ref))
        archive.writestr('ASSUMPTIONS.md',assumptions)
        archive.writestr('provenance/source-history-manifest.json',canonical(history))
        archive.writestr('provenance/dataset-manifest.json',canonical(dataset))
        for ref in refs:
            archive.writestr('output/'+ref['key'],checked_read(store,ref))
        archive.writestr('README.txt',
            'EXPERIMENTAL SIMULATED-LOCATION MODEL\n'
            'Observed county counts are real; within-county points are randomly allocated.\n'
            'Outputs are conditional allocated customer impact, not physical outage probabilities.\n'
            'No weather or canopy predictors were trained. Calendar/geography only.\n'
            'Unzip preserving output/ object keys and model-ref.json. Use the matching\n'
            'outage_model.experimental Python package and requirements-model.lock plus\n'
            'requirements-experimental.txt. See EXPERIMENTAL_RANDOM_POINTS.md.\n'
            'Dataset/source references in the manifest describe provenance but the large\n'
            'training matrices/raw CSV are intentionally not included or needed for inference.\n')
    ref=put_bytes(store,f'{PREFIX}/exports',output.getvalue(),'zip')
    return {'bundle_ref':ref,'model_id':meta['model_id'],'inference_only':True,'experimental':True,'locations_simulated':True}

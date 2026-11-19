"""Run the pinned fitted model and publish daily county impact for the web app."""
from datetime import date, datetime, timezone
from pathlib import Path
import argparse
import hashlib
import json
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BUNDLE = ROOT / 'artifacts/experimental-point-model-4e97b613.zip'
BUNDLE_SHA256 = 'a179ba28e681273333de4bc635357c9754c6c2d8199892cf32b4fe92293e6268'
MODEL_ID = '4e97b613a2ff4aebeb7d63d4140c397972c2c832983611ac099a7db2d0d0d572'
STATES = dict(zip('01 02 04 05 06 08 09 10 11 12 13 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 44 45 46 47 48 49 50 51 53 54 55 56'.split(),
                      'AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY'.split()))


def generate(anchor: date):
    from outage_model.storage import LocalStore, checked_read
    from outage_model.experimental.train import load_model
    from outage_model.experimental.predict import predict_counties
    raw = BUNDLE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != BUNDLE_SHA256:
        raise ValueError('Pinned model bundle checksum mismatch')
    with tempfile.TemporaryDirectory(prefix='experimental-impact-') as tmp:
        root = Path(tmp).resolve()
        with zipfile.ZipFile(BUNDLE) as archive:
            for name in archive.namelist():
                if not (root / name).resolve().is_relative_to(root):
                    raise ValueError('Unsafe bundle path')
            archive.extractall(root)
        store = LocalStore(root / 'output')
        ref = json.loads((root / 'model-ref.json').read_bytes())
        _, meta = load_model(store, ref)
        if meta['model_id'] != MODEL_ID or meta['training_weather'] or meta['training_canopy']:
            raise ValueError('Unexpected model identity or feature semantics')
        prediction = predict_counties(store, ref, anchor.isoformat(), meta['supported_county_fips'])
        geometry = json.loads(checked_read(store, meta['geometry_ref']))
    counties = {}
    for feature in geometry['features']:
        props = feature['properties']; fips = props['GEOID']
        counties[fips] = {'name': props['NAME'], 'state': STATES.get(fips[:2], fips[:2]), 'windows': []}
    for row in prediction['records']:
        counties[row['county_fips']]['windows'].append({
            'start': row['window_start_utc'], 'end': row['window_end_utc'],
            'estimated_customer_impact': row['allocated_county_customer_impact'],
            'within_training_feature_ranges': row['within_training_feature_ranges'],
        })
    supported = sum(len(c['windows']) == 3 for c in counties.values())
    if supported != len(meta['supported_county_fips']):
        raise ValueError('Incomplete model export')
    return {'schema_version': 'experimental-impact-web/v1', 'model_id': MODEL_ID,
            'bundle_sha256': BUNDLE_SHA256, 'fixture': False, 'experimental': True,
            'physical_outage_probability': False, 'locations_simulated': True,
            'training_weather': False, 'training_canopy': False,
            'anchor_date': anchor.isoformat(), 'published_at': datetime.now(timezone.utc).isoformat(),
            'history_year': meta['history_year'], 'calendar_extrapolation': prediction['calendar_extrapolation'],
            'windows_are_fixed_utc_days': True, 'supported_counties': supported,
            'unsupported_counties': sorted(f for f, c in counties.items() if not c['windows']),
            'limitations': 'Experimental typical customer impact using calendar and geography. Simulated within-county locations; strongly underestimates severe totals. Not outage probability or household risk. Weather and canopy are separate layers.',
            'counties': counties}


def publish(data, bucket, client=None):
    from google.cloud import storage
    from google.api_core.exceptions import NotFound
    client = client or storage.Client(project='runway-515115')
    target = client.bucket(bucket)
    raw = json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    key = 'experimental-impact/batches/' + hashlib.sha256(raw).hexdigest() + '.json'
    blob = target.blob(key); blob.cache_control = 'public, max-age=31536000, immutable'
    blob.upload_from_string(raw, content_type='application/json', checksum='crc32c', if_generation_match=0)
    latest = target.blob('experimental-impact/latest.json')
    try:
        latest.reload(); generation = int(latest.generation)
        current = json.loads(latest.download_as_bytes(if_generation_match=generation))
        if current['anchor_date'] > data['anchor_date']:
            raise ValueError('Refusing to replace a newer daily forecast')
    except NotFound:
        generation = 0
    latest.cache_control = 'no-store'
    latest.upload_from_string(raw, content_type='application/json', checksum='crc32c', if_generation_match=generation)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', type=date.fromisoformat, default=datetime.now(timezone.utc).date())
    parser.add_argument('--output', type=Path, default=ROOT / '.cache/experimental-impact/latest.json')
    parser.add_argument('--bucket')
    args = parser.parse_args()
    data = generate(args.date)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, separators=(',', ':'), allow_nan=False))
    if args.bucket:
        publish(data, args.bucket)
    print(json.dumps({'model_id': data['model_id'], 'date': data['anchor_date'], 'supported_counties': data['supported_counties'], 'output': str(args.output), 'published': bool(args.bucket)}))


if __name__ == '__main__':
    main()

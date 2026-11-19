import base64
import json
from unittest.mock import Mock
import google_crc32c
import pytest
import requests
from google.auth.credentials import AnonymousCredentials
from google.cloud import storage
from raster_pipeline.storage import GCSStore


@pytest.mark.parametrize('key,expected_generation,mime,cache',[
    ('canopy/test.png',0,'image/png','public, max-age=31536000, immutable'),
    ('weather/latest.json',7,'application/json','no-store'),
])
def test_gcs_upload_uses_supported_checksum_and_browser_metadata(key,expected_generation,mime,cache):
    response=requests.Response();response.status_code=200;response._content=b'{"generation":"8"}'
    transport=Mock(spec=requests.Session);transport.is_mtls=False;transport.request.return_value=response
    client=storage.Client(project='test',credentials=AnonymousCredentials(),_http=transport)
    store=GCSStore('test-bucket',client=client,cache_control='public, max-age=31536000, immutable')
    payload=b'snapshot contents'
    if expected_generation==0:store.put(key,payload)
    else:assert store.replace_pointer(key,payload,'7')
    transport.request.assert_called_once();request=transport.request.call_args
    assert f'ifGenerationMatch={expected_generation}' in request.args[1]
    body=request.kwargs['data']
    checksum=base64.b64encode(google_crc32c.Checksum(payload).digest())
    assert b'"crc32c": "'+checksum+b'"' in body
    assert b'content-type: '+mime.encode()+b'\r\n' in body
    assert json.dumps(cache).encode() in body

import base64
import json
from unittest.mock import Mock

import google_crc32c
import pytest
import requests
from google.auth.credentials import AnonymousCredentials
from google.cloud import storage

from raster_pipeline.storage import GCSStore


@pytest.mark.parametrize('operation,expected_generation', [('put', 0), ('replace_pointer', 7)])
def test_gcs_upload_checksums_with_installed_client(operation, expected_generation):
    # Exercise the real client's checksum validation without network access.
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps({'generation': '8'}).encode()
    transport = Mock(spec=requests.Session)
    transport.is_mtls = False
    transport.request.return_value = response
    client = storage.Client(project='test', credentials=AnonymousCredentials(), _http=transport)
    store = GCSStore('test-bucket', client=client)
    payload = b'snapshot contents'

    if operation == 'put':
        store.put('outage/v1/test.json', payload)
    else:
        assert store.replace_pointer('outage/v1/test.json', payload, '7') is True

    transport.request.assert_called_once()
    request = transport.request.call_args
    assert f'ifGenerationMatch={expected_generation}' in request.args[1]
    checksum = base64.b64encode(google_crc32c.Checksum(payload).digest())
    assert b'"crc32c": "' + checksum + b'"' in request.kwargs['data']

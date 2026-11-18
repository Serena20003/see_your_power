from datetime import datetime,timezone
import json
from pathlib import Path
import threading
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
import httpx
import pytest
from raster_pipeline.catalog import list_candidates,collect_catalog
from raster_pipeline.contracts import SourceField,SourceSelection
from raster_pipeline.decode import inspect_grib
from raster_pipeline.download import download_inputs


def test_both_directory_adapters_and_layout_failure():
    def respond(request):
        path=request.url.path
        if '/r/' in path and path.endswith('/r/'):
            html='<a href="2026-11-16T00%3A00/">run</a>'
        elif '/r/' in path:html='<a href="PT001H00M.grib2">hour</a>'
        else:html='<a href="icon_global_icosahedral_single-level_2026111600_001_T_2M.grib2.bz2">hour</a>'
        return httpx.Response(200,text=html)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        a=list_candidates([{'adapter':'legacy','root':'https://example.test/grib/'}],client=client)
        b=list_candidates([{'adapter':'v1','root':'https://example.test/p/'}],client=client)
    assert len(a)==16 and len(b)==4
    assert any('/T_2M/r/2026-11-16T00%3A00/s/PT001H00M.grib2' in u for u in b)
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,text='layout changed'))) as client:
        with pytest.raises(ValueError,match='layout'):list_candidates([{'adapter':'legacy','root':'https://example.test/'}],client=client)


def test_stream_retry_metadata_cache_and_corruption(tmp_path):
    import eccodes as e
    path=tmp_path/'source.grib2';g=e.codes_grib_new_from_samples('regular_ll_sfc_grib2')
    e.codes_set(g,'paramId',167);e.codes_set(g,'dataDate',20261116);e.codes_set(g,'dataTime',0);e.codes_set(g,'forecastTime',1)
    with path.open('wb') as f:e.codes_write(g,f)
    e.codes_release(g);payload=path.read_bytes();calls=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            if len(calls)==1:self.send_error(503);return
            self.send_response(200);self.end_headers();self.wfile.write(payload)
        def log_message(self,*a):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        meta=inspect_grib(path);seen=datetime(2026,11,16,6,tzinfo=timezone.utc)
        f=SourceField(**meta,first_seen=seen,url=f'http://127.0.0.1:{server.server_port}/temperature')
        selection=SourceSelection(cutoff=seen,target_start=seen,selected_run=f.run,intervals=[],fields=[f])
        inventory=download_inputs(selection,tmp_path/'cache',retries=1)
        assert len(calls)==2 and inventory.downloaded_bytes==len(payload)
        assert download_inputs(selection,tmp_path/'cache').fields[0].sha256==inventory.fields[0].sha256
        assert len(calls)==2
        Path(inventory.fields[0].path).write_bytes(b'interrupted')
        assert download_inputs(selection,tmp_path/'cache',retries=0).fields[0].sha256==inventory.fields[0].sha256
        bad=f.model_copy(update={'level':10})
        with pytest.raises(ValueError,match='metadata mismatch'):
            download_inputs(selection.model_copy(update={'fields':[bad]}),tmp_path/'bad',retries=0)
        observed=collect_catalog([f.url],tmp_path/'observed',retries=0)[0]
        Path(observed.path).unlink()
        fetched=collect_catalog([f.url],tmp_path/'observed',retries=0)[0]
        assert fetched.sha256==observed.sha256
        assert fetched.first_seen==observed.first_seen
    finally:server.shutdown();server.server_close();thread.join()

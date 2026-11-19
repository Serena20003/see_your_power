from datetime import datetime,timezone,timedelta
from raster_pipeline.cloud_job import candidate_urls


def urls(run,leads):
    return [f'https://opendata.dwd.de/weather/nwp/v1/m/icon/p/{field}/r/{run}/s/PT{h:03}H00M.grib2'
        for field in ('TOT_PREC','T_2M','U_10M','V_10M') for h in leads]


def test_candidates_download_only_selected_run_and_needed_history():
    now=datetime(2026,11,18,3,tzinfo=timezone.utc)
    current=urls('2026-11-18T00%3A00',range(79));old=urls('2026-11-17T18%3A00',range(79))
    selected,run=candidate_urls(current+old+urls('2026-11-17T12%3A00',range(79)),now)
    assert run=='2026-11-18T00:00:00+00:00'
    assert set(current)<=set(selected)
    assert len(set(selected)&set(old))==12
    assert not any('T12%' in u for u in selected)


def test_candidates_do_not_fill_sparse_hours_or_select_future_runs():
    now=datetime(2026,11,18,7,tzinfo=timezone.utc)
    sparse=urls('2026-11-18T00%3A00',list(range(79))+list(range(81,103,3)))
    assert candidate_urls(sparse+urls('2026-11-18T12%3A00',range(79)),now)==([],None)


def test_missing_hour_blocks_candidate_run():
    now=datetime(2026,11,18,3,tzinfo=timezone.utc)
    candidates=urls('2026-11-18T00%3A00',range(79))
    candidates=[u for u in candidates if '/U_10M/' not in u or 'PT012' not in u]
    assert candidate_urls(candidates,now)==([],None)

from pathlib import Path
from pydantic import Field
from .contracts import Contract, GridSpec, SourceField


class PipelineConfig(Contract):
    grid: GridSpec = Field(default_factory=GridSpec)
    lookback_hours: int = Field(default=5, ge=0, le=24)
    concurrency: int = Field(default=4, ge=1, le=16)
    retries: int = Field(default=3, ge=0, le=10)
    timeout_seconds: float = Field(default=90, gt=0)
    catalog: list[SourceField] = Field(default_factory=list)
    source_catalog: str | None = None
    source_routes: list[str] = Field(default_factory=lambda:[
        'https://opendata.dwd.de/weather/nwp/icon/grib/{cycle}/{field}/'])
    static_manifest: str | None = None
    risk_config: str | None = None
    native_grid: str | None = None
    native_grid_sha256: str | None = None


def load_config(path):
    import json
    path=Path(path).resolve()
    data=json.loads(path.read_text())
    for key in ('source_catalog','risk_config','native_grid'):
        if data.get(key) and not Path(data[key]).is_absolute():
            data[key]=str((path.parent/data[key]).resolve())
    if data.get('source_catalog'):
        data['catalog']=json.loads(Path(data['source_catalog']).read_text())
    return PipelineConfig.model_validate(data)

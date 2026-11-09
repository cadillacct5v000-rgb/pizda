import os
from pathlib import Path
import pytest

# Never load or mutate the uploaded real config, even during test collection.
os.environ['KADROGEN_DATA_DIR'] = str(Path(__file__).resolve().parent / '.data')

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    import app
    import pipeline
    import fastgen_api
    monkeypatch.setattr(fastgen_api, '_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(app, 'CONFIG_PATH', str(tmp_path / 'config.json'))
    monkeypatch.setattr(pipeline, 'LOG_PATH', str(tmp_path / 'videogen.log'))
    monkeypatch.setattr(pipeline, 'LAST_JOB_DIR', None)
    pipeline.CANCEL.clear()
    pipeline.clear_wait()
    pipeline.take_redo()
    app.STATE.update(running=False, preparing=False, queue_active=False, cancelled=False, error=None,
                     result=None, job_dir=None, log=[])
    yield
    pipeline.CANCEL.clear()

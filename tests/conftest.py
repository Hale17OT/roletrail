import pytest
from discovery import dossier
import dashboard
import shutil
from pathlib import Path

@pytest.fixture(autouse=True)
def isolated_cv_files(tmp_path,monkeypatch):
    here=tmp_path/'profile'/'discovery';here.mkdir(parents=True)
    cv=here.parent/'cvs';cv.mkdir()
    for name,_ in dossier.CVS.values():
        (cv/name).write_bytes(b'%PDF-1.4\n% synthetic test fixture only\n%%EOF')
    monkeypatch.setattr(dossier,'HERE',here)
    original=Path(__file__).resolve().parents[1]
    shutil.copyfile(original/'discovery/candidate.example.json',here/'candidate.example.json')
    library=here.parent/'frontend/src/data';library.mkdir(parents=True)
    shutil.copyfile(original/'frontend/src/data/cvs.example.json',library/'cvs.example.json')
    monkeypatch.setattr(dashboard,'ROOT',here.parent)

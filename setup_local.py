"""Initialize ignored local configuration without replacing existing files."""
import shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parent
for example,target in [
    ('discovery/candidate.example.json','discovery/candidate.json'),
    ('discovery/review_sources.example.json','discovery/review_sources.json'),
    ('frontend/src/data/cvs.example.json','frontend/src/data/cvs.json')]:
    destination=ROOT/target
    if not destination.exists():
        shutil.copyfile(ROOT/example,destination)
        print('Created local configuration:',target)
(ROOT/'cvs').mkdir(exist_ok=True)
print('Edit your profile and CV library before running discovery. Existing data is preserved.')

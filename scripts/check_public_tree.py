"""Reject private artifacts and common secret formats in Git-tracked files."""
from pathlib import Path
import re,subprocess,sys

root=Path(__file__).resolve().parents[1]
result=subprocess.run(['git','ls-files','-z'],cwd=root,capture_output=True,check=True)
paths=[p for p in result.stdout.decode().split('\0') if p]
if not paths:
    raise SystemExit('No tracked files to check; stage the public source first.')
private_parts={'.discovery','cvs','output','tmp','node_modules','__pycache__','.venv','dist'}
private_names={'jobs.json','.email_config.json','.env','candidate.json','review_sources.json'}
patterns=[re.compile(r'gh[pousr]_[A-Za-z0-9]{30,}'),re.compile(r'github_pat_[A-Za-z0-9_]{30,}'),
          re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
          re.compile(r'AKIA[A-Z0-9]{16}'),re.compile(r'(?i)(?:api_hash|password|app_password)\s*[:=]\s*[\"\x27][A-Za-z0-9+/=_-]{16,}[\"\x27]')]
errors=[]
for name in paths:
    path=Path(name)
    if private_parts.intersection(path.parts) or path.name in private_names or path.suffix.lower() in {'.pdf','.docx','.sqlite3','.db','.auth','.session','.log'}:
        errors.append(name+': private/generated artifact')
    data=(root/path).read_bytes()
    try:text=data.decode('utf8')
    except UnicodeDecodeError:
        errors.append(name+': unexpected binary');continue
    if any(pattern.search(text) for pattern in patterns):errors.append(name+': possible secret')
if errors:
    print('\n'.join(errors));sys.exit(1)
print(f'Public-tree check passed: {len(paths)} tracked text files; no private artifacts or matched secret patterns.')

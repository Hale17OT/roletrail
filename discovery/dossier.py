"""Complete dossiers from verified facts and a single substantiated candidate profile."""
import json
import re
from pathlib import Path

from .policy import DOMAIN, identity, parse_date, signature, now_utc

HERE = Path(__file__).resolve().parent
PROFILE_FILE = HERE / "candidate.json"
PROFILE = json.loads((PROFILE_FILE if PROFILE_FILE.exists() else HERE / "candidate.example.json").read_text(encoding="utf-8"))
REQUIRED = "id title company region locationType payoutMethod salary postedDate postedAgo status matchScore tags keyPitch description fitRationale requirements matchedCv applyUrl instructions tailoredLetter".split()
CVS = {
    'software':('software-engineer.pdf','Software Engineer | Full-Stack & Backend'),
    "power_platform": ("power-platform.pdf", "Power Platform & Business Applications Specialist"),
    "hybrid": ("hybrid-developer.pdf", "Software & Power Platform Developer | Integrations")
}


def build_dossier(lead, audit, status="ready"):
    if audit["state"] != "verified" or status not in {"ready", "saved"}:
        raise ValueError("Only verified leads can become dossiers")
    title, company, desc = lead["title"], lead["company"], lead["description"]
    preferred = bool(DOMAIN.search(title+' '+desc))
    focus_description = 'Microsoft business applications and automation' if preferred else 'software development, integrations and automation'
    pro = bool(re.search(r"C#|\.net|typescript|\bpcf\b|plugins?|web api|azure devops|\balm\b", desc, re.I))
    filename, role = CVS['software' if not preferred else "hybrid" if pro else "power_platform"]
    checks = audit["checks"]
    location = lead.get("location") or "Worldwide"
    if checks["localAddis"]:
        location_type = "Hybrid (Addis Ababa)" if "hybrid" in lead.get("workplace", "").lower() else "On-site (Addis Ababa)" if not checks["remote"] else "100% Remote / Telecommute"
        region = f"{location} (Local; UTC+3)"
        arrangement = "I am based in Addis Ababa (UTC+3) and available for the advertised local arrangement."
    elif checks["ethiopiaEligibleRemote"] and not checks.get('visaSponsorshipInferred'):
        location_type = "100% Remote / Telecommute"
        region = f"{location} (Remote; based in Ethiopia, UTC+3)"
        arrangement = "I am based in Addis Ababa, Ethiopia (UTC+3), with working-hour overlap with UK, European and African teams; I can discuss the role's required schedule."
    else:
        mode = "Remote within destination country" if checks["remote"] else "Hybrid" if "hybrid" in lead.get("workplace", "").lower() else "On-site"
        sponsorship='Visa Sponsorship Inferred' if checks.get('visaSponsorshipInferred') else 'Visa Sponsored'
        location_type = f"{mode} ({location}, {sponsorship})"
        region = f"{location} ({sponsorship}; relocation from Ethiopia)"
        arrangement = (f"I am currently based in Addis Ababa, Ethiopia (UTC+3), and am open to relocating to {location}. Please confirm whether you can sponsor my initial work visa; sponsorship is inferred from your international hiring/relocation information, not explicitly guaranteed." if checks.get('visaSponsorshipInferred') else f"I am currently based in Addis Ababa, Ethiopia (UTC+3), and am open to relocating to {location} under the work visa sponsorship stated in your posting.")
    pitch = f"{PROFILE['name']}: {PROFILE['certification']} and {PROFILE['degree']}. My documented project experience aligns with {company}'s {title} role."
    tag_map = {"power apps": "Power Apps", "power automate": "Power Automate", "dataverse": "Dataverse", "dynamics": "Dynamics 365", "typescript": "TypeScript", "c#": "C# / .NET", "copilot studio": "Copilot Studio", 'python':'Python','javascript':'JavaScript','.net':'.NET','node.js':'Node.js','react':'React','vue':'Vue','postgresql':'PostgreSQL','docker':'Docker','rest api':'REST APIs','automated testing':'Automated Testing','fastapi':'FastAPI','machine learning':'Machine Learning','ruby':'Ruby','rails':'Ruby on Rails','graphql':'GraphQL','ci/cd':'CI/CD','kubernetes':'Kubernetes','terraform':'Terraform','rust':'Rust','swift':'Swift','kotlin':'Kotlin','sql':'SQL','azure':'Azure','aws':'AWS','c++':'C++'}
    tags = [v for k, v in tag_map.items() if k in (title + " " + desc).lower()]
    tags = list(dict.fromkeys(tags + (["Power Platform", "PL-200", "Automation", "REST APIs"] if preferred else ['Software Engineering','Software Development'])))[:6]
    requirements = PROFILE["projects"]
    focus = next((s.strip() for s in desc.splitlines() if 40 <= len(s.strip()) <= 250), title)
    letter = f"""Dear Hiring Team at {company},

I am applying for the {title} position. I hold a {PROFILE['degree']} and am {PROFILE['certification']}.

Your posting describes the following focus: {focus} My documented evidence is: {'; '.join(p['item'] + ': ' + p['evidence'] for p in PROFILE['projects'])}. Please assess my actual contributions against your requirements.

{arrangement}

I would welcome the opportunity to discuss how my software engineering foundations, Power Platform certification and automation experience could contribute to {company}. Thank you for considering my application.

Sincerely,
{PROFILE['name']}
{PROFILE['email']}
{PROFILE['phone']}
{PROFILE['github']}"""
    slug = re.sub(r"[^a-z0-9]+", "-", company.lower() + "-" + title.lower()).strip("-")[:110]
    score = min(98, 75 + sum(3 for k in tag_map if k in desc.lower()) + (8 if preferred else 0) + (5 if re.search(r"pl-200|functional consultant", desc, re.I) else 0))
    score=max(0,score-(7 if checks.get('experienceBand')=='stretch' else 0)-(6 if checks.get('publicationBasis')=='confirmed_open_older_posting' else 0))
    result = {
        "id": f"job-{slug}-{identity(lead)[:8]}", "title": title, "company": company,
        "region": region, "locationType": location_type, "payoutMethod": "Not specified in posting",
        "salary": lead.get("salary") or "Not quoted in posting",
        "postedDate": parse_date(lead.get('published_at'))[0].strftime('%b %d, %Y') if parse_date(lead.get('published_at')) else lead.get('published_at') or 'Date not disclosed',
        "postedAgo": f"Published {lead['published_at']} ({lead['source']}; live official source audited {audit['auditedAt']})" if lead.get('published_at') else f"Publication date not disclosed; open application verified {audit['auditedAt']} ({lead['source']})",
        "status": status, "matchScore": score, "tags": tags, "keyPitch": pitch, "description": desc,
        "fitRationale": f"- {PROFILE['certification']}.\n- {PROFILE['degree']}.\n- {arrangement}",
        "requirements": requirements,
        "matchedCv": {"fileName": filename, "roleTitle": role, "shortLabel": 'Software Engineering' if not preferred else "Low-Code & Pro-Code" if pro else "Power Platform",
                      "reason": f"Relevant to the {focus_description} responsibilities of {title}.",
                      "url": "/cvs/" + filename, "diskPath": str(HERE.parent / "cvs" / filename)},
        "applyUrl": lead["url"],
        "instructions": f"1. Open the official application: {lead['url']}\n2. Review the live eligibility, responsibilities and working arrangement.\n3. Upload {filename} and use the tailored cover letter.\n4. Complete the employer's application questions and submit.\n5. Mark Applied after submission.",
        "tailoredLetter": letter,
        "sourceEvidence": {**audit, "lead": lead, "rankingMethod": "75 base + 3 per technical keyword group + 8 Power Platform/Dynamics preference + 5 functional credential relevance; maximum 98, after eligibility gates"}
    }
    if lead.get("attribution_url"):
        result["sourceAttribution"] = {"name": lead["source"], "url": lead["attribution_url"]}
        result["description"] += f"\n\nDiscovered via {lead['source']}: {lead['attribution_url']}"
    return result


def validate_dossier(job):
    errors = [f"missing:{key}" for key in REQUIRED if job.get(key) is None or job.get(key) == "" or job.get(key) == [] or job.get(key) == {}]
    if not isinstance(job.get("tags"), list) or not 4 <= len(job["tags"]) <= 6:
        errors.append("tags_must_have_four_to_six_items")
    reqs = job.get("requirements")
    if not isinstance(reqs, list) or not 3 <= len(reqs) <= 5 or any(not isinstance(r, dict) or not r.get("item") or not r.get("evidence") for r in reqs):
        errors.append("invalid_requirements")
    if not isinstance(job.get("matchScore"), (int, float)) or not 0 <= job["matchScore"] <= 100:
        errors.append("invalid_match_score")
    cv = job.get("matchedCv")
    if not isinstance(cv, dict) or any(not cv.get(k) for k in ("fileName", "roleTitle", "shortLabel", "reason", "url", "diskPath")):
        errors.append("invalid_cv")
    elif not Path(cv["diskPath"]).is_file():
        errors.append("cv_file_missing")
    if PROFILE.get("certification") and PROFILE["certification"] not in job.get("keyPitch", ""):
        errors.append("credential_missing")
    return errors

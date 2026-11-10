"""Shared eligibility rules; unknown publication age and experience exclude."""
import hashlib
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

UTC = timezone.utc
SENIOR = re.compile(r"\b(senior|sr\.?|lead|principal|staff|architect|director|head of|vp|vice president|manager|chief)\b", re.I)
DOMAIN = re.compile(r"\b(power\s*apps|power\s*BI|power\s*platform|power\s*automate|dataverse|dynamics\s*(365|crm)|d365|pl-200|power\s*pages|copilot studio)\b", re.I)
SOFTWARE = re.compile(r'\b(?:software|back[- ]?end|front[- ]?end|full[- ]?stack|web|mobile|application|platform|cloud|data|devops|AI|machine learning|quantitative|python|javascript|typescript|java|\.net)\s+(?:engineer(?:ing|s)?|develop(?:ers?|ment))\b|\b(?:develop|build|implement|design)(?:ing)?\s+(?:\w+\s+){0,3}(?:software|web applications|APIs)\b',re.I)
SOFTWARE = re.compile(SOFTWARE.pattern+r'|\b(?:qa|test|software test)\s+(?:automation\s+)?engineer\b|(?<!\w)(?:C\#|\.NET|C\+\+|SQL|ML)\s+(?:developer|engineer)\b', re.I)
SOFTWARE = re.compile(SOFTWARE.pattern+r'|\b(?:Laravel|FastAPI|Django|React|Vue|Flutter|Kotlin|Spring(?: Boot)?)\s+(?:developer|engineer)\b',re.I)
RELEVANT = re.compile(DOMAIN.pattern+'|'+SOFTWARE.pattern,re.I)
POLICY_VERSION = '2026-11-10-seven-day-flexible'
MAX_AGE_HOURS = 168
PL200_TRIAL = 'pl200-cross-border-12-day-trial'
PL200_PREFERRED_TRIAL = 'pl200-required-or-preferred-cross-border-12-day-trial'
SKILLS_TRIAL = 'skills-first-30-day-or-confirmed-open-trial'
ALL_TRACKS_TRIAL = 'skills-first-all-tracks-active-30-day-trial'
RECURRING_PROFILE = 'responsibility-first-active-30-day'
PL200_TRIALS = {PL200_TRIAL,PL200_PREFERRED_TRIAL}
PL200_CERT = re.compile(r'\bPL[- ‑–]?200\b|Microsoft Certified\s*:\s*Power Platform Functional Consultant Associate',re.I)


def pl200_requirement(text):
    """Retain exact certification wording; preferred credentials are not mandatory."""
    paragraphs=[p.strip() for p in text.splitlines() if p.strip()]
    found=[];section=None
    for i,p in enumerate(paragraphs):
        heading=p.strip('#:•*- ').lower()
        if len(heading)<100 and not PL200_CERT.search(p):
            if re.fullmatch(r'(?:preferred|desirable|desired|nice[- ]to[- ]have|bonus)(?:\s+(?:microsoft|technical))?(?:\s+(?:skills|qualifications|certifications|experience))?',heading):section='preferred'
            elif re.fullmatch(r'(?:required|essential|minimum|mandatory)\b[^.]{0,75}\b(?:skills|qualifications|certifications|experience|criteria)|requirements|required|must[- ]haves?|certifications\s*/\s*required',heading):section='required'
            elif re.fullmatch(r'benefits|responsibilities|about (?:us|the (?:role|company))|what (?:we offer|you.ll do)|nice to have skills',heading):section=None
        if not PL200_CERT.search(p):continue
        optional_words=r'preferred|desirable|nice[- ]to[- ]have|bonus|advantage|optional|willing.*obtain|pursuing|\ba plus\b|\basset\b|beneficial|valued|valuable'
        explicit_required=bool(re.search(r'required|mandatory|must (?:have|hold)|essential',p,re.I))
        optional=bool(re.search(optional_words,p,re.I) or (not explicit_required and section=='preferred'))
        required=not optional and (explicit_required or section=='required')
        found.append({'classification':'required' if required else 'preferred' if optional else 'mentioned','excerpt':p})
    return found
EARLY_LEVEL = re.compile(r'\b(?:junior|jr\.?|mid(?:dle)?(?:[- /]level)?|intermediate|entry[- ]level|graduates?|intern|associate)\b', re.I)
RESTRICTIONS = re.compile(r"\b(w[- ]?2 only|us citizens? only|u\.?s\.? citizens? only|citizenship required|must (?:be a|hold).*citizen|(?:active|must have|required).*security clearance|security clearance (?:is )?required|top secret|polygraph|uk sc/dv|must (?:reside|live|be based|be located) in (?:the )?(?:us|usa|united states|uk|united kingdom|canada)|(?:us|uk|canada) residents? only|without (?:visa )?sponsorship|already (?:have|hold).*(?:work permit|right to work))\b", re.I)
SPONSOR = re.compile(r"\b(?:(?:we (?:offer|provide)|offers?|provides?|available|full|open to(?: the)?)\s+(?:work\s+)?visa sponsorship|(?:work\s+)?visa sponsorship\s+(?:is\s+)?(?:available|provided|offered)|sponsor(?:ing)?\s+(?:your |a )?(?:work )?visa)\b", re.I)
NO_SPONSOR = re.compile(r"\b(no (?:visa )?sponsorship|(?:cannot|can't|do not|unable to|will not) sponsor|sponsorship (?:is )?(?:not available|unavailable|not provided))\b", re.I)
GLOBAL = re.compile(r"\b(worldwide|world[- ]wide|work from anywhere|anywhere in the world|any country|ethiopia|all countries)\b", re.I)
REMOTE_ELIGIBILITY = re.compile(r"\b(?:remote\s*[-:,(]?\s*(?:worldwide|anywhere|globally)|worldwide\s+remote|work(?:ing)?\s+from\s+anywhere|(?:open to|accept(?:ing)?|welcome|hir(?:e|ing))\s+(?:international\s+)?(?:candidates?|applicants?|applications?)\s+(?:from\s+)?(?:worldwide|anywhere|ethiopia|any country)|remote\s+(?:work\s+)?(?:from|in)\s+ethiopia)\b", re.I)
EXPERIENCE = re.compile(r"(?<![\w.])(\d{1,2}(?:\.\d+)?)(?:\s*[-–—to]+\s*(\d{1,2}(?:\.\d+)?))?\s*\+?\s*(?:years?|yrs?)(?:\s+of)?\s+(?:\w+[ /-]*){0,4}(?:experience|exp)\b", re.I)


def now_utc():
    return datetime.now(UTC)


def clean(value):
    if value is None or str(value).strip().lower() in {"nan", "nat", "none"}:
        return ""
    return unicodedata.normalize("NFKC", str(value)).strip()


def signature(company, title):
    norm = lambda s: re.sub(r"\W+", " ", clean(s).casefold()).strip()
    return norm(company) + "::" + norm(title)


def canonical_url(url):
    p = urlsplit(clean(url))
    if p.scheme not in {"https", "http"} or not p.hostname or p.username or p.password:
        return ""
    params = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
              if not k.lower().startswith("utm_") and k.lower() not in {"source", "ref", "referrer", "trackingid", "gh_src", "jbsrc", "~"}]
    path = p.path.rstrip('/')
    hostname = p.hostname.lower()
    if hostname == 'jobs.ashbyhq.com' and re.search(r'/[a-f0-9-]{36}/application$',path,re.I):
        path = path[:-len('/application')]
    if hostname in {'jobs.lever.co','jobs.eu.lever.co'} and re.search(r'/[a-f0-9-]{36}/apply$',path,re.I):
        path = path[:-len('/apply')]
    if hostname.endswith('.hrmdirect.com') and any(k.lower()=='req' for k,v in params):
        params = [(k,v) for k,v in params if k.lower()=='req']
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), path, urlencode(sorted(params)), ""))


def identity(lead):
    raw = canonical_url(lead.get("url", "")) or signature(lead.get("company"), lead.get("title"))
    if lead.get("source", "").startswith("telegram:") and not lead.get("official_verified"):
        raw += ":" + lead.get("source_id", "")
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def parse_date(value):
    """Return (earliest, latest) possible publication time; never fabricate dates."""
    value = clean(value)
    if not value:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            start = datetime.fromisoformat(value).replace(tzinfo=UTC)
            return start, start + timedelta(days=1) - timedelta(microseconds=1)
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return None
        return dt.astimezone(UTC), dt.astimezone(UTC)
    except (TypeError, ValueError):
        return None


def publication_bounds(value, now=None):
    """Publication-day age, not crawl/repost time. Relative days use their maximum age."""
    bounds = parse_date(value)
    if bounds:
        return bounds
    now = now or now_utc()
    match = re.fullmatch(r'(?:posted\s+)?(\d+)\s*(hours?|hrs?|days?)\s+ago', clean(value), re.I)
    if match:
        unit = timedelta(days=1) if match[2].lower().startswith('day') else timedelta(hours=1)
        age = int(match[1])
        return now-(age+1)*unit, now-age*unit
    return None


def evaluate(lead, now=None, hours=MAX_AGE_HOURS):
    now = now or now_utc()
    title, text = clean(lead.get("title")), clean(lead.get("description"))
    evidence = {"auditedAt": now.isoformat(), "sourceUrl": lead.get("url"), "checks": {}, 'policyVersion':POLICY_VERSION}
    reject, pending = [], []
    recurring=lead.get('search_profile')==RECURRING_PROFILE
    all_tracks_trial=lead.get('search_profile') in {ALL_TRACKS_TRIAL,RECURRING_PROFILE}
    skills_trial=lead.get('search_profile') in {SKILLS_TRIAL,ALL_TRACKS_TRIAL,RECURRING_PROFILE}
    trial=lead.get('search_profile') in PL200_TRIALS
    if skills_trial:evidence['policyVersion']=lead['search_profile']
    if trial:evidence['policyVersion']=lead['search_profile']
    window=720 if skills_trial else 288 if trial else min(MAX_AGE_HOURS,hours)
    certification=pl200_requirement(text) if trial else []
    allowed_certifications={'required','preferred'} if lead.get('search_profile')==PL200_PREFERRED_TRIAL else {'required'}
    if trial and not any(p['classification'] in allowed_certifications for p in certification):
        reject.append('pl200_not_required_or_preferred' if lead.get('search_profile')==PL200_PREFERRED_TRIAL else 'pl200_not_explicitly_required')
    if not title or not clean(lead.get("company")) or not text:
        pending.append("missing_employer_title_or_description")
    if SENIOR.search(title+' '+clean(lead.get('seniority'))):
        reject.append("seniority")
    if re.search(r'\bas (?:the )?(?:first )?(?:senior|principal|staff|lead)\b[^.\n]{0,60}\b(?:hire|engineer|developer)\b',text,re.I):
        reject.append('seniority')
    # Candidate's canonical education is a BSc; do not manufacture a doctorate.
    for paragraph in text.splitlines():
        if re.search(r'\b(?:you have completed or are finishing|must (?:have|hold)|required(?: qualification)?\s*:)\s+(?:a\s+)?(?:Ph\.?D\.?|doctorate)\b',paragraph,re.I) and not re.search(r'\b(?:preferred|optional|bachelor|equivalent experience)\b',paragraph,re.I):
            reject.append('required_doctorate_not_met')
            break
    preferred = bool(DOMAIN.search(title + ' ' + text))
    software = bool(SOFTWARE.search(title + ' ' + text))
    adjacent=bool(re.search(r'\b(?:sharepoint|microsoft 365|M365|business applications|CRM|workflow automation|copilot|AB-410)\b',title+' '+text,re.I))
    if not (preferred or software or (skills_trial and adjacent)):
        reject.append('no_relevant_technical_alignment')
    if skills_trial and not all_tracks_trial and not (preferred or adjacent):reject.append('no_business_application_alignment')
    experience_text = re.sub(r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten)\s*\((\d+)\)", r"\1", text, flags=re.I)
    # The candidate holds a relevant BSc. A posting's explicitly alternative
    # ten-year visa-documentation route is not ten required professional years.
    degree_path=bool(re.search(r'\bbachelor[^.\n]{0,100}\b(?:computer science|software engineering)\b',text,re.I))
    visa_alternatives=[]
    if degree_path:
        lines=[]
        for paragraph in experience_text.splitlines():
            if re.search(r'\b(?:alternatively|if you apply based on)\b',paragraph,re.I) and re.search(r'\b(?:for visa purposes|instead of[^.\n]{0,40}bachelor)',paragraph,re.I):
                visa_alternatives.append(paragraph)
            else:lines.append(paragraph)
        experience_text='\n'.join(lines)
    for word, number in {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}.items():
        experience_text = re.sub(r"\b" + word + r"(?=\s+(?:years?|yrs?))", str(number), experience_text, flags=re.I)
    spans = list(EXPERIENCE.finditer(experience_text))
    minimums = list(re.finditer(r"(?:minimum|at least|requires?|required|experience\s*:)\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)", experience_text, re.I))
    labelled_level = bool(EARLY_LEVEL.search(title+' '+clean(lead.get('seniority'))) or re.search(r'(?:role|position|level|seniority)\s*[:–-]\s*(?:junior|mid[- ]?level|middle|intermediate|entry[- ]level|graduate|associate)\b|(?:seeking|hiring|looking for)\s+(?:an?\s+)?(?:strong\s+)?(?:junior|mid[- ]?level|intermediate)\b',text,re.I))
    role_tenure=list(re.finditer(r'^\s*(?:[-*•]\s*)?(?:(?:at least|minimum(?: of)?)\s+)?(\d{1,2})\s*\+?\s*(?:years?|yrs?)\s+(?:in|as)\s+\w',experience_text,re.I|re.M))
    years = [max(float(m[1]),float(m[2] or m[1])) for m in spans] + [int(m[1]) for m in minimums+role_tenure]
    metadata_years=list(re.finditer(r'\b(?:experience\s*(?:level|required)?|seniority)\s*[:\n]\s*(?:(?:junior|mid[- ]level|intermediate|associate)\s*)?(\d{1,2})(?:\s*[-–—]\s*(\d{1,2}))?\s*\+?\s*years?\b',experience_text,re.I))
    years += [max(int(m[1]),int(m[2] or m[1])) for m in metadata_years]
    if years and max(years) > (5 if skills_trial or labelled_level else 3):
        reject.append('experience_above_five_years' if labelled_level or skills_trial else 'experience_above_three_years')
    elif not skills_trial and not years and not labelled_level and not re.search(r'\bno (?:(?:prior|professional|previous) )?experience\b',text,re.I):
        reject.append('experience_not_verified')
    fit_review=lead.get('responsibility_review') or {}
    if skills_trial and not (fit_review.get('approved') is True and fit_review.get('basis') and fit_review.get('gaps') is not None):
        pending.append('responsibility_review_required')
    restricted_text = re.sub(r"without (?:visa )?sponsorship", "", text, flags=re.I)
    if RESTRICTIONS.search(restricted_text) or re.search(r"(?:authorized|right|eligible|permission) to work[^.\n]{0,80}without (?:visa )?sponsorship|\b(?:us|u\.s\.|uk|british|canadian) citizenship (?:is )?(?:required|mandatory)\b", text, re.I):
        reject.append("residency_citizenship_or_clearance_restriction")
    if re.search(r'authori[sz]ed to work[^.\n]{0,100}country', text, re.I) and re.search(r'(?:does not|do not|will not|cannot) (?:offer |provide )?(?:employment |visa )?sponsor', text, re.I):
        reject.append('residency_citizenship_or_clearance_restriction')
    if re.search(r"\b(?:remote|applicants?|candidates?|residents?)\s*(?:[-:(]|from|in)?\s*(?:the )?(?:us|usa|united states|uk|united kingdom|canada|europe|eu|south africa)\s+only\b|\b(?:must|required to)\s+(?:already\s+)?have (?:the )?right to work\b", text, re.I):
        reject.append("domestic_only_eligibility")
    if re.search(r'\bremote\s+anywhere\s+in\s+(?:the\s+)?(?:us|usa|united states|uk|united kingdom|canada)\b',text,re.I):
        reject.append('domestic_only_eligibility')
    # A country label alone is not a restriction. These clauses explicitly
    # specify where the applicant/remote position must be based.
    restricted_countries = r'(?:the\s+)?(?:US|USA|United States|UK|United Kingdom|Canada|India|Poland|Germany|France|Spain|Portugal|Romania|Ukraine|Philippines|Pakistan|Brazil|Colombia|Mexico|Japan|Australia|Europe|EU)\b'
    based_clause = r'\b(?:remote\s*,?\s*based in|(?:position|role|job)\b[^.\n]{0,50}\bbased in|(?:must|required to|need to)\s+(?:live|reside|be based|be located)\s+in)\s+'
    if re.search(based_clause + restricted_countries, text, re.I):
        reject.append('domestic_only_eligibility')
    # A relative age is observed once. Reopening a saved lead tomorrow must not
    # move its publication window forward and make it perpetually fresh.
    observed = parse_date(lead.get('publication_observed_at'))
    bounds = publication_bounds(lead.get('published_at'), observed[0] if observed else now)
    open_check=lead.get('open_check') or {}
    open_stamp=parse_date(open_check.get('checked_at'))
    confirmed_open=bool(skills_trial and lead.get('official_verified') and open_check.get('active') is True and
        open_check.get('identity_matched') is True and canonical_url(open_check.get('source_url',''))==canonical_url(lead.get('url','')) and
        canonical_url(open_check.get('application_url','')) and open_check.get('evidence') and open_stamp and
        0<=(now-open_stamp[0]).total_seconds()<=86400)
    if all_tracks_trial and not confirmed_open:
        pending.append('open_status_not_verified')
    if not bounds:
        if skills_trial and not confirmed_open:pending.append('open_status_not_verified')
        elif not skills_trial:reject.append("publication_date_unknown")
    elif bounds[0] < now - timedelta(hours=window) and not (recurring and confirmed_open) and (all_tracks_trial or not confirmed_open):
        reject.append("outside_freshness_window")
    elif bounds[0] > now:
        reject.append("future_publication_date")
    if lead.get("valid_through"):
        deadline = parse_date(lead["valid_through"])
        if deadline and deadline[1] < now:
            reject.append("expired")
    if lead.get("closed"):
        reject.append("closed")

    location = clean(lead.get("location"))
    workplace = clean(lead.get("workplace"))
    local = bool(re.search(r"\baddis ababa\b", location, re.I))
    country_scoped_remote=bool(re.search(r'\bremote(?:ly)?(?:\s+work(?:ing)?)?\s*(?:\(\s*)?(?:only\s+in|within|exclusively\s+in)\s+'+restricted_countries,title+'\n'+text,re.I))
    remote = bool(re.search(r"telecommute|remote", workplace+' '+location, re.I) or
                  re.search(r'^\s*remote\s*$|#li-remote\b|^\s*(?:location|workplace|work(?:ing)?(?: arrangement| model)?)\s*:\s*(?:(?:fully|100%)\s+)?remote\b|\b(?:all of our roles are|this (?:role|position) is|fully|100%)\s+remote\b',text,re.I|re.M) or country_scoped_remote)
    explicit_sponsored = bool(SPONSOR.search(text) or re.search(r'\bsponsors?\s+(?:work\s+)?visas?\s*\+\s*relocation\b',text,re.I)) and not NO_SPONSOR.search(text)
    inference=lead.get('sponsorship_inference') or {}
    inference_stamp=parse_date(inference.get('checked_at'))
    phrase=clean(inference.get('evidence'))
    inferred_sponsored=bool(not explicit_sponsored and lead.get('official_verified') and inference.get('approved') is True and inference.get('basis') and phrase and
        phrase.casefold() in text.casefold() and re.search(r'overseas|international|relocat|visa',phrase,re.I) and
        canonical_url(inference.get('source_url',''))==canonical_url(lead.get('url','')) and
        inference_stamp and 0<=(now-inference_stamp[0]).total_seconds()<=86400 and not NO_SPONSOR.search(text))
    sponsored = explicit_sponsored or inferred_sponsored
    if country_scoped_remote and not local and not sponsored:
        reject.append('mandatory_foreign_presence')
    if remote and not local and not sponsored:
        for paragraph in text.splitlines():
            if re.search(r'\b(?:availability|willingness|able|required|expected)\s+to\s+travel\s+to\b',paragraph,re.I) and not re.search(r'\boptional|not required|voluntary\b',paragraph,re.I):
                reject.append('mandatory_foreign_presence')
                break
    if remote and re.search(r"\b(?:must|mandatory|required|requires?)\b[^.\n]{0,70}\b(?:on[- ]?site|office|hybrid|relocat)\w*", text, re.I) and not local and not sponsored:
        reject.append("mandatory_foreign_presence")
    if remote and re.search(r'\b(?:in[- ]person|on[- ]site)\s+(?:interview|assessment|meeting)s?[^.\n]{0,40}\b(?:required|mandatory)\b',text,re.I) and not local and not sponsored:
        reject.append('mandatory_foreign_presence')
    if remote and not local and not sponsored and re.search(
        r'\bremote[- ]first\s*,?\s*but\s+not\s+remote[- ]only\b|'
        r'\b(?:expect(?:ed)?|required|mandatory|must)\b[^.\n]{0,140}\b(?:in[- ]person|on[- ]site)\s+(?:working\s+)?(?:sessions?|gatherings?|meetings?|events?)\b',
        text, re.I):
        reject.append('mandatory_foreign_presence')
    applicant_locations = clean(lead.get("applicant_locations"))
    broad_region = re.compile(r'\b(?:EMEA|pan[- ]?Africa|African continent|worldwide|global|international|anywhere|Ethiopia)\b|^Africa$',re.I)
    eligible_remote = remote and not country_scoped_remote
    explicit_applicant_restriction = bool(applicant_locations and not broad_region.search(applicant_locations))
    if remote and explicit_applicant_restriction:
        reject.append('domestic_only_eligibility')
        eligible_remote = False
    if not (local or eligible_remote or sponsored):
        pending.append("location_or_sponsorship_not_verified")
    if sponsored and not location:
        pending.append("sponsorship_destination_unknown")
    cross_border=bool(remote and (GLOBAL.search(re.sub(r'\bEthiopia\b','',applicant_locations+' '+location,flags=re.I)) or
        REMOTE_ELIGIBILITY.search(re.sub(r'\bEthiopia\b','',text,flags=re.I)) or
        re.search(r'\bremote\b[^.\n]{0,35}\b(?:EMEA|Africa)\b|\b(?:EMEA|Africa)\b[^.\n]{0,35}\bremote\b',location+'\n'+text,re.I) or
        re.fullmatch(r'EMEA|Africa',applicant_locations,re.I)))
    explicit_geography_failure=bool(set(reject)&{'domestic_only_eligibility','residency_citizenship_or_clearance_restriction','mandatory_foreign_presence'})
    inferred_cross_border=bool((lead.get('search_profile')==PL200_PREFERRED_TRIAL or skills_trial) and eligible_remote and not country_scoped_remote and not explicit_applicant_restriction and not explicit_geography_failure)
    if trial and not (cross_border or inferred_cross_border):
        reject.append('cross_border_remote_not_established')
    if not lead.get("official_verified"):
        pending.append("official_source_not_verified")
    evidence["checks"] = {"localAddis": local, "remote": remote, "ethiopiaEligibleRemote": eligible_remote,
                          'domainTrack':'power_platform' if preferred else 'software' if software else 'unconfirmed',
                          'powerPlatformPreferred':preferred,
                          'experienceBasis':'responsibility_review' if skills_trial and fit_review.get('approved') else 'junior_mid_label' if labelled_level else 'numeric_requirement' if years else 'no_prior_experience' if re.search(r'\bno (?:(?:prior|professional|previous) )?experience\b',text,re.I) else 'unconfirmed',
                          'remoteEligibilityBasis':('explicit_global_scope' if GLOBAL.search(applicant_locations+' '+location) or REMOTE_ELIGIBILITY.search(text) else 'no_explicit_exclusion') if eligible_remote else 'not_established',
                          'experienceBand':'stretch' if years and max(years)>3 else 'core',
                          'reviewCaveats':fit_review.get('gaps',[]),
                          "visaSponsored": explicit_sponsored, 'visaSponsorshipInferred':inferred_sponsored,
                          'sponsorshipBasis':'explicit_posting' if explicit_sponsored else 'inferred_source_signal' if inferred_sponsored else 'not_established',
                          "publication": lead.get("published_at"),
                          'freshnessHours':window,'searchProfile':lead['search_profile'] if trial or skills_trial else 'standard',
                          **({'publicationBasis':'confirmed_open_date_unknown' if not bounds and confirmed_open else 'confirmed_open_older_posting' if confirmed_open and bounds[0]<now-timedelta(hours=window) else 'publication_date',
                              'openStatusVerified':confirmed_open,'responsibilityReview':fit_review,'experienceStretch':bool(years and max(years)>3)} if skills_trial else {}),
                          **({'pl200Evidence':certification,'crossBorderRemote':cross_border or inferred_cross_border,
                              'crossBorderRemoteExplicit':cross_border,
                              'crossBorderEligibilityBasis':'explicit_scope' if cross_border else 'inferred_no_explicit_exclusion' if inferred_cross_border else 'not_established'} if trial else {}),
                          'degreeAlternativeApplied':bool(visa_alternatives),
                          "experienceExcerpts": list(dict.fromkeys(m[0] for m in spans + minimums + role_tenure))}
    evidence["reasons"] = sorted(set(reject + pending))
    evidence["state"] = "rejected" if reject else "needs_evidence" if pending else "verified"
    return evidence

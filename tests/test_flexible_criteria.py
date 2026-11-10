from datetime import datetime, timedelta, timezone
import pytest
from discovery.policy import evaluate

NOW=datetime(2026,11,10,12,tzinfo=timezone.utc)

def job(**changes):
    return {'title':'Software Engineer','company':'Example','description':'Build software in Python and TypeScript. 2 years of experience.',
            'workplace':'Remote','location':'EMEA','official_verified':True,'published_at':(NOW-timedelta(days=6)).isoformat(),**changes}

@pytest.mark.parametrize('date,state',[
    ((NOW-timedelta(hours=168)).isoformat(),'verified'),
    ((NOW-timedelta(hours=168,seconds=1)).isoformat(),'rejected'),
    ('6 days ago','verified'),('7 days ago','rejected'),('', 'rejected'),
    ('2026-11-03','rejected'),('2026-11-04','verified')])
def test_maximum_publication_age_not_assumed(date,state):
    assert evaluate(job(published_at=date),NOW)['state']==state


def test_saved_relative_publication_age_does_not_reset_each_day():
    raw=job(published_at='6 days ago',publication_observed_at=NOW.isoformat())
    assert evaluate(raw,NOW)['state']=='verified'
    assert 'outside_freshness_window' in evaluate(raw,NOW+timedelta(days=1))['reasons']

@pytest.mark.parametrize('title,description,state',[
    ('Junior Software Engineer','Build software.','verified'),
    ('Mid-level Software Developer','Build software. 3–5 years of experience.','verified'),
    ('Junior/Mid Software Engineer','Build software.','verified'),
    ('Software Engineer','Build software. No professional experience is required.','verified'),
    ('Software Engineer','Build software.','rejected'),
    ('Software Engineer','Build software. 4 years of experience.','rejected'),
    ('Software Engineer','Build software. 1.5+ years of experience.','verified'),
    ('Mid-level Software Engineer','Build software. 6 years of experience.','rejected'),
    ('Senior Software Engineer','Build software. 2 years of experience.','rejected')])
def test_experience_labels_and_unknown_exclusion(title,description,state):
    assert evaluate(job(title=title,description=description),NOW)['state']==state

@pytest.mark.parametrize('applicants,state',[('', 'verified'),('EMEA','verified'),('Africa','verified'),
    ('United States','rejected'),('South Africa','rejected')])
def test_loose_remote_scope_keeps_explicit_restrictions(applicants,state):
    assert evaluate(job(applicant_locations=applicants),NOW)['state']==state

def test_inferred_eligibility_is_labelled_and_foreign_office_is_not_relaxed():
    audit=evaluate(job(location='France'),NOW)
    assert audit['state']=='verified' and audit['checks']['remoteEligibilityBasis']=='no_explicit_exclusion'
    assert evaluate(job(description='Build software. 2 years of experience. US residents only.'),NOW)['state']=='rejected'
    assert 'mandatory_foreign_presence' in evaluate(job(description='Build software. 2 years of experience. Must attend the office weekly.'),NOW)['reasons']

def test_no_experience_does_not_waive_mandatory_doctorate():
    desc='Build software. You have completed or are finishing a PhD in Computer Science. No professional experience is required.'
    assert 'required_doctorate_not_met' in evaluate(job(description=desc),NOW)['reasons']
    assert evaluate(job(description='Build software. 2 years of experience. PhD preferred.'),NOW)['state']=='verified'


def test_relevant_bsc_satisfies_explicit_visa_degree_or_experience_branch():
    text='Build software. 3+ years of experience in development.\nA bachelor’s degree in computer science or a closely related field.\nAlternatively, at least 10 years of relevant work experience may be accepted for visa purposes.\nIf you apply based on 10 years of work experience instead of a relevant bachelor’s degree, certificates proving the full 10 years of relevant experience are required for visa purposes.'
    audit=evaluate(job(description=text),NOW)
    assert audit['state']=='verified' and audit['checks']['degreeAlternativeApplied']
    assert len(audit['checks']['experienceExcerpts'])==1
    assert 'experience_above_three_years' in evaluate(job(description=text.replace('3+ years','6+ years')),NOW)['reasons']


def test_unqualified_degree_or_mandatory_ten_year_requirement_is_not_waived():
    text='Build software. A bachelor’s degree in computer science is required for visa purposes.\nRequired: 10 years of professional experience.'
    assert 'experience_above_three_years' in evaluate(job(description=text),NOW)['reasons']


def test_country_scoped_remote_needs_sponsorship_but_country_label_does_not():
    raw=job(location='Tokyo, Japan',description='Build software. 2 years of experience. Remote work (Only in Japan).')
    assert 'mandatory_foreign_presence' in evaluate(raw,NOW)['reasons']
    raw['description']+=' We provide work visa sponsorship.'
    audit=evaluate(raw,NOW)
    assert audit['state']=='verified' and audit['checks']['visaSponsored'] and not audit['checks']['ethiopiaEligibleRemote']
    assert evaluate(job(location='Japan'),NOW)['state']=='verified'


def test_required_destination_travel_is_not_worldwide_remote():
    raw=job(description='Build software. 2 years of experience. Advanced English and availability to travel to São Carlos when needed.')
    assert 'mandatory_foreign_presence' in evaluate(raw,NOW)['reasons']
    raw['description']+=' Travel is optional.'
    assert evaluate(raw,NOW)['state']=='verified'


@pytest.mark.parametrize('condition',[
    'This is a fully remote position based in India.',
    'Location: remote, based in the UK, Spain or Portugal.',
    'Candidates must reside in Poland.'
])
def test_explicit_foreign_base_requirement(condition):
    assert 'domestic_only_eligibility' in evaluate(job(description='Build software. 2 years of experience. '+condition),NOW)['reasons']

def test_only_job_main_publication_label_is_used():
    from discovery.audit import relative_publication_hint
    assert relative_publication_hint('<nav>Posted 1 day ago</nav><main>Apply before tomorrow</main>')==''
    assert relative_publication_hint('<main>Posted: 6 days ago</main>')=='6 days ago'
    assert relative_publication_hint('<main>Updated 1 day ago</main>')==''

from copy import deepcopy
from datetime import timedelta
from discovery.policy import evaluate
from discovery.dossier import build_dossier
from test_discovery import lead, NOW


def inferred():
    raw=lead(location='Berlin, Germany',workplace='Hybrid',description='Build software APIs. 2 years of experience. International relocation support is provided if required.')
    raw['sponsorship_inference']={'approved':True,'basis':'Reviewed international relocation signal; initial visa must be confirmed.',
        'evidence':'International relocation support is provided if required','source_url':raw['url'],'checked_at':NOW.isoformat()}
    return raw


def test_inference_is_labelled_and_never_an_explicit_promise():
    raw=inferred(); audit=evaluate(raw,NOW)
    assert audit['state']=='verified'
    assert audit['checks']['visaSponsorshipInferred']
    assert not audit['checks']['visaSponsored']
    job=build_dossier(raw,audit)
    assert 'Visa Sponsorship Inferred' in job['locationType']
    assert 'not explicitly guaranteed' in job['tailoredLetter']


def test_inference_requires_current_official_same_source_phrase():
    for changes in [{'official_verified':False}, {'description':'Build software APIs. 2 years of experience.'}]:
        raw=inferred();raw.update(changes)
        assert not evaluate(raw,NOW)['checks']['visaSponsorshipInferred']
    for changes in [{'source_url':'https://other.example/job'}, {'checked_at':(NOW-timedelta(days=2)).isoformat()}, {'approved':False}]:
        raw=inferred();raw['sponsorship_inference'].update(changes)
        assert not evaluate(raw,NOW)['checks']['visaSponsorshipInferred']


def test_inference_does_not_waive_explicit_restrictions():
    for phrase in ['We cannot sponsor a work visa.', 'US citizens only.']:
        raw=inferred(); raw['description']+=' '+phrase
        assert evaluate(raw,NOW)['state']!='verified'


def test_explicit_plural_visa_promise_takes_precedence():
    raw=inferred();raw['description']+=' Apply from anywhere. Sponsors Visas + relocation.'
    audit=evaluate(raw,NOW)
    assert audit['checks']['visaSponsored']
    assert not audit['checks']['visaSponsorshipInferred']

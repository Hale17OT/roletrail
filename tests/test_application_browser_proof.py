import copy
import json
from datetime import timedelta
import pytest
from discovery.policy import now_utc
from discovery.review import source_lead


@pytest.mark.parametrize('change,accepted',[
    ({},True),({'url':'https://docs.google.com/forms/other'},False),
    ({'checkedAt':(now_utc()-timedelta(days=2)).isoformat()},False),
    ({'state':'login_required'},False),({'method':'candidate_assumption'},False)])
def test_browser_access_proof_is_fresh_and_bound_to_exact_application(change,accepted):
    application='https://docs.google.com/forms/d/e/example/viewform'
    schema={'@type':'JobPosting','title':'Junior Software Engineer','description':'Build software.',
        'hiringOrganization':{'name':'Example'},'datePosted':now_utc().isoformat()}
    page={'data':{'title':schema['title'],'description':'Build software.','career_page_link':application},'jobSchema':schema}
    class Fetch:
        def get(self,url,**kwargs):
            return {'url':url,'status':401 if url==application else 200,
                'text':'<script id="__NEXT_DATA__">'+json.dumps({'props':{'pageProps':page}})+'</script>'}
    proof={'url':application,'state':'accessible','method':'signed_in_browser','formTitle':'Example Application',
        'checkedAt':now_utc().isoformat(),'evidence':'Observed active form and Next control',**change}
    _,facts=source_lead({'url':'https://example.com/job','format':'ethiojobs','authority':'official',
        'applicationBrowserVerification':proof},Fetch())
    assert facts['applicationHttpStatus']==401
    assert bool(facts.get('applicationBrowserVerified')) is accepted

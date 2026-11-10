import pytest
from discovery.review import source_lead

@pytest.mark.parametrize('title,upload,authority,expected',[
    ('Junior Java Developer','<input type="file" name="resume">','official',True),
    ('Other vacancy','<input type="file" name="resume">','official',False),
    ('Junior Java Developer','','official',False),
    ('Junior Java Developer','<input type="file" disabled>','official',False),
    ('Junior Java Developer','<input type="file">','listing',False),
])
def test_embedded_application_requires_exact_title_and_enabled_upload(title,upload,authority,expected):
    class Fetch:
        def get(self,url,**kwargs):
            return {'url':url,'status':200,'text':f'<h1>{title}</h1><article>Develop software APIs.</article><form>{upload}</form>'}
    source={'url':'https://example.org/job/123','title':'Junior Java Developer','company':'Example',
        'format':'dom','selector':'article','authority':authority,'openSelector':'h1','applicationFormSelector':'form'}
    lead,_=source_lead(source,Fetch())
    assert bool(lead.get('open_check'))==expected

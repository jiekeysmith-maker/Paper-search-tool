"""Regressions for Xplore's actual public HTML schema observed 2026-10-06.

The fixture is a field projection of document 10274722, not an API response.
No credentials, userInfo, restricted requests, or full-text content are retained.
"""
import json
import pytest
from tpami_adapter import TPAMIAdapter, EarlyAccessRecord
from tpami_policy import TITLE


def public_metadata():
    return dict(title='A Bayesian Federated Learning Framework With Online Laplace Approximation',
        articleNumber='10274722', publicationNumber='34', publicationTitle=TITLE,
        contentType='periodicals', contentTypeDisplay='Journals', isJournal=True,
        isEarlyAccess=False, isEphemera=False, volume='46', issue='1',
        publicationDate='January 2024', publicationYear='2024',
        displayPublicationDate='09 October 2023', doi='10.1109/TPAMI.2023.3322743',
        isNumber='10345401', pdfUrl='/stamp/stamp.jsp?tp=&arnumber=10274722',
        authors=[dict(name=n) for n in ('Liangxi Liu','Xi Jiang','Feng Zheng','Hong Chen',
                                      'Guo-Jun Qi','Heng Huang','Ling Shao')],
        abstract='Federated learning (FL) allows multiple clients to collaboratively learn a globally shared model through cycles of model aggregation and local model training, without the need to share data.')


def final_context():
    return dict(Year=2024,Volume='46',Issue='1',Issue_Publication_Date='2024-01',
        Issue_URL='https://ieeexplore.ieee.org/xpl/tocresult.jsp?isnumber=10345401&punumber=34')


def test_public_html_schema_keeps_distinct_publication_events():
    data=public_metadata()
    html='<script>xplGlobal.document.metadata='+json.dumps(data)+';</script>'
    row=TPAMIAdapter().detail(html,'https://ieeexplore.ieee.org/document/10274722',final_context())
    assert row['Year']==2024 and row['Issue_Publication_Date']=='2024-01'
    assert row['First_Official_Publication_Date']=='2023-10-09'
    assert row['Publisher_Issue_Date']=='January 2024'
    assert row['Early_Access_Date']==''  # Online date is not explicitly labeled EA.
    assert row['PDF_URL'].startswith('https://ieeexplore.ieee.org/')
    assert len(row['Authors'].split(';'))==7


@pytest.mark.parametrize('key,value',[
    ('isJournal',False),('contentTypeDisplay','Conferences'),('isNumber','999'),
    ('publicationDate','February 2024'),('displayPublicationDate','unknown'),
    ('displayPublicationDate','20 February 2024'),('isEphemera',True),
])
def test_public_flags_dates_and_issue_identity_fail_closed(key,value):
    data=public_metadata();data[key]=value
    with pytest.raises(ValueError):TPAMIAdapter().metadata(data,final_context())


def test_missing_assignment_is_not_invented_early_access():
    data=public_metadata();data['issue']=''
    with pytest.raises(ValueError,match='no explicit Early Access') as exc:
        TPAMIAdapter().metadata(data,final_context())
    assert not isinstance(exc.value,EarlyAccessRecord)
    data['isEarlyAccess']=True
    with pytest.raises(EarlyAccessRecord):TPAMIAdapter().metadata(data,final_context())


def test_generic_journal_type_does_not_excuse_suspected_correction():
    data=public_metadata();data.update(title='Correction to a model',articleType='Journal Article')
    with pytest.raises(ValueError,match='non-research'):TPAMIAdapter().metadata(data,final_context())

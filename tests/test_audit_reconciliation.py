"""Synthetic identity cases; no production titles, IDs or expected venue counts."""
import pytest
import json
from submission_identity import compatible_authors, forum_id
from venue_audit import reconcile, audit
from venue_pipelines import conference_program


def paper(**extra):
    return dict(dict(Title='A synthetic study', Authors='Alice Smith; Bob Jones',
        Official_URL='https://proceedings.mlr.press/v999/synthetic.html',
        OpenReview_URL='https://openreview.net/forum?id=synthetic', Paper_ID='fixture',
        Venue='ICML', Year=2099, Track='Conference', Abstract='Offline evidence.',
        PDF_URL='', Formal_Publication_Evidence='Synthetic official publication'), **extra)


def event(**extra):
    return paper(**dict(dict(Official_URL='', Program_Event_ID='poster',
        Source_Group='https://openreview.net/group?id=ICML.cc/2099/Conference'), **extra))


def test_oral_poster_title_versions_one_submission():
    rows, count = reconcile([paper()], [event(), event(Program_Event_ID='oral', Title='An earlier title',
        OpenReview_URL='http://openreview.net/forum?ref=program&id=synthetic#discussion')])
    assert count == 1
    assert {r['Status'] for r in rows} == {'TITLE_VARIANT', 'DUPLICATE_EVENT'}
    assert all(r['Resolved'] for r in rows)
    assert len(next(r for r in rows if r['Status']=='TITLE_VARIANT')['Program_Events']) == 2


def test_all_observed_titles_can_find_publisher_without_forum_link():
    rows,count=reconcile([paper(OpenReview_URL='')],[event(Title='Historical title'),event(Program_Event_ID='oral')])
    assert count==1 and all(r['Resolved'] for r in rows)


@pytest.mark.parametrize('a,b', [
    ('Jos\u00e9 Garc\u00eda; Alice Smith', 'Jose\u0301 Garci\u0301a; Alice Smith'),
    ('Ren&eacute; Dupont; Alice Smith', 'René Dupont; Alice Smith'),
    ('A. Smith; B. Jones', 'Alice Smith, Bob Jones'),
    ('Smith, Alice; Jones, Bob', 'Alice Smith; Bob Jones'),
    ('Alice M. Smith; Bob Jones', 'Alice Smith; Bob Jones'),
    (' Alice   Smith ; Bob Jones ', 'Bob Jones; Alice Smith'),
    ("Anne O’Neil; Bob Jones", "Anne O'Neil; Bob Jones"),
    ("Anne O'Neil; Bob Jones", "Anne ONeil; Bob Jones"),
    ('Anne O&#x27;Neil; Bob Jones', 'Anne O&amp;#x27;Neil; Bob Jones'),
])
def test_author_representation(a,b):
    assert compatible_authors(a,b)
    rows,_=reconcile([paper(OpenReview_URL='', Authors=a)], [event(OpenReview_URL='',Authors=b)])
    assert all(r['Resolved'] for r in rows)


@pytest.mark.parametrize('a,b', [('Alice Smith', 'Bob Smith'), ('Alice Smith', 'Alice Brown'),
    ('A Smith; A Smith', 'Alice Smith; Andrew Smith'), ('', ''), ('Alice Smith; Bob Jones','Alice Smith')])
def test_genuine_or_ambiguous_author_conflicts(a,b):
    assert not compatible_authors(a,b)
    rows,_=reconcile([paper(OpenReview_URL='',Authors=a)], [event(OpenReview_URL='',Authors=b)])
    assert any(not r['Resolved'] for r in rows)


def test_shared_forum_genuine_author_conflict_stays_review(tmp_path):
    p=paper()
    result=audit(tmp_path,'ICML',2099,[p],[p],[event(Authors='Carol Green; David White')],evidence_complete=True)
    assert result['status']=='REVIEW_REQUIRED'


def test_distinct_forums_same_title_not_matched():
    rows,_=reconcile([paper()], [event(OpenReview_URL='https://openreview.net/forum?id=different')])
    assert any(r['Reason']=='Conflicting explicit OpenReview forum IDs' for r in rows)
    assert not all(r['Resolved'] for r in rows)


def test_duplicate_publisher_identity_not_resolved():
    rows,_=reconcile([paper(),paper(Official_URL='https://proceedings.mlr.press/v999/another.html')],[event()])
    assert sum(r['Status']=='DUPLICATE_IDENTITY' for r in rows)==2


def test_position_publisher_evidence_and_real_absence(tmp_path):
    position=event(Source_Group='https://openreview.net/group?id=ICML.cc/2099/Position_Paper_Track')
    p=paper()
    assert audit(tmp_path/'present','ICML',2099,[p],[p],[position],evidence_complete=True)['status']=='VERIFIED'
    missing=event(Title='Another study',OpenReview_URL='https://openreview.net/forum?id=absent',
                  Program_Event_ID='other', Source_Group=position['Source_Group'])
    result=audit(tmp_path/'absent','ICML',2099,[p],[p],[position,missing],evidence_complete=True)
    assert result['status']=='REVIEW_REQUIRED' and result['counts_by_class']['PROGRAM_ONLY']==1


def test_no_identity_fuzzy_title_is_not_confirmation():
    rows,count=reconcile([paper(OpenReview_URL='')],[event(OpenReview_URL='',Title='A synthetic study revised')])
    assert count==1 and {r['Status'] for r in rows}=={'INDEX_ONLY','PROGRAM_ONLY'}


def test_missing_identity_does_not_group_same_title_events():
    rows,count=reconcile([paper(OpenReview_URL='')],[event(OpenReview_URL=''),event(OpenReview_URL='',Program_Event_ID='oral')])
    assert count==2 and any(r['Status']=='DUPLICATE_IDENTITY' for r in rows)


def test_transitive_conflict_never_erased_by_grouping():
    a=event(Official_URL='https://proceedings.mlr.press/v999/synthetic.html')
    b=event(Official_URL=a['Official_URL'],OpenReview_URL='https://openreview.net/forum?id=other',Program_Event_ID='oral')
    rows,count=reconcile([paper()],[a,b])
    assert count==1 and any(not r['Resolved'] for r in rows)


@pytest.mark.parametrize('value',['','https://evil.test/forum?id=x','https://openreview.net/group?id=x',
    'https://openreview.net/forum?id=x&id=y','https://openreview.net/forum?id='])
def test_only_valid_forum_identity(value):
    assert not forum_id(value)


def test_shared_stable_id_partial_author_coverage_is_observed_not_new_identity():
    rows,count=reconcile([paper()],[event(),event(Program_Event_ID='oral',Authors='Alice Smith')])
    assert count==1 and all(r['Resolved'] for r in rows)
    rows,_=reconcile([paper()],[event(Authors='Alice Smith')])
    assert all(r['Resolved'] for r in rows) and 'coverage differs' in rows[0]['Reason']


@pytest.mark.parametrize('a,b',[('Alice Maria Smith','Alice Smith'),('Søren Holm','Soren Holm'),
    ('Alice (Ada) Smith','Alice Smith'),('Alice Smith','AliceSmith')])
def test_shared_forum_name_versions_only(a,b):
    rows,_=reconcile([paper(Authors=a)],[event(Authors=b)])
    assert all(r['Resolved'] for r in rows)
    rows,_=reconcile([paper(Authors=a,OpenReview_URL='')],[event(Authors=b,OpenReview_URL='')])
    assert any(not r['Resolved'] for r in rows)


def test_acquisition_retains_explicit_event_relation_and_rejects_placeholder_identity():
    def raw(n,kind,review,related):
        return dict(id=n,name='A synthetic study',authors=[dict(fullname='Alice Smith'),dict(fullname='Bob Jones')],
            decision='Accept',sourceurl='https://openreview.net/group?id=ICLR.cc/2099/Conference',
            virtualsite_url=f'/virtual/2099/{kind}/{n}',paper_url=review,event_type=kind,
            related_events_ids=related)
    data=dict(count=2,next=None,results=[raw(1,'oral','https://openreview.net/forum?id=2099-Oral--123-abcdef',[2]),
        raw(2,'poster','https://openreview.net/forum?id=synthetic',[])])
    class Cache:
        def get(self,url):return json.dumps(data)
    program,excluded=conference_program(Cache(),'ICLR',2099)
    assert not excluded and not program[0]['OpenReview_URL']
    assert '2099-Oral' in program[0]['Event_OpenReview_Placeholders']
    rows,count=reconcile([paper()],program)
    assert count==1 and all(r['Resolved'] for r in rows)


@pytest.mark.parametrize('change',[
    dict(OpenReview_URL='https://openreview.net/forum?id=conflicting'),
    dict(Authors='Carol Green'), dict(Source_Group='other group'),dict(Title='Unrelated talk'),
])
def test_event_relation_requires_corroboration(change):
    a=event(Program_Event_ID='oral',Related_Event_IDs='["poster"]',**change)
    b=event()
    # Remove the shared stable ID except in the explicit different-ID case.
    if 'OpenReview_URL' not in change:a['OpenReview_URL']=''
    _,count=reconcile([paper()],[a,b])
    assert count==2


def test_conflicting_links_on_program_event_fail_closed():
    data=dict(count=1,next=None,results=[dict(id=1,name='Synthetic',authors=[],decision='Accept',
        sourceurl='https://openreview.net/group?id=ICLR.cc/2099/Conference',virtualsite_url='/virtual/2099/poster/1',
        paper_url='https://openreview.net/forum?id=first',eventmedia=[dict(uri='https://openreview.net/forum?id=second')])])
    class Cache:
        def get(self,url):return json.dumps(data)
    with pytest.raises(ValueError,match='Conflicting OpenReview'):conference_program(Cache(),'ICLR',2099)

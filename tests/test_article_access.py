import uuid
from pathlib import Path

from wechat_agent.article_access import ArticleAccessStore

ROOT=Path(__file__).resolve().parents[1]/'output'/'_article_access_test'

def test_article_owner_admin_and_share_matrix():
    path=ROOT/uuid.uuid4().hex;path.mkdir(parents=True,exist_ok=True)
    store=ArticleAccessStore(path/'auth.db')
    meta={'id':'a'*32,'owner_user_id':'editor-owner'}
    admin={'id':'admin','role':'admin'};owner={'id':'editor-owner','role':'editor'}
    other={'id':'editor-other','role':'editor'};viewer={'id':'viewer','role':'viewer'}
    for action in ('view','edit','push','delete'): assert store.allowed(meta,admin,action)
    for action in ('view','edit','push','delete'): assert store.allowed(meta,owner,action)
    assert not store.allowed(meta,other,'view')
    store.set_grant(meta['id'],other['id'],['view','edit'],'admin')
    assert store.allowed(meta,other,'view') and store.allowed(meta,other,'edit')
    assert not store.allowed(meta,other,'push') and not store.allowed(meta,other,'delete')
    store.set_grant(meta['id'],viewer['id'],['view'],'admin')
    assert store.allowed(meta,viewer,'view') and not store.allowed(meta,viewer,'edit')
    store.revoke(meta['id'],other['id'])
    assert not store.allowed(meta,other,'view')

def test_creator_can_manage_own_article_but_cannot_push():
    path=ROOT/uuid.uuid4().hex;path.mkdir(parents=True,exist_ok=True)
    store=ArticleAccessStore(path/'auth.db');meta={'id':'c'*32,'owner_user_id':'creator-owner'}
    owner={'id':'creator-owner','role':'creator'};shared={'id':'creator-shared','role':'creator'}
    assert store.allowed(meta,owner,'view') and store.allowed(meta,owner,'edit') and store.allowed(meta,owner,'delete')
    assert not store.allowed(meta,owner,'push')
    store.set_grant(meta['id'],shared['id'],['view','edit','push'],'admin')
    assert store.allowed(meta,shared,'view') and store.allowed(meta,shared,'edit')
    assert not store.allowed(meta,shared,'push') and not store.allowed(meta,shared,'delete')


def test_push_grant_does_not_allow_delete():
    path=ROOT/uuid.uuid4().hex;path.mkdir(parents=True,exist_ok=True)
    store=ArticleAccessStore(path/'auth.db');meta={'id':'b'*32,'owner_user_id':'owner'}
    editor={'id':'shared','role':'editor'}
    store.set_grant(meta['id'],editor['id'],['push'],'admin')
    assert store.allowed(meta,editor,'view') and store.allowed(meta,editor,'push')
    assert not store.allowed(meta,editor,'edit') and not store.allowed(meta,editor,'delete')

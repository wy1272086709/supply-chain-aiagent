from app.modules.documents.access import (
    ES_CHUNK_VISIBILITY_FIELDS,
    ES_DOC_VISIBILITY_FIELDS,
    access_from_user,
    can_read_document,
    can_write_document,
    es_visibility_filter,
)
from app.modules.documents.status import DocumentStatus


class _Doc:
    def __init__(self, author_id=None, team_id=None, is_public=False, status=DocumentStatus.PUBLISHED):
        self.author_id = author_id
        self.team_id = team_id
        self.is_public = is_public
        self.status = status


ADMIN_SCOPE = {"unrestricted": True, "userId": "9", "teamIds": []}
USER_SCOPE = {"unrestricted": False, "userId": "1", "teamIds": ["10"]}


def test_access_from_user():
    assert access_from_user({"roles": ["ROLE_ADMIN"], "userId": "1", "teamIds": []})["unrestricted"]
    assert access_from_user({"roles": ["ROLE_REVIEWER"], "userId": "1", "teamIds": []})["unrestricted"]
    assert not access_from_user({"roles": ["ROLE_USER"], "userId": "1", "teamIds": []})["unrestricted"]


def test_read_matrix():
    assert can_read_document(_Doc(), ADMIN_SCOPE)
    assert can_read_document(_Doc(author_id="1"), USER_SCOPE)  # 自己写的
    assert can_read_document(_Doc(is_public=True), USER_SCOPE)  # 公开
    assert can_read_document(_Doc(team_id="10"), USER_SCOPE)  # 同团队
    assert not can_read_document(_Doc(team_id="99"), USER_SCOPE)
    assert not can_read_document(_Doc(author_id="1", status=DocumentStatus.DRAFT),
                                 {"unrestricted": False, "userId": "2", "teamIds": []})


def test_write_matrix():
    assert can_write_document(_Doc(author_id="2"), {"roles": ["ROLE_ADMIN"], "userId": "1"})
    assert can_write_document(_Doc(author_id="1"), {"roles": ["ROLE_USER"], "userId": "1"})
    assert not can_write_document(_Doc(author_id="2"), {"roles": ["ROLE_USER"], "userId": "1"})


def test_es_visibility_filter():
    assert es_visibility_filter(ADMIN_SCOPE, ES_DOC_VISIBILITY_FIELDS) is None
    f = es_visibility_filter(USER_SCOPE, ES_CHUNK_VISIBILITY_FIELDS)
    assert f["bool"]["minimum_should_match"] == 1
    terms = f["bool"]["should"]
    assert {"term": {"is_public": True}} in terms
    assert {"terms": {"team_id": ["10"]}} in terms
    # 无团队时不加 terms 子句
    no_team = {"unrestricted": False, "userId": "1", "teamIds": []}
    assert len(es_visibility_filter(no_team, ES_DOC_VISIBILITY_FIELDS)["bool"]["should"]) == 2

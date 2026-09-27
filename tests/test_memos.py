import pytest
from fastapi.testclient import TestClient

from helpersonagent import api


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "test.db"))
    with TestClient(api.app) as c:
        yield c


def _create(client, title, body):
    res = client.post("/memos", json={"title": title, "body": body})
    assert res.status_code == 201
    return res.json()


def test_create_and_get_memo(client):
    created = client.post("/memos", json={"title": "첫 메모", "body": "안녕"})
    assert created.status_code == 201

    got = client.get(f"/memos/{created.json()['id']}")
    assert got.status_code == 200
    assert got.json()["title"] == "첫 메모"


def test_get_missing_memo_returns_404(client):
    assert client.get("/memos/999").status_code == 404


# --- /memos/search ---


def test_search_matches_title(client):
    _create(client, "장보기 목록", "우유, 계란")
    _create(client, "회의록", "다음 주 일정")

    res = client.get("/memos/search", params={"q": "장보기"})
    assert res.status_code == 200
    assert [m["title"] for m in res.json()] == ["장보기 목록"]


def test_search_matches_body(client):
    _create(client, "장보기 목록", "우유, 계란")
    _create(client, "회의록", "다음 주 일정")

    res = client.get("/memos/search", params={"q": "일정"})
    assert res.status_code == 200
    assert [m["title"] for m in res.json()] == ["회의록"]


def test_search_is_case_sensitive_substring(client):
    _create(client, "Python", "notes")

    assert len(client.get("/memos/search", params={"q": "yth"}).json()) == 1
    assert client.get("/memos/search", params={"q": "python"}).json() == []


def test_search_empty_result_returns_empty_list(client):
    _create(client, "장보기 목록", "우유, 계란")

    res = client.get("/memos/search", params={"q": "없는말"})
    assert res.status_code == 200
    assert res.json() == []


def test_search_requires_q(client):
    assert client.get("/memos/search").status_code == 422


def test_search_rejects_empty_q(client):
    _create(client, "a", "1")

    assert client.get("/memos/search", params={"q": ""}).status_code == 422


def test_search_route_takes_precedence_over_memo_id(client):
    # /memos/search 가 /memos/{memo_id} 보다 먼저 매칭되어야 한다.
    assert client.get("/memos/search", params={"q": "x"}).status_code == 200


def test_search_sort_by_id_and_title(client):
    _create(client, "나", "x")
    _create(client, "가", "x")
    _create(client, "다", "x")

    by_id = client.get("/memos/search", params={"q": "x", "sort": "id"}).json()
    assert [m["title"] for m in by_id] == ["나", "가", "다"]

    by_title = client.get("/memos/search", params={"q": "x", "sort": "title"}).json()
    assert [m["title"] for m in by_title] == ["가", "나", "다"]


@pytest.mark.parametrize(
    "bad_sort",
    [
        "body",
        "id; DROP TABLE memos",
        "(SELECT body FROM memos LIMIT 1)",
        "CASE WHEN 1 THEN id ELSE title END",
    ],
)
def test_search_rejects_unlisted_sort_and_keeps_table_intact(client, bad_sort):
    _create(client, "안전", "데이터")

    res = client.get("/memos/search", params={"q": "안전", "sort": bad_sort})
    assert res.status_code == 422

    # 주입 시도 뒤에도 테이블과 데이터가 그대로 있어야 한다.
    after = client.get("/memos/search", params={"q": "안전"})
    assert after.status_code == 200
    assert [m["title"] for m in after.json()] == ["안전"]


def test_search_limit_and_offset(client):
    for i in range(5):
        _create(client, f"메모{i}", "공통")

    page1 = client.get("/memos/search", params={"q": "공통", "limit": 2}).json()
    page2 = client.get(
        "/memos/search", params={"q": "공통", "limit": 2, "offset": 2}
    ).json()
    page3 = client.get(
        "/memos/search", params={"q": "공통", "limit": 2, "offset": 4}
    ).json()

    assert [m["title"] for m in page1] == ["메모0", "메모1"]
    assert [m["title"] for m in page2] == ["메모2", "메모3"]
    assert [m["title"] for m in page3] == ["메모4"]


def test_search_default_limit_is_20(client):
    for i in range(25):
        _create(client, f"메모{i}", "공통")

    assert len(client.get("/memos/search", params={"q": "공통"}).json()) == 20


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"offset": api.MAX_OFFSET + 1}],
)
def test_search_rejects_out_of_range_paging(client, params):
    res = client.get("/memos/search", params={"q": "x", **params})
    assert res.status_code == 422


# --- 2회차 수정: 입력 길이 제한, FTS5 색인 검색 ---


@pytest.mark.parametrize(
    "payload",
    [
        {"title": "", "body": "x"},
        {"title": "a" * (api.TITLE_MAX_LENGTH + 1), "body": "x"},
        {"title": "a", "body": "b" * (api.BODY_MAX_LENGTH + 1)},
        {"title": "a"},
        {"title": 1, "body": 2},
    ],
)
def test_create_rejects_invalid_payload(client, payload):
    assert client.post("/memos", json=payload).status_code == 422


def test_create_accepts_max_length(client):
    res = client.post(
        "/memos",
        json={"title": "a" * api.TITLE_MAX_LENGTH, "body": "b" * api.BODY_MAX_LENGTH},
    )
    assert res.status_code == 201


def test_search_rejects_overlong_q(client):
    res = client.get("/memos/search", params={"q": "a" * (api.TITLE_MAX_LENGTH + 1)})
    assert res.status_code == 422


def test_search_long_query_matches_substring_across_words(client):
    _create(client, "회의록", "다음 주 일정 정리")
    _create(client, "장보기", "우유 계란")

    res = client.get("/memos/search", params={"q": "다음 주 일"})
    assert [m["title"] for m in res.json()] == ["회의록"]


def test_search_long_query_is_case_sensitive(client):
    _create(client, "Python notes", "hello")

    assert len(client.get("/memos/search", params={"q": "Python"}).json()) == 1
    assert client.get("/memos/search", params={"q": "python"}).json() == []


@pytest.mark.parametrize("q", ['say "hi" now', "' OR 1=1 --", "100% sure", "a_b_c"])
def test_search_treats_special_characters_literally(client, q):
    _create(client, "특수", q)
    _create(client, "다른", "아무 내용")

    res = client.get("/memos/search", params={"q": q})
    assert res.status_code == 200
    assert [m["title"] for m in res.json()] == ["특수"]


@pytest.mark.parametrize("q", ["OR", "AND", "NOT", "NEAR"])
def test_search_fts_operators_are_not_interpreted(client, q):
    _create(client, "x", "hello")

    res = client.get("/memos/search", params={"q": q})
    assert res.status_code == 200
    assert res.json() == []


def test_search_index_covers_rows_created_before_index(tmp_path, monkeypatch):
    import sqlite3

    db = tmp_path / "old.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE memos (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "title TEXT NOT NULL, body TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO memos (title, body) VALUES ('옛 메모', '색인 이전 데이터')")

    monkeypatch.setattr(api, "DB_PATH", str(db))
    with TestClient(api.app) as c:
        res = c.get("/memos/search", params={"q": "색인 이전"})
        assert [m["title"] for m in res.json()] == ["옛 메모"]


def test_search_sort_by_title_breaks_ties_by_id(client):
    for i in range(4):
        _create(client, "같은 제목", f"본문{i}")

    page1 = client.get(
        "/memos/search", params={"q": "본문", "sort": "title", "limit": 2}
    ).json()
    page2 = client.get(
        "/memos/search", params={"q": "본문", "sort": "title", "limit": 2, "offset": 2}
    ).json()
    assert [m["body"] for m in page1 + page2] == ["본문0", "본문1", "본문2", "본문3"]


def test_search_boundary_paging_values(client):
    for i in range(3):
        _create(client, f"메모{i}", "공통")

    assert len(client.get("/memos/search", params={"q": "공통", "limit": 1}).json()) == 1
    assert client.get("/memos/search", params={"q": "공통", "limit": 100}).status_code == 200
    assert client.get("/memos/search", params={"q": "공통", "offset": 999}).json() == []


# --- 3회차 지적: FTS 분기 정렬·페이징, 재기동 경로 ---


@pytest.mark.parametrize("q", ["공통", "공통내용"], ids=["instr-2자", "fts-3자"])
def test_search_sort_and_paging_same_in_both_branches(client, q):
    for title in ["나", "가", "다", "라", "마"]:
        _create(client, title, "공통내용")

    by_id = client.get("/memos/search", params={"q": q, "sort": "id"}).json()
    assert [m["title"] for m in by_id] == ["나", "가", "다", "라", "마"]

    by_title = client.get("/memos/search", params={"q": q, "sort": "title"}).json()
    assert [m["title"] for m in by_title] == ["가", "나", "다", "라", "마"]

    page1 = client.get(
        "/memos/search", params={"q": q, "sort": "title", "limit": 2}
    ).json()
    page2 = client.get(
        "/memos/search", params={"q": q, "sort": "title", "limit": 2, "offset": 2}
    ).json()
    page3 = client.get(
        "/memos/search", params={"q": q, "sort": "title", "limit": 2, "offset": 4}
    ).json()
    assert [m["title"] for m in page1 + page2 + page3] == ["가", "나", "다", "라", "마"]


@pytest.mark.parametrize("q", ["본문", "본문내용"], ids=["instr-2자", "fts-3자"])
def test_search_title_ties_break_by_id_in_both_branches(client, q):
    for i in range(4):
        _create(client, "같은 제목", f"본문내용{i}")

    page1 = client.get(
        "/memos/search", params={"q": q, "sort": "title", "limit": 2}
    ).json()
    page2 = client.get(
        "/memos/search", params={"q": q, "sort": "title", "limit": 2, "offset": 2}
    ).json()
    assert [m["body"] for m in page1 + page2] == [f"본문내용{i}" for i in range(4)]


def test_restart_with_existing_index_keeps_search_consistent(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "restart.db"))

    with TestClient(api.app) as c:
        _create(c, "첫 세션", "재기동 이전 메모")

    # 두 번째 기동: memos_fts가 이미 있으므로 rebuild 없이 트리거만 재확인한다.
    with TestClient(api.app) as c:
        before = c.get("/memos/search", params={"q": "재기동"}).json()
        assert [m["title"] for m in before] == ["첫 세션"]

        _create(c, "둘째 세션", "재기동 이후 메모")
        after = c.get("/memos/search", params={"q": "재기동"}).json()
        assert [m["title"] for m in after] == ["첫 세션", "둘째 세션"]


def test_fts_index_follows_update_and_delete(client, tmp_path):
    import sqlite3

    created = _create(client, "원본", "수정 전 내용")
    with sqlite3.connect(api.DB_PATH) as conn:
        conn.execute("UPDATE memos SET body = '수정 후 내용' WHERE id = ?", (created["id"],))

    assert client.get("/memos/search", params={"q": "수정 전"}).json() == []
    assert len(client.get("/memos/search", params={"q": "수정 후"}).json()) == 1

    with sqlite3.connect(api.DB_PATH) as conn:
        conn.execute("DELETE FROM memos WHERE id = ?", (created["id"],))

    assert client.get("/memos/search", params={"q": "수정 후"}).json() == []


# --- PATCH /memos/{memo_id} ---


def test_patch_title_only_keeps_body(client):
    created = _create(client, "원래 제목", "원래 본문")

    res = client.patch(f"/memos/{created['id']}", json={"title": "새 제목"})
    assert res.status_code == 200
    assert res.json() == {"id": created["id"], "title": "새 제목", "body": "원래 본문"}

    got = client.get(f"/memos/{created['id']}").json()
    assert got == {"id": created["id"], "title": "새 제목", "body": "원래 본문"}


def test_patch_body_only_keeps_title(client):
    created = _create(client, "원래 제목", "원래 본문")

    res = client.patch(f"/memos/{created['id']}", json={"body": "새 본문"})
    assert res.status_code == 200
    assert res.json() == {"id": created["id"], "title": "원래 제목", "body": "새 본문"}


def test_patch_both_fields(client):
    created = _create(client, "원래 제목", "원래 본문")

    res = client.patch(
        f"/memos/{created['id']}", json={"title": "새 제목", "body": "새 본문"}
    )
    assert res.status_code == 200
    assert res.json() == {"id": created["id"], "title": "새 제목", "body": "새 본문"}


def test_patch_missing_memo_returns_404(client):
    res = client.patch("/memos/999", json={"title": "없음"})
    assert res.status_code == 404


def test_patch_does_not_touch_other_memos(client):
    first = _create(client, "첫째", "1")
    second = _create(client, "둘째", "2")

    client.patch(f"/memos/{first['id']}", json={"title": "바뀐 첫째", "body": "one"})

    assert client.get(f"/memos/{second['id']}").json() == second


def test_patch_empty_body_allowed(client):
    created = _create(client, "제목", "내용")

    res = client.patch(f"/memos/{created['id']}", json={"body": ""})
    assert res.status_code == 200
    assert res.json()["body"] == ""


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"title": ""},
        {"title": None},
        {"body": None},
        {"title": "a" * (api.TITLE_MAX_LENGTH + 1)},
        {"body": "b" * (api.BODY_MAX_LENGTH + 1)},
        {"title": 1},
        {"unknown": "x"},
    ],
)
def test_patch_rejects_invalid_payload_and_keeps_memo(client, payload):
    created = _create(client, "제목", "내용")

    assert client.patch(f"/memos/{created['id']}", json=payload).status_code == 422
    assert client.get(f"/memos/{created['id']}").json() == created


def test_patch_accepts_max_length(client):
    created = _create(client, "제목", "내용")

    res = client.patch(
        f"/memos/{created['id']}",
        json={"title": "a" * api.TITLE_MAX_LENGTH, "body": "b" * api.BODY_MAX_LENGTH},
    )
    assert res.status_code == 200


def test_patch_non_integer_id_returns_422(client):
    assert client.patch("/memos/abc", json={"title": "x"}).status_code == 422


def test_patch_updates_search_index(client):
    created = _create(client, "원본", "수정 전 내용")

    client.patch(f"/memos/{created['id']}", json={"body": "수정 후 내용"})

    assert client.get("/memos/search", params={"q": "수정 전"}).json() == []
    hits = client.get("/memos/search", params={"q": "수정 후"}).json()
    assert [m["id"] for m in hits] == [created["id"]]

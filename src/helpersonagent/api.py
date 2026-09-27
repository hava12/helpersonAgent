import sqlite3
from contextlib import asynccontextmanager, closing
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

DB_PATH = "memos.db"

TITLE_MAX_LENGTH = 200
BODY_MAX_LENGTH = 10_000

SORT_COLUMNS: dict[str, str] = {"id": "id", "title": "title"}
SortKey = Literal["id", "title"]

# FTS5 trigram 토크나이저는 3글자 미만 질의를 색인으로 처리하지 못한다.
FTS_MIN_QUERY_LENGTH = 3
# OFFSET은 그만큼 행을 읽고 버리므로 상한을 둔다.
MAX_OFFSET = 10_000


class MemoIn(BaseModel):
    title: str = Field(min_length=1, max_length=TITLE_MAX_LENGTH)
    body: str = Field(max_length=BODY_MAX_LENGTH)


class MemoPatch(BaseModel):
    """PATCH 본문. 보낸 필드만 바뀌며, 보낸 필드에 null은 허용하지 않는다."""

    title: str | None = Field(None, min_length=1, max_length=TITLE_MAX_LENGTH)
    body: str | None = Field(None, max_length=BODY_MAX_LENGTH)

    @field_validator("title", "body")
    @classmethod
    def _reject_explicit_null(cls, v: str | None) -> str:
        # 기본값(None)에는 검증기가 돌지 않으므로, 여기 None이 오면 명시적 null이다.
        if v is None:
            raise ValueError("null is not allowed; omit the field instead")
        return v


def get_conn() -> sqlite3.Connection:
    # 여러 워커가 동시에 기동해 스키마를 만들 때 락 대기를 허용한다.
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(get_conn()) as conn:
        # 스키마 생성과 색인 백필을 한 쓰기 트랜잭션으로 묶어,
        # 워커 여러 개가 동시에 기동해도 rebuild는 한 프로세스만 수행한다.
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS memos ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "title TEXT NOT NULL, "
            "body TEXT NOT NULL)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memos_title ON memos (title)")

        fts_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memos_fts'"
        ).fetchone()
        # 부분 문자열 검색을 색인으로 처리하기 위한 외부 콘텐츠 FTS5 테이블.
        # case_sensitive 1 로 기존 instr() 검색과 같은 대소문자 구분 의미를 유지한다.
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memos_fts USING fts5("
            "title, body, content='memos', content_rowid='id', "
            "tokenize='trigram case_sensitive 1')"
        )
        # executescript는 진행 중인 트랜잭션을 커밋하므로 개별 execute로 만든다.
        for trigger in (
            "CREATE TRIGGER IF NOT EXISTS memos_ai AFTER INSERT ON memos BEGIN "
            "INSERT INTO memos_fts(rowid, title, body) "
            "VALUES (new.id, new.title, new.body); END",
            "CREATE TRIGGER IF NOT EXISTS memos_ad AFTER DELETE ON memos BEGIN "
            "INSERT INTO memos_fts(memos_fts, rowid, title, body) "
            "VALUES ('delete', old.id, old.title, old.body); END",
            "CREATE TRIGGER IF NOT EXISTS memos_au AFTER UPDATE ON memos BEGIN "
            "INSERT INTO memos_fts(memos_fts, rowid, title, body) "
            "VALUES ('delete', old.id, old.title, old.body); "
            "INSERT INTO memos_fts(rowid, title, body) "
            "VALUES (new.id, new.title, new.body); END",
        ):
            conn.execute(trigger)
        if not fts_exists:
            # 색인을 처음 만들 때만 기존 행을 채운다. 이후 기동에서는 실행되지 않는다.
            conn.execute("INSERT INTO memos_fts(memos_fts) VALUES ('rebuild')")
        conn.execute("COMMIT")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(lifespan=lifespan)


@app.post("/memos", status_code=201)
def create_memo(memo: MemoIn) -> dict:
    with closing(get_conn()) as conn:
        cur = conn.execute(
            "INSERT INTO memos (title, body) VALUES (?, ?)", (memo.title, memo.body)
        )
        conn.commit()
        return {"id": cur.lastrowid, "title": memo.title, "body": memo.body}


def _fts_phrase(q: str) -> str:
    """검색어를 FTS5 구문 질의의 문자열 리터럴로 감싼다. 연산자로 해석되지 않는다."""
    return '"' + q.replace('"', '""') + '"'


@app.get("/memos/search")
def search_memos(
    q: str = Query(min_length=1, max_length=TITLE_MAX_LENGTH),
    sort: SortKey = "id",
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=MAX_OFFSET),
) -> list[dict]:
    # sort는 Literal로 검증된 뒤 허용 목록에서만 컬럼명을 꺼낸다. 외부 입력은 SQL에 직접 들어가지 않는다.
    order_by = SORT_COLUMNS[sort]

    if len(q) >= FTS_MIN_QUERY_LENGTH:
        where = "id IN (SELECT rowid FROM memos_fts WHERE memos_fts MATCH ?)"
        params: tuple = (_fts_phrase(q),)
    else:
        # 1~2자 질의는 색인을 쓸 수 없어 순차 조회로 처리한다. 빈 질의는 min_length로 막는다.
        where = "instr(title, ?) > 0 OR instr(body, ?) > 0"
        params = (q, q)

    with closing(get_conn()) as conn:
        rows = conn.execute(
            f"SELECT id, title, body FROM memos WHERE {where} "
            f"ORDER BY {order_by}, id LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/memos/{memo_id}")
def get_memo(memo_id: int) -> dict:
    with closing(get_conn()) as conn:
        row = conn.execute(
            "SELECT id, title, body FROM memos WHERE id = ?", (memo_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="memo not found")
    return dict(row)


@app.patch("/memos/{memo_id}")
def update_memo(memo_id: int, patch: MemoPatch) -> dict:
    changes = patch.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="no fields to update")

    # 컬럼명은 MemoPatch에 선언된 필드명에서만 나온다. 외부 입력은 값으로만 바인딩된다.
    assignments = ", ".join(f"{column} = ?" for column in changes)
    with closing(get_conn()) as conn:
        cur = conn.execute(
            f"UPDATE memos SET {assignments} WHERE id = ?",
            (*changes.values(), memo_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="memo not found")
        conn.commit()
        row = conn.execute(
            "SELECT id, title, body FROM memos WHERE id = ?", (memo_id,)
        ).fetchone()
    return dict(row)

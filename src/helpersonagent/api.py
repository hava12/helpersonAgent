import sqlite3
from contextlib import asynccontextmanager, closing

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

DB_PATH = "memos.db"


class MemoIn(BaseModel):
    title: str
    body: str


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(get_conn()) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS memos ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "title TEXT NOT NULL, "
            "body TEXT NOT NULL)"
        )
        conn.commit()


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


@app.get("/memos/{memo_id}")
def get_memo(memo_id: int) -> dict:
    with closing(get_conn()) as conn:
        row = conn.execute(
            "SELECT id, title, body FROM memos WHERE id = ?", (memo_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="memo not found")
    return dict(row)

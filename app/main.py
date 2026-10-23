"""Team-notes demo app for the Proof-Carrying Pipeline.

This app deliberately ships known weaknesses (V1-V7 in the PRD) so the in-CI
security gate has real, synthetic targets to exercise. Each weakness is gated
behind the SECURE_MODE flag:

    SECURE_MODE=0  -> vulnerable build  (the Red Team's target)
    SECURE_MODE=1  -> patched build     (what the Blue Team ships)

Running the *same* exploit against both builds is the evidence the gate uses:
it must succeed on the vulnerable build and fail on the patched one. Everything
runs against an ephemeral staging deployment with synthetic data only.
"""
from __future__ import annotations

import html
import os

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from app import db
from app.summarize import summarize_text

SECURE_MODE = os.environ.get("SECURE_MODE", "0") == "1"
APP_VERSION = os.environ.get("APP_VERSION", "dev")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Team Notes", version=APP_VERSION, lifespan=lifespan)


# --------------------------------------------------------------------------
# Auth helper (synthetic tokens only)
# --------------------------------------------------------------------------
def current_user(authorization: str | None = Header(default=None)):
    """Resolve the caller from a synthetic bearer token, or None."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1].strip()
    return db.get_user_by_token(token)


def require_admin(user=Depends(current_user)):
    # V4: in the vulnerable build the admin route skips this check entirely.
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    return user


# --------------------------------------------------------------------------
# Health & meta
# --------------------------------------------------------------------------
def _dependency_report() -> dict:
    """Report the version of the dependency that carries V6, so the security
    gate can confirm whether the vulnerable pin is actually deployed.

    In SECURE_MODE the build ships the bumped (patched) requirements, so it
    advertises the patched version; the vulnerable build advertises 0.0.6.
    The authoritative source in CI is the dependency-scanning report; this
    runtime signal is what the reachability probe confirms."""
    if SECURE_MODE:
        return {"python-multipart": os.environ.get("MULTIPART_VERSION", "0.0.18")}
    return {"python-multipart": os.environ.get("MULTIPART_VERSION", "0.0.6")}


@app.get("/healthz")
def healthz() -> dict:
    return {
        "status": "ok",
        "version": APP_VERSION,
        "secure_mode": SECURE_MODE,
        "deps": _dependency_report(),
    }


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    mode = "secure" if SECURE_MODE else "vulnerable"
    return (
        "<!doctype html><html><head><title>Team Notes</title></head>"
        f"<body><h1>Team Notes</h1><p>build: {html.escape(APP_VERSION)} "
        f"({mode})</p>"
        '<form action="/search" method="get">'
        '<input name="q" placeholder="search notes"/>'
        '<button type="submit">Search</button></form>'
        "</body></html>"
    )


# --------------------------------------------------------------------------
# V1: SQL injection in note search  /  V2: reflected XSS in the echo
# --------------------------------------------------------------------------
@app.get("/search", response_class=HTMLResponse)
def search(q: str = Query(default="")) -> str:
    with db.cursor() as cur:
        if SECURE_MODE:
            # V1 fix: parameterized query.
            cur.execute(
                "SELECT id, title, body, tags FROM notes "
                "WHERE title LIKE ? OR body LIKE ?",
                (f"%{q}%", f"%{q}%"),
            )
        else:
            # V1 (vuln): user input is string-formatted straight into SQL.
            sql = (
                "SELECT id, title, body, tags FROM notes "
                f"WHERE title LIKE '%{q}%' OR body LIKE '%{q}%'"
            )
            cur.executescript(sql) if False else cur.execute(sql)
        rows = cur.fetchall()

    # V2: the search term is echoed back into the page.
    echo = html.escape(q) if SECURE_MODE else q  # V2 fix: escape output.
    items = "".join(
        f"<li>#{r['id']} <b>{html.escape(r['title'])}</b>: "
        f"{html.escape(r['body'])} "
        f"<span class='tags'>[{html.escape(r['tags'])}]</span></li>"
        for r in rows
    )
    return (
        f"<!doctype html><html><body><h2>Results for: {echo}</h2>"
        f"<ul>{items}</ul></body></html>"
    )


# --------------------------------------------------------------------------
# V3: IDOR on GET /notes/{id}
# --------------------------------------------------------------------------
@app.get("/notes/{note_id}")
def get_note(note_id: int, user=Depends(current_user)) -> JSONResponse:
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, owner_id, title, body, tags FROM notes WHERE id = ?",
            (note_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")

    if SECURE_MODE:
        # V3 fix: a caller may only read their own notes.
        if user is None:
            raise HTTPException(status_code=401, detail="authentication required")
        if row["owner_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="not your note")

    return JSONResponse(dict(row))


# --------------------------------------------------------------------------
# V4: admin route with no auth
# --------------------------------------------------------------------------
if SECURE_MODE:

    @app.get("/admin/users")
    def admin_users(_admin=Depends(require_admin)) -> JSONResponse:
        # V4 fix: require_admin dependency enforces the admin role.
        with db.cursor() as cur:
            cur.execute("SELECT id, username, role FROM users")
            rows = [dict(r) for r in cur.fetchall()]
        return JSONResponse({"users": rows})

else:

    @app.get("/admin/users")
    def admin_users_vuln() -> JSONResponse:
        # V4 (vuln): no auth; anyone can enumerate users and their tokens.
        with db.cursor() as cur:
            cur.execute("SELECT id, username, role, api_token FROM users")
            rows = [dict(r) for r in cur.fetchall()]
        return JSONResponse({"users": rows})


# --------------------------------------------------------------------------
# V5: stack trace leaked on 500
# --------------------------------------------------------------------------
@app.get("/boom")
def boom() -> dict:
    # A route that always errors, to exercise the 500 handler.
    raise RuntimeError(
        "internal failure in notes_service at db.py:142 "
        "(connection string postgres://demo:demo@10.0.0.5/notes)"
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    if SECURE_MODE:
        # V5 fix: generic error, no internals leaked.
        return JSONResponse(status_code=500, content={"detail": "internal server error"})
    # V5 (vuln): leak the exception text (and anything in it) to the client.
    return PlainTextResponse(status_code=500, content=f"Traceback: {exc}")


# --------------------------------------------------------------------------
# V7: prompt injection in /summarize
# --------------------------------------------------------------------------
@app.post("/summarize")
async def summarize(request: Request) -> JSONResponse:
    payload = await request.json()
    text = str(payload.get("text", ""))
    result = summarize_text(text, secure=SECURE_MODE)
    return JSONResponse({"summary": result})

"""Team-notes demo app for Polygraph.

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
from importlib import metadata
from urllib.parse import quote

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from app import db
from app.summarize import summarize_text

SECURE_MODE = os.environ.get("SECURE_MODE", "0") == "1"
APP_VERSION = os.environ.get("APP_VERSION", "dev")
# Weaknesses the build ships WITHOUT their fix even in secure mode (demo of an
# incomplete Blue Team patch). Baked into the image and its labels at build
# time; see the Dockerfile.
UNPATCHED = {v.strip().upper() for v in os.environ.get("PCP_UNPATCHED", "").split(",") if v.strip()}


def hardened(vuln_id: str) -> bool:
    """Is the fix for this weakness (V1..V7) in this build?"""
    return SECURE_MODE and vuln_id not in UNPATCHED


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
    """Report the INSTALLED version of the dependency that carries V6, read
    from package metadata. The image decides it: the pre-patch twin installs
    the vulnerable pin (requirements-vuln.txt), the shipped build does not.
    Nothing at runtime can make this report a version that isn't installed."""
    try:
        version = metadata.version("python-multipart")
    except metadata.PackageNotFoundError:
        version = "not-installed"
    return {"python-multipart": version}


# /health is what probes use: Cloud Run reserves paths ending in "z" (its
# front end answers /healthz itself with a 404). /healthz stays for local use.
@app.get("/health")
@app.get("/healthz")
def healthz() -> dict:
    return {
        "status": "ok",
        "version": APP_VERSION,
        "secure_mode": SECURE_MODE,
        "unpatched": sorted(UNPATCHED),
        "deps": _dependency_report(),
    }


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    mode = "secure" if SECURE_MODE else "pre-patch twin"
    tag_links = " ".join(
        f'<a href="/search?tag={quote(t)}">{html.escape(t)}</a>'
        for t in db.list_tags()
    )
    return (
        "<!doctype html><html><head><title>Team Notes</title></head>"
        f"<body><h1>Team Notes</h1><p>build: {html.escape(APP_VERSION)} "
        f"({mode})</p>"
        '<form action="/search" method="get">'
        '<input name="q" placeholder="search notes"/>'
        '<button type="submit">Search</button></form>'
        f"<p class='tag-picker'>Filter by tag: {tag_links}</p>"
        "</body></html>"
    )


# --------------------------------------------------------------------------
# V1: SQL injection in note search  /  V2: reflected XSS in the echo
# --------------------------------------------------------------------------
@app.get("/search", response_class=HTMLResponse)
def search(q: str = Query(default=""), tag: str = Query(default="")) -> str:
    with db.cursor() as cur:
        if hardened("V1"):
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
            cur.execute(sql)
        rows = cur.fetchall()

    # Tag filter (applied after the query, so it adds no SQL surface).
    rows = db.filter_by_tag(rows, tag)
    active_tag = db.normalize_tag(tag)
    tag_note = (
        f" <small>(tag: {html.escape(active_tag)}) "
        f"<a href='/search?q={quote(q)}'>clear filter</a></small>"
        if active_tag
        else ""
    )

    # V2: the search term is echoed back into the page.
    echo = html.escape(q) if hardened("V2") else q  # V2 fix: escape output.
    items = "".join(
        f"<li>#{r['id']} <b>{html.escape(r['title'])}</b>: "
        f"{html.escape(r['body'])} "
        f"<span class='tags'>[{html.escape(r['tags'])}]</span></li>"
        for r in rows
    )
    return (
        f"<!doctype html><html><body><h2>Results for: {echo}{tag_note}</h2>"
        f"<ul>{items}</ul></body></html>"
    )


# --------------------------------------------------------------------------
# Note listing with optional tag filter:  GET /notes?tag=work
# Returns the caller's own notes (auth required); `tag` narrows them to notes
# carrying that tag. Matching is exact and case-insensitive; a blank tag means
# no filter; an unknown tag yields an empty list.
# --------------------------------------------------------------------------
@app.get("/notes")
def list_notes(tag: str = Query(default=""), user=Depends(current_user)) -> JSONResponse:
    if user is None:
        raise HTTPException(status_code=401, detail="authentication required")
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, owner_id, title, body, tags FROM notes "
            "WHERE owner_id = ? ORDER BY id",
            (user["id"],),
        )
        rows = cur.fetchall()
    rows = db.filter_by_tag(rows, tag)
    return JSONResponse({"notes": [dict(r) for r in rows]})


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

    if hardened("V3"):
        # V3 fix: a caller may only read their own notes.
        if user is None:
            raise HTTPException(status_code=401, detail="authentication required")
        if row["owner_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="not your note")

    return JSONResponse(dict(row))


# --------------------------------------------------------------------------
# V4: admin route with no auth
# --------------------------------------------------------------------------
if hardened("V4"):

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
    if hardened("V5"):
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
    result = summarize_text(text, secure=hardened("V7"))
    return JSONResponse({"summary": result})

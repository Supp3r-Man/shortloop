"""shortloop: URL shortener with click analytics. FastAPI + SQLite, local only."""
import hashlib
import hmac
import html
import ipaddress
import os
import re
import secrets
import socket
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import uvicorn
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from qr import qr_svg

ROOT = Path(__file__).parent
DB = ROOT / "shortloop.db"
GEO = ROOT / "GeoLite2-Country.mmdb"
PORT = 8000
BASE = f"http://localhost:{PORT}"
ORIGINS = [BASE, f"http://127.0.0.1:{PORT}"]
OWN_HOST = os.environ.get("SHORTLOOP_HOST", "localhost")
RESERVED = {"api", "static"}
SLUG_RE = r"^[A-Za-z0-9_-]{3,32}$"
ALPHA = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, pw_hash TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  csrf TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS links(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  slug TEXT NOT NULL UNIQUE, url TEXT NOT NULL, expires_at TEXT, max_clicks INTEGER,
  click_count INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS clicks(
  id INTEGER PRIMARY KEY, link_id INTEGER NOT NULL REFERENCES links(id) ON DELETE CASCADE,
  ts TEXT NOT NULL, referrer TEXT NOT NULL, device TEXT NOT NULL, country TEXT, ip_hash TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS clicks_link_ts ON clicks(link_id, ts);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


@contextmanager
def conn():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db():
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        c.execute("INSERT OR IGNORE INTO meta VALUES('salt', ?)", (secrets.token_hex(16),))
        return c.execute("SELECT v FROM meta WHERE k='salt'").fetchone()[0]


SALT = init_db()
ph = PasswordHasher()
DUMMY = ph.hash("not-a-real-password")  # burned on unknown emails so login timing doesn't leak accounts
BLOCKLIST = [
    re.compile(p, re.I)
    for p in (ROOT / "blocklist.txt").read_text().splitlines()
    if p.strip() and not p.startswith("#")
] if (ROOT / "blocklist.txt").exists() else []


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def ip_of(request):
    return request.client.host if request.client else "?"


def ip_hash(ip, day=None):
    # salted and day-scoped: counts uniques per day, can't follow anyone across days
    return sha(f"{SALT}{day or now()[:10]}{ip}")[:16]


HITS = {}


def limit(key, n, per):
    t = time.monotonic()
    q = [x for x in HITS.get(key, []) if t - x < per]
    if len(q) >= n:
        HITS[key] = q
        raise HTTPException(429, "Too many requests, slow down")
    q.append(t)
    HITS[key] = q


def bad(msg):
    raise HTTPException(422, msg)


def base62(n):
    s = ""
    while n:
        n, r = divmod(n, 62)
        s = ALPHA[r] + s
    return s or "0"


def public(ip):
    ip = getattr(ip, "ipv4_mapped", None) or ip
    return ip.is_global and not ip.is_multicast


def check_url(raw):
    raw = raw.strip()
    if re.search(r"[\s\x00-\x1f]", raw):
        bad("URL contains whitespace or control characters")
    try:
        u = urlparse(raw)
        u.port
    except ValueError:
        bad("Malformed URL")
    if u.scheme not in ("http", "https") or not u.hostname:
        bad("Only http(s) URLs with a host are allowed")
    if u.username or u.password:
        bad("Credentials in URLs are not allowed")
    host = u.hostname.lower().rstrip(".")
    if host == OWN_HOST or host.endswith("." + OWN_HOST):
        bad("That is this site's own domain")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        bad("Private hosts are not allowed")
    try:
        ips = [ipaddress.ip_address(i[4][0].split("%")[0]) for i in socket.getaddrinfo(host, None)]
    except (socket.gaierror, UnicodeError):
        bad("Could not resolve that host")
    if not all(public(i) for i in ips):
        bad("Host resolves to a private or loopback address")
    if any(p.search(raw) for p in BLOCKLIST):
        bad("Destination matches the blocklist")
    return raw


def parse_expiry(s):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        bad("Bad expiry date")
    if d.tzinfo:
        d = d.astimezone(timezone.utc).replace(tzinfo=None)
    d = d.isoformat(timespec="seconds")
    if d <= now():
        bad("Expiry must be in the future")
    return d


def device(ua):
    ua = (ua or "").lower()
    if re.search(r"bot|crawl|spider|slurp|preview|curl|wget|python-requests", ua):
        return "bot"
    if re.search(r"ipad|tablet|android(?!.*mobile)", ua):
        return "tablet"
    if re.search(r"mobi|iphone|android", ua):
        return "mobile"
    return "desktop"


def ref_host(r):
    try:
        return (urlparse(r).hostname or "direct")[:100]
    except ValueError:
        return "direct"


_geo = None


def country(ip):
    global _geo
    if _geo is None:
        try:
            import maxminddb
            _geo = maxminddb.open_database(str(GEO)) if GEO.exists() else False
        except ImportError:
            _geo = False
    if not _geo:
        return None
    try:
        return ((_geo.get(ip) or {}).get("country") or {}).get("iso_code")
    except ValueError:
        return None


class Cred(BaseModel):
    email: str = Field(max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=10, max_length=128)


class NewLink(BaseModel):
    url: str = Field(max_length=2048)
    slug: str | None = Field(None, pattern=SLUG_RE)
    expires_at: str | None = Field(None, max_length=40)
    max_clicks: int | None = Field(None, ge=1, le=1_000_000)


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)


@app.middleware("http")
async def guard(request: Request, call_next):
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        o = request.headers.get("origin")
        if o and o not in ORIGINS:
            return JSONResponse({"detail": "Bad origin"}, 403)
    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    )
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


def current(request: Request):
    with conn() as c:
        row = c.execute(
            "SELECT u.id, u.email, s.csrf FROM sessions s JOIN users u ON u.id=s.user_id "
            "WHERE s.token_hash=? AND s.expires_at>?",
            (sha(request.cookies.get("sid", "")), now()),
        ).fetchone()
    if not row:
        raise HTTPException(401, "Not logged in")
    if request.method not in ("GET", "HEAD"):
        sent = request.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(sent.encode(), row["csrf"].encode()):
            raise HTTPException(403, "Bad CSRF token")
    return row


def start_session(c, uid, resp):
    tok, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    exp = (datetime.now(timezone.utc) + timedelta(days=7)).replace(tzinfo=None).isoformat(timespec="seconds")
    c.execute("DELETE FROM sessions WHERE expires_at<?", (now(),))
    c.execute("INSERT INTO sessions VALUES(?,?,?,?)", (sha(tok), uid, csrf, exp))
    resp.set_cookie("sid", tok, max_age=7 * 86400, httponly=True, samesite="lax", path="/")
    return csrf


@app.post("/api/register", status_code=201)
def register(body: Cred, request: Request, resp: Response):
    limit(f"auth:{ip_of(request)}", 10, 60)
    email = body.email.lower()
    try:
        with conn() as c:
            uid = c.execute(
                "INSERT INTO users(email, pw_hash, created_at) VALUES(?,?,?)",
                (email, ph.hash(body.password), now()),
            ).lastrowid
            return {"email": email, "csrf": start_session(c, uid, resp)}
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Email already registered")


@app.post("/api/login")
def login(body: Cred, request: Request, resp: Response):
    email = body.email.lower()
    limit(f"auth:{ip_of(request)}", 10, 60)
    limit(f"login:{email}", 5, 60)
    with conn() as c:
        row = c.execute("SELECT id, pw_hash FROM users WHERE email=?", (email,)).fetchone()
        try:
            ok = ph.verify(row["pw_hash"] if row else DUMMY, body.password) and row is not None
        except (VerificationError, InvalidHashError):
            ok = False
        if not ok:
            raise HTTPException(401, "Wrong email or password")
        return {"email": email, "csrf": start_session(c, row["id"], resp)}


@app.post("/api/logout", status_code=204)
def logout(request: Request, u=Depends(current)):
    with conn() as c:
        c.execute("DELETE FROM sessions WHERE token_hash=?", (sha(request.cookies["sid"]),))
    r = Response(status_code=204)
    r.delete_cookie("sid", path="/")
    return r


@app.get("/api/me")
def me(u=Depends(current)):
    return {"email": u["email"], "csrf": u["csrf"]}


@app.get("/api/links")
def list_links(u=Depends(current)):
    with conn() as c:
        rows = c.execute(
            "SELECT id, slug, url, expires_at, max_clicks, click_count, created_at "
            "FROM links WHERE user_id=? ORDER BY id DESC",
            (u["id"],),
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/links", status_code=201)
def create_link(body: NewLink, u=Depends(current)):
    limit(f"mk:{u['id']}", 30, 60)
    if body.slug in RESERVED:
        bad("That slug is reserved")
    url = check_url(body.url)
    exp = parse_expiry(body.expires_at)
    with conn() as c:
        for _ in range(1 if body.slug else 8):
            slug = body.slug or base62(secrets.randbelow(62**7)).rjust(7, "0")
            try:
                c.execute(
                    "INSERT INTO links(user_id, slug, url, expires_at, max_clicks, created_at) VALUES(?,?,?,?,?,?)",
                    (u["id"], slug, url, exp, body.max_clicks, now()),
                )
                return {"slug": slug}
            except sqlite3.IntegrityError:
                if body.slug:
                    raise HTTPException(409, "That slug is taken")
        raise HTTPException(500, "Could not find a free slug, try again")


def owned(c, lid, u):
    row = c.execute("SELECT * FROM links WHERE id=? AND user_id=?", (lid, u["id"])).fetchone()
    if not row:
        raise HTTPException(404, "No such link")
    return row


@app.delete("/api/links/{lid}", status_code=204)
def delete_link(lid: int, u=Depends(current)):
    with conn() as c:
        owned(c, lid, u)
        c.execute("DELETE FROM links WHERE id=?", (lid,))
    return Response(status_code=204)


TOP = {
    col: f"SELECT {col} AS name, COUNT(*) AS n FROM clicks WHERE link_id=? AND ts>=? AND {col} IS NOT NULL "
         f"GROUP BY {col} ORDER BY n DESC LIMIT 5"
    for col in ("referrer", "device", "country")
}


@app.get("/api/links/{lid}/stats")
def stats(lid: int, days: int = 30, u=Depends(current)):
    days = max(1, min(days, 90))
    start = (datetime.now(timezone.utc) - timedelta(days=days - 1)).replace(
        tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    since = start.isoformat(timespec="seconds")
    with conn() as c:
        owned(c, lid, u)
        per_day = {
            r["d"]: r["n"]
            for r in c.execute(
                "SELECT substr(ts,1,10) AS d, COUNT(*) AS n FROM clicks WHERE link_id=? AND ts>=? GROUP BY d",
                (lid, since))
        }
        visitors = c.execute(
            "SELECT COUNT(DISTINCT ip_hash) FROM clicks WHERE link_id=? AND ts>=?", (lid, since)).fetchone()[0]
        top = {col: [dict(r) for r in c.execute(sql, (lid, since))] for col, sql in TOP.items()}
    daily = []
    for i in range(days):
        d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        daily.append({"day": d, "n": per_day.get(d, 0)})
    return {
        "days": days, "total": sum(d["n"] for d in daily), "visitors": visitors, "daily": daily,
        "referrers": top["referrer"], "devices": top["device"], "countries": top["country"],
    }


@app.get("/api/links/{lid}/qr")
def qr(lid: int, u=Depends(current)):
    with conn() as c:
        row = owned(c, lid, u)
    return Response(qr_svg(f"{BASE}/{row['slug']}"), media_type="image/svg+xml",
                    headers={"Cache-Control": "private, max-age=3600"})


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


def page(status, title, body):
    t = html.escape(title)
    return HTMLResponse(
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{t} - shortloop</title><link rel="icon" href="data:,">'
        f'<link rel="stylesheet" href="/static/style.css"><main class="card narrow"><h1>{t}</h1>{body}</main>',
        status,
    )


def find(slug):
    with conn() as c:
        return c.execute("SELECT * FROM links WHERE slug=?", (slug,)).fetchone()


def dead_reason(link):
    if link["expires_at"] and link["expires_at"] <= now():
        return "This link has expired."
    if link["max_clicks"] is not None and link["click_count"] >= link["max_clicks"]:
        return "This link has reached its click limit."


# Registered before /{slug}, which would otherwise swallow "abc+".
@app.get("/{slug}+")
def preview(slug: str):
    link = find(slug)
    if not link:
        return page(404, "No such link", "<p>Nothing lives here.</p>")
    reason = dead_reason(link)
    if reason:
        return page(410, "Link unavailable", f"<p>{reason}</p>")
    host = html.escape(urlparse(link["url"]).hostname or "")
    return page(200, "Where does this go?", (
        f'<p>Destination site: <strong>{host}</strong></p>'
        f'<p class="url">{html.escape(link["url"])}</p>'
        f'<p><a class="btn" href="/{html.escape(link["slug"])}" rel="nofollow">Continue</a></p>'))


@app.get("/{slug}")
def go(slug: str, request: Request):
    ip = ip_of(request)
    limit(f"go:{ip}", 60, 60)  # stops one visitor burning a click-limited link
    link = find(slug)
    if not link:
        return page(404, "No such link", "<p>Nothing lives here.</p>")
    reason = dead_reason(link)
    with conn() as c:
        ok = not reason and c.execute(
            "UPDATE links SET click_count=click_count+1 "
            "WHERE id=? AND (max_clicks IS NULL OR click_count<max_clicks)", (link["id"],)).rowcount
        if ok:
            c.execute(
                "INSERT INTO clicks(link_id, ts, referrer, device, country, ip_hash) VALUES(?,?,?,?,?,?)",
                (link["id"], now(), ref_host(request.headers.get("referer", "")),
                 device(request.headers.get("user-agent")), country(ip), ip_hash(ip)),
            )
    if not ok:
        return page(410, "Link unavailable", f"<p>{reason or 'This link has reached its click limit.'}</p>")
    return RedirectResponse(link["url"], 302, headers={"Cache-Control": "no-store"})


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT)

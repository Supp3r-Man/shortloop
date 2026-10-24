# Architecture

```
  Owner's browser (HTML + vanilla JS)            Visitor's browser
     |  fetch /api/*                                   |  GET /<slug>   or  /<slug>+
     |  cookie sid (httpOnly) + X-CSRF-Token           |
     v                                                 v
 +------------------------------------------------------------------+
 | FastAPI  app.py                                                  |
 |  middleware: Origin check -> CORS (localhost) -> CSP/security hdrs|
 |                                                                  |
 |  /api/*  register login logout me links stats qr   (session+CSRF)|
 |  /{slug}+  preview page        (no click logged)                 |
 |  /{slug}   302 + click log     (rate limited per IP)             |
 |                                                                  |
 |  check_url(): scheme, userinfo, own host, DNS -> public IPs only,|
 |               blocklist.txt        qr.py: pure-Python QR -> SVG  |
 +-----------------------------+------------------------------------+
                               |
                         SQLite shortloop.db
          users | sessions | links | clicks | meta(salt)
                     optional: GeoLite2-Country.mmdb
```

## API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | /api/register | none, rate limited | Create account, start session |
| POST | /api/login | none, rate limited | Start session, returns CSRF token |
| POST | /api/logout | session + CSRF | End session |
| GET | /api/me | session | Current user + CSRF token |
| GET | /api/links | session | List own links with click counts |
| POST | /api/links | session + CSRF | Create link (`url`, optional `slug`, `expires_at`, `max_clicks`) |
| DELETE | /api/links/{id} | session + CSRF | Delete own link and its clicks |
| GET | /api/links/{id}/stats | session | Daily clicks, top referrers, devices, countries, uniques |
| GET | /api/links/{id}/qr | session | QR code SVG for the short URL |
| GET | /{slug}+ | none | Preview destination, no click logged |
| GET | /{slug} | none | 302 to destination, logs click (410 if expired or limit hit) |

## Database

```sql
users(id PK, email UNIQUE, pw_hash, created_at)
sessions(token_hash PK, user_id FK, csrf, expires_at)          -- token stored hashed
links(id PK, user_id FK, slug UNIQUE, url, expires_at NULL, max_clicks NULL,
      click_count DEFAULT 0, created_at)
clicks(id PK, link_id FK, ts, referrer, device, country NULL, ip_hash)
        INDEX clicks_link_ts(link_id, ts)
meta(k PK, v)                                                  -- holds the IP-hash salt
```

Timestamps are naive UTC ISO strings, so string comparison is time comparison.

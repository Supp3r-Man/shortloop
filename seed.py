"""Seed a demo user, a few links and 30 days of fake clicks. Run once: python seed.py"""
import random
import secrets
from datetime import datetime, timedelta, timezone

from app import base62, conn, ip_hash, now, ph

EMAIL, PASSWORD = "demo@example.com", "demo-password-123"
LINKS = [
    ("docs", "https://example.com/docs/getting-started"),
    ("launch", "https://example.org/blog/launch-day"),
    ("menu", "https://example.net/lunch-menu"),
    ("talk", "https://example.com/conf/2026/keynote"),
    (None, "https://example.org/a/very/long/path?with=query&and=more"),
]
REFS = ["direct", "news.ycombinator.com", "twitter.com", "reddit.com", "linkedin.com", "google.com"]
DEVICES = ["desktop", "mobile", "tablet", "bot"]
COUNTRIES = [None, "US", "DE", "IN", "BR", "JP"]

random.seed(7)
today = datetime.now(timezone.utc).replace(tzinfo=None)

with conn() as c:
    if c.execute("SELECT 1 FROM users WHERE email=?", (EMAIL,)).fetchone():
        raise SystemExit("Already seeded.")
    uid = c.execute(
        "INSERT INTO users(email, pw_hash, created_at) VALUES(?,?,?)", (EMAIL, ph.hash(PASSWORD), now())
    ).lastrowid
    for slug, url in LINKS:
        slug = slug or base62(secrets.randbelow(62**7)).rjust(7, "0")
        lid = c.execute(
            "INSERT INTO links(user_id, slug, url, created_at) VALUES(?,?,?,?)", (uid, slug, url, now())
        ).lastrowid
        weight, rows = random.uniform(0.3, 2.0), []
        for day in range(30):
            midnight = (today - timedelta(days=29 - day)).replace(hour=0, minute=0, second=0, microsecond=0)
            for _ in range(max(0, int(random.gauss(10, 5) * weight * (0.5 + day / 30)))):
                ts = midnight + timedelta(seconds=random.randrange(86400))
                if ts > today:
                    continue
                stamp = ts.isoformat(timespec="seconds")
                rows.append((
                    lid, stamp,
                    random.choices(REFS, [5, 3, 2, 2, 1, 4])[0],
                    random.choices(DEVICES, [50, 38, 7, 5])[0],
                    random.choice(COUNTRIES),
                    ip_hash(f"10.0.0.{random.randrange(300)}", stamp[:10]),
                ))
        c.executemany(
            "INSERT INTO clicks(link_id, ts, referrer, device, country, ip_hash) VALUES(?,?,?,?,?,?)", rows)
        c.execute("UPDATE links SET click_count=? WHERE id=?", (len(rows), lid))
print(f"Seeded. Log in as {EMAIL} / {PASSWORD}")

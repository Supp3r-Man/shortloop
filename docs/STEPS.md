# Steps

1. **Schema first.** Five tables in one `SCHEMA` string, created on import. A random salt is generated once and kept in `meta`.
2. **Auth.** argon2 hashes, random session tokens stored only as SHA-256, httpOnly SameSite=Lax cookie, per-session CSRF token checked on every non-GET, Origin check for the pre-login POSTs, in-memory rate limit on register/login.
3. **URL validation.** Scheme allow-list, no userinfo, block own host and `localhost`-ish names, resolve DNS and require every address to be public, then run `blocklist.txt`.
4. **Link creation.** Custom slug (regex + reserved words) or a random number below 62^7 encoded as base62. The UNIQUE index is the collision check, retried a few times.
5. **Redirect.** One atomic `UPDATE ... WHERE click_count < max_clicks` enforces the limit, then the click row is inserted and a 302 is returned with `no-store`.
6. **Click record.** Referrer reduced to its hostname, device from a few UA regexes, country only if a GeoIP file and `maxminddb` exist, IP stored as a daily salted hash.
7. **Preview.** `/{slug}+` shows destination host and full URL, registered before `/{slug}`.
8. **Stats + QR.** SQL aggregates for daily/referrer/device/country; `qr.py` encodes the short URL to an SVG.
9. **Frontend.** Three static files, DOM built with `textContent`, SVG chart via `createElementNS`, `<progress>` bars so the strict CSP needs no inline styles.
10. **Seed.** `seed.py` adds a demo user, five links and 30 days of fake clicks.

## Interview Q&A

**1. Base62 of a random ID vs hashing the URL?**
A hash of the URL makes the same URL map to the same slug (handy for dedupe) but forces a truncation-collision plan, leaks nothing useful, and can't give two users separate stats for the same URL. A random ID encoded as base62 gives short, URL-safe, unguessable slugs; 62^7 is about 3.5 trillion. Collisions are handled by the UNIQUE index plus retry. A sequential counter would be shorter but enumerable.

**2. 301 or 302?**
302. A 301 is cacheable forever, so browsers would skip us on repeat visits, which breaks click counts, expiry, click limits and the ability to edit or delete a link. The cost is one extra round trip per click.

**3. What is an open redirect and how is it handled here?**
A trusted domain that redirects to attacker-chosen URLs lends its reputation to phishing. A shortener is a redirector by design, so the mitigation is to constrain what it will point at: http/https only, no `javascript:` or `data:`, no credentials in the URL, a local blocklist, no chaining through other shorteners, no pointing at itself, and a preview page that shows the destination host.

**4. What is the SSRF angle if the server never fetches the URL?**
Nothing here fetches it, but the same private-address checks stop the link from being used to aim users (or a future link-preview fetcher) at `127.0.0.1`, `10.x`, `169.254.x` and similar. DNS is resolved at creation and every address must be public, and IPv4-mapped IPv6 is unwrapped. Honest limit: DNS can change after creation (rebinding), so a real deployment would re-check at redirect time or in the fetcher.

**5. What makes the analytics privacy-friendly?**
Store less. IPs are kept only as `sha256(salt + day + ip)` truncated, so uniques per day work but a visitor can't be followed across days and the raw IP is never written. Referrers are reduced to hostnames, there are no cookies for visitors, and country is looked up from the IP in memory and only the code is saved. A hash of an IPv4 address is still guessable by brute force if the salt leaks, which is why the salt lives only in the DB.

**6. How does rate limiting work and where does it fall short?**
A sliding window per key (IP for auth and redirects, user for link creation, email for login) held in a dict. It's enough to slow credential stuffing and to stop one visitor from burning a click-limited link. It resets on restart, isn't shared across processes, and keys on the socket address, so behind a proxy it would need a trusted forwarded-IP setup or a shared store like Redis.

**7. How would you cache hot redirects?**
Cache slug to destination in memory with a short TTL, since the mapping rarely changes, and invalidate on delete. The catch is that every click still needs a write (count, click row, limit check), so at scale you'd batch click writes through a queue and enforce limits with an atomic counter. Expiry and limits must be checked against the cached record's timestamps, not just its presence.

**8. Why cookie sessions with CSRF tokens instead of JWTs in localStorage?**
An httpOnly cookie can't be read by injected JavaScript, and the server can revoke a session by deleting a row. Cookies are sent automatically, so CSRF becomes the risk: SameSite=Lax, an Origin allow-list, and a per-session token in a custom header that a cross-site page cannot set. A JWT in localStorage avoids CSRF but hands the token to any XSS.

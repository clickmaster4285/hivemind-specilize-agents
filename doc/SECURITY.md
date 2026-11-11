# Sign-in and account protection — how it works

What protects the login, the accounts and the API: what each part does, when you see it, and why it is
there. Part 1 is the plain-language picture; Part 2 is the technical reference (routes, tables, files);
Part 3 lists the numbers and where to change them; Part 4 the known limits and what is still to build.

Plans and status: [BACKLOG.md](BACKLOG.md) L16 (S1–S7). Problems found on the way: [ISSUES_LOG.md](ISSUES_LOG.md)
O16, O36, O38, O39. The device-login vault (AES) is in [SERVER_ACCESS_DECISION.md](SERVER_ACCESS_DECISION.md).

---

## Part 1 — In plain words

### 1.1 Who can reach the app

- The app runs on one PC. The **UI** (port 3000) is open to the LAN: `http://192.168.88.36:3000`.
- The **API** (port 8787) listens on 127.0.0.1 only. Browsers never talk to it directly; the UI passes
  every `/api/...` call on to it, so port 8787 is closed to the LAN.
- **People outside the LAN cannot open it.** 192.168.x.x is a private address that the internet cannot
  route to, and the router (NAT) forwards nothing to this PC. That changes only if someone adds a port
  forward or a tunnel on the router. Don't, until HTTPS exists (Part 4).
- The LAN uses **plain http**. That was your decision (S1b): someone sniffing the LAN could read the traffic.
  The cookies get `Secure` by themselves once HTTPS is added.

### 1.2 Sessions: staying signed in (S1)

- After sign-in the browser gets two cookies that page scripts cannot read (`HttpOnly`):
  - a **15-minute access token**;
  - a **7-day refresh token** that renews it quietly.
- **Logout** ends the session at once. So does **disabling a user**, which takes effect on their very next request.
- If a stolen refresh token is used after the real one, the whole sign-in is cancelled.

### 1.3 Abuse limits (S2)

| What | Limit | What the user sees |
|---|---|---|
| Wrong passwords, one account | 5 free; then a wait of 1 s, 2 s, 4 s … up to 30 s; **10 in 15 min → account locked 15 min** | "Too many attempts — try again in N min" |
| Wrong passwords, one PC | **20 in 15 min → that PC blocked 15 min** (one PC trying many usernames) | same |
| Unknown usernames | counted and timed exactly like real ones | nothing reveals which usernames exist |
| Requests, signed in | 120 per minute per user (bursts of 60 allowed; a page load is ~10–20 calls) | 429 "too many requests" |
| Requests, signed out | 60 per minute per PC | same |
| Size of one request | 30 MB (the largest real upload is a 25 MB file plus form overhead) | 413 "too large" |

One PC = one person (your assumption), so "per PC" means per IP address.

### 1.4 The CAPTCHA (S2b)

**The problem it solves.** The S2 lock has a weak spot: *anyone* can lock *anyone* out. If you know a
colleague's username and type 10 wrong passwords, they are locked out for 15 minutes. A script could do that
to everybody, over and over. The CAPTCHA closes the gap. From the 3rd wrong password, every further try needs
a **person** to read a picture. A script cannot keep going, so it never gets to 10.

It also keeps **sign-up** clean: without it a script could fill "Waiting for approval" with fake accounts.

**When it appears**

| Where | When |
|---|---|
| Sign-in | only after **3 wrong passwords** (for that account or from that PC, within 15 min), before the S2 wait starts at 5; a typo or two never shows it |
| Sign-up | **always** |

After a successful sign-in, or an admin's unlock, the count goes back to zero and the CAPTCHA goes away.

**How it is made** (inside our own API, no outside service):

1. The browser asks the API for a picture.
2. The API picks **6 random characters**. It uses no look-alikes: no `0/O`, `1/I/l`, `2/Z`, `5/S` or `8/B`.
3. It draws them with **Pillow**. Each character gets a random tilt, height and dark colour, with lines and
   dots drawn over them. A person reads it easily; a simple program does not.
4. The browser gets the **picture and an ID**. The answer stays on the server, kept in memory for **5 minutes**.
5. The user types the characters. Upper or lower case doesn't matter, and spaces at the ends are ignored.
6. The API checks the answer **once**. Right or wrong, that picture is used up, so each try shows a new one.
   The **New image** button gets a fresh picture if one is hard to read.

```
Browser                              API
  │  "give me a CAPTCHA"   ───►      makes "KX7MPA", draws the picture,
  │                                  remembers  id-123 → KX7MPA  (5 min)
  │  ◄───  picture + id-123
  │
  │  sign-in + id-123 + "kx7mpa" ─►  compares → OK, and deletes id-123 (one use)
```

**What each try does once a CAPTCHA is needed**

| The sign-in sends … | Answer | Counts as a wrong password? |
|---|---|---|
| no CAPTCHA | "Type the characters in the picture" | **no**: the password is not even checked |
| a wrong CAPTCHA | "The characters did not match — type the ones in the new picture" | **no** |
| right CAPTCHA, wrong password | "Invalid username/email or password" | yes |
| right CAPTCHA, right password | signed in | count reset |

The first two rows are the point: **only a person who solves the picture can add a failure**.

**Why this kind of CAPTCHA**

| Option | Why not / why |
|---|---|
| Google reCAPTCHA, hCaptcha, Cloudflare Turnstile | outside services: need internet, send visitors' data out |
| Proof-of-work (Altcha, mCaptcha, Friendly Captcha) | needs the browser's `crypto.subtle`, which exists only on https or localhost pages, not on our plain-http LAN address; it slows a computer down but does not prove a person |
| **Text picture drawn by our API** ✔ | works on plain http, nothing new to install, proves a person |

### 1.5 Admin unlock (S2b)

You don't have to wait the 15 minutes.

- **Settings → Users**:
  - A locked account shows a red **locked** badge. Open it and click **Unlock now**.
  - **Blocked computers** lists the blocked PCs, each with **Unlock**.
- Each unlock is written to `logs/api.log` with the admin's name.

### 1.6 Self sign-up (S7)

- **"Create account"** on the sign-in page asks for:
  - username (≥ 3 characters);
  - email (valid and not already in use);
  - phone (optional);
  - password (≥ 8 characters, typed twice);
  - the CAPTCHA.
- **Two ways in:**
  - **Open sign-up:** anyone on the LAN can use it. An admin can switch it off in Settings → Users.
  - **Invite links:** an admin makes a link. Each link works once, lasts 7 days, and can carry an email and the modules to give. It still works when open sign-up is off.
- **Every new account waits for an admin.** That includes invited ones. Until approved, sign-in says
  "Your account is waiting for an admin's approval". Approve makes the account active; Reject deletes it.
- At most **5 sign-ups per PC per day**.
- **Google / Microsoft sign-in** (OAuth 2.0 / OpenID Connect) comes **later**, once there is HTTPS: their
  sign-in only returns to https:// or localhost addresses. Clerk was not used, because accounts would live on
  an outside service and need internet.

---

## Part 2 — Technical reference

### 2.1 Request path and the client address

```
LAN browser ─► UI :3000 (frontend/server.mjs → Next.js) ──rewrite /api──► API 127.0.0.1:8787
```

- **The real client address.** Every request reaches the API from 127.0.0.1, so the API reads the client from
  `X-Forwarded-For`.
  - It trusts that header only when the direct peer is 127.0.0.1 or ::1 (`TRUSTED_PROXIES`).
  - It takes the last entry and maps `::ffff:a.b.c.d` to `a.b.c.d`.
- **Why the custom server.** Next's rewrite adds no `X-Forwarded-For` and passes a forged one through (checked
  against its proxy code). So the UI runs `frontend/server.mjs` (`npm run dev` → `node server.mjs`), which
  **overwrites** the header with the socket address before Next sees the request.
- **Middleware is plain ASGI, not `BaseHTTPMiddleware`.** `BaseHTTPMiddleware` wraps errors raised while
  reading the body in an `ExceptionGroup`, and FastAPI then answers 400 instead of the 413. This applies to
  `CrossSiteGuard` (`web/server.py`) and `RequestLimits` (`web/limits.py`).

### 2.2 Sessions (S1)

| Item | Detail |
|---|---|
| Cookies | `ma_access` (path `/api`, `JWT_EXPIRE_MINUTES`, 15) and `ma_refresh` (path `/api/auth`, `REFRESH_TOKEN_DAYS`, 7); `HttpOnly`, `SameSite=Strict`, `Secure` when the request came over https |
| Refresh tokens | table `refresh_tokens` holds SHA-256 hashes only; one family per sign-in; a spent token shown again later than `REFRESH_REUSE_GRACE` (10 s, two tabs refreshing together) revokes the family |
| Routes | `POST /api/auth/login`, `/api/auth/refresh`, `/api/auth/logout` (revokes; access tokens of a revoked family are refused at once) |
| Account status | only `active` users get or keep a session; `pending` and `disabled` are refused on every request |
| Cross-site | no CORS: the browser calls `/api` on the page's own origin (rewrite timeout 300 s, SSE heartbeat 15 s); a write whose `Origin` is another site → 403 |
| Secret | startup refuses the dev default or a `JWT_SECRET` shorter than 32 bytes |
| Frontend | no token in `localStorage`; `apiFetch` refreshes once on a 401 (one refresh shared by parallel calls) and repeats the call |

### 2.3 Abuse limits (S2)

- **Failed sign-ins** are stored in table `login_failures` (`key`, `failed_at`). An API restart does not reset
  an attacker's count. Keys:
  - `user:<id>` for a known account;
  - `name:<typed name>` for an unknown one;
  - `addr:<ip>` for the PC.
- **Order of checks in `POST /api/auth/login`:**
  1. Lock or back-off (`check_login`) → 429 + `Retry-After`.
  2. CAPTCHA needed and missing or wrong → 400. The try is not counted.
  3. Credentials wrong → 401. The failure is recorded.
  4. `pending` or `disabled` account → 403.
  5. Success → the account's failures are cleared.
- **Equal timing.** For unknown usernames, `check_credentials` checks against a cached dummy bcrypt hash, so
  an unknown name takes as long as a real one.
- **Request rates** live in memory as token buckets (`RateLimiter`; the API is one process):
  - signed-in callers have one bucket per user, found by decoding the access cookie;
  - signed-out callers have one bucket per address;
  - idle buckets are pruned.
- **Body cap** (`MAX_BODY_BYTES`). It is checked from `Content-Length`, and again while the body streams in → 413.
- **Logging.** Every failed sign-in is logged at INFO with its account and address. Each lock or block is
  logged as a WARNING in `logs/api.log`.

### 2.4 CAPTCHA (S2b)

| Item | Detail |
|---|---|
| Route | `GET /api/auth/captcha` → `{id, image}`; `image` is a PNG data URL (240×80) |
| Store | `CaptchaStore` in `src/core/captcha.py`: id → (answer, expiry) in memory, behind a lock; at most `MAX_OPEN` open, oldest dropped; expired ones dropped as new ones are made |
| Check | single use (removed on the first check, right or wrong); answer stripped and upper-cased, compared as bytes with `secrets.compare_digest` (constant time, no crash on non-ASCII); expired → refused |
| Sent with | `captcha_id` + `captcha_answer` in `LoginPayload` / `SignupPayload` (`web/schemas.py`) |
| Signal | response header **`X-Captcha: required`** on the 400 (missing or wrong) and on the 401 of the failure that reaches 3, so the picture shows at once; the error text stays a plain message |
| When | `needs_captcha()` in `src/core/abuse.py`: account or address has ≥ `LOGIN_CAPTCHA_AFTER` failures in the window; sign-up always |
| Frontend | `Captcha.tsx` (picture, answer box, New image); `AuthScreen.tsx` shows it in sign-up always and in sign-in when `captchaRequired` (`useAuth`); a new picture after every try (`captchaRound`) |
| Tests | `tests/test_captcha.py`. Other tests use the fixtures `captchas_solved` (every CAPTCHA counts as solved) and `solved_captcha` (a real one, answered) in `tests/conftest.py` |

### 2.5 Unlock (S2b)

| Route (admin only) | Does |
|---|---|
| `GET /api/auth/users/locks` | locked accounts (until when, failures) and blocked addresses |
| `DELETE /api/auth/users/{id}/lock` | deletes that account's rows in `login_failures` → `{unlocked: true/false}` |
| `DELETE /api/auth/users/locks/address/{address}` | the same for a PC |

`GET /api/auth/users` adds `locked_until` and `failed_logins` to each user. UI: `UserForm.tsx` shows the
"Unlock now" banner, and `BlockedComputers.tsx` shows the blocked PCs.

### 2.6 Sign-up (S7)

| Item | Detail |
|---|---|
| Routes (public) | `GET /api/auth/signup?invite=` → `{open, invite, invite_valid}`; `POST /api/auth/signup` → 201, status `pending` |
| Routes (admin) | `GET/PUT /api/auth/users/signup-settings` (`{open}`); `GET/POST /api/auth/users/invites` (the token is in the POST answer only); `DELETE /api/auth/users/invites/{id}`; `DELETE /api/auth/users/{id}` on a pending user = reject (deleted, `status: "rejected"`); `PATCH` status `active` = approve |
| Errors | 400 CAPTCHA · 403 open sign-up off and no invite · 409 username / email taken · 410 invite used, expired or revoked · 422 account rules or invite email mismatch · 429 too many sign-ups from this address |
| Tables | `user_invites` (token hash only, `created_by`, `email`, `modules_json`, `expires_at`, `used_at`); `app_settings` (`open_signup`); `users.signup_address`, `users.invited_by` |
| Rules | one module, `src/core/accounts.py` (username, password, email), shared by the admin form, `seed_users` and sign-up; a sign-up is always a `member` |
| UI | `AuthScreen.tsx` (sign-up mode; `?invite=` opens it); Settings → Users: "Waiting for approval" → Review → Approve / Reject (`UserForm.tsx`), `InvitesPanel.tsx`. On plain http there is no clipboard API, so Copy selects the link for Ctrl+C |
| Tests | `tests/test_signup.py` |

### 2.7 Files

| Part | File |
|---|---|
| Sessions, password check | `src/core/auth.py`, `web/routers/auth.py` |
| Client address, sign-in limits, locks, unlock | `src/core/abuse.py` |
| Request rate + body cap | `web/limits.py` |
| Cross-site write check | `web/server.py` (`CrossSiteGuard`) |
| Real client address on the UI side | `frontend/server.mjs` |
| CAPTCHA | `src/core/captcha.py`, `frontend/src/components/auth/Captcha.tsx` |
| Sign-up, invites | `src/core/signup.py`, `src/core/accounts.py`, `frontend/src/components/auth/AuthScreen.tsx`, `InvitesPanel.tsx` |
| Admin pages | `web/routers/users.py`, `UsersSection.tsx`, `UserForm.tsx`, `BlockedComputers.tsx` |
| Tests | `tests/test_sessions.py`, `tests/test_abuse_limits.py`, `tests/test_captcha.py`, `tests/test_signup.py` |

---

## Part 3 — The numbers and where to change them

Every number is a named constant; changing one is a one-line edit plus an API restart.

| Number | Constant | File |
|---|---|---|
| 15 min access token / 7 days refresh | `JWT_EXPIRE_MINUTES`, `REFRESH_TOKEN_DAYS` (`.env`) | `src/core/config.py` |
| 10 s two-tab grace | `REFRESH_REUSE_GRACE` | `src/core/auth.py` |
| 15 min window and lock | `LOGIN_WINDOW`, `LOGIN_LOCK` | `src/core/abuse.py` |
| 5 free failures | `LOGIN_FREE_FAILURES` | 〃 |
| back-off 1 s doubling, max 30 s | `LOGIN_BACKOFF_FIRST`, `LOGIN_BACKOFF_MAX` | 〃 |
| 10 → account lock / 20 → PC block | `ACCOUNT_LOCK_FAILURES`, `ADDRESS_BLOCK_FAILURES` | 〃 |
| CAPTCHA from the 3rd failure | `LOGIN_CAPTCHA_AFTER` | 〃 |
| 120/min, burst 60 (user) · 60/min, burst 60 (signed out) | `USER_RATE_PER_MINUTE`, `USER_BURST`, `ANON_RATE_PER_MINUTE`, `ANON_BURST` | 〃 |
| 30 MB body | `MAX_BODY_BYTES` | `web/limits.py` |
| 6 characters, alphabet | `CAPTCHA_LENGTH`, `ALPHABET` | `src/core/captcha.py` |
| 5 min CAPTCHA life | `CAPTCHA_LIFETIME_SECONDS` | 〃 |
| 2000 open CAPTCHAs | `MAX_OPEN` | 〃 |
| picture difficulty | `MAX_TURN_DEGREES` (25), `MAX_SHIFT_Y` (10), `NOISE_LINES` (6), `NOISE_DOTS` (350), `FONT_SIZE` (42) | 〃 |
| username ≥ 3, password ≥ 8 | `MIN_USERNAME_LEN`, `MIN_PASSWORD_LEN` | `src/core/accounts.py` |
| invite 7 days | `INVITE_LIFETIME` | `src/core/signup.py` |
| 5 sign-ups per PC per day | `SIGNUPS_PER_ADDRESS`, `SIGNUP_WINDOW` | 〃 |
| open sign-up on by default | `OPEN_SIGNUP_DEFAULT` | 〃 |

---

## Part 4 — Known limits and what is next

**Accepted limits**
- **Plain http on the LAN.** Traffic, passwords included, can be read by someone sniffing the LAN (S1b decision).
- **The CAPTCHA is a speed bump, not a wall.** Good text-recognition software could read some pictures.
  Together with the S2 limits, that is a fair trade on an office LAN. If automated tries get through, raise
  the picture difficulty (Part 3).
- **Open CAPTCHAs and request rates live in memory.** An API restart forgets them: an open picture just
  needs a new one. Failed sign-ins are in the database and survive a restart.
- **One PC = one person.** PCs behind one shared address (NAT, terminal server) would share their limits.
- **No audio CAPTCHA.** For someone who cannot read the picture, an admin creates the account by hand and
  unlocks it if needed.

**Still to build** (BACKLOG L16)
- **S2b live check (O39).** You check that the picture is readable and shows at the right time; the numbers
  are then tuned here and in Part 3.
- **S3 uploads:** file type read from the bytes, images re-encoded, ClamAV scan (fail closed).
- **S4 headers + XSS:** CSP, `nosniff`, `frame-ancestors 'none'`, `Referrer-Policy`; safe links in answers.
- **S5 encryption at rest:** each new stored secret is decided one at a time.
- **S6 security log:** failed sign-ins, locks, unlocks, token reuse and rejected uploads, shown to admins.
- **HTTPS**, if the app ever leaves the trusted LAN (own domain + Let's Encrypt). After that comes
  **Google / Microsoft sign-in**.

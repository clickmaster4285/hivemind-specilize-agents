# Messages, chats, sessions and history — per account and per machine

**Status:** to discuss → decide (2026-10-19).
**Related:** `frontend/src/lib/threads.ts`, `lib/projects.ts`, `hooks/useHistory.ts`, `src/models_db.py` (jobs), `src/server_vault.py`.

---

## 1. The words

| Word | Means |
|---|---|
| **Message** | One question + its answer. In the code: one **job** (the question, settings, attachments) + its **events** (the streamed answer, tools, sources). |
| **Chat** | A conversation: several messages in order, with a title (sidebar entry). In the code: a **thread**. |
| **Project** | A folder of chats. |
| **Session** | One sign-in on one browser: a login token (valid 12 h). |
| **History** | The list of a user's chats in the sidebar. |

## 2. How it works today

| What | Where it is kept | Per account? | Same on every PC? |
|---|---|---|---|
| Messages (jobs + answers) | **Server** — Postgres, `app.jobs` / `app.job_events`, with `user_id` | ✅ yes | ✅ yes |
| Chats (which messages belong together, title, pin, delete) | **Browser** — `localStorage` `multeagent_threads_v1` | ❌ **no** | ❌ **no** |
| Projects | **Browser** — `multeagent_projects_v1` | ❌ no | ❌ no |
| Open chat / open project | Browser | ❌ no | ❌ no |
| Specialist choice | Browser | ❌ no | ❌ no |
| Login token | Browser (12 h) | ✅ | — one per browser |
| Follow-up context ("make that a docx") | Sent by the **browser** from what is on screen | ✅ | only the open chat |
| Server logins (SSH) | **API memory**, per account, 8 h | ✅ | ✅ (same API) |

## 3. What goes wrong because of this

1. **Another PC or browser = broken history.** The messages are there, but the browser does not know which
   belong together, so it makes **one chat per message**. That is why the sidebar shows dozens of
   "1 turn" chats. Chat titles, pins and projects made on another PC are missing.
2. **Two accounts on one PC see each other's chat titles (privacy).** The chat list in the browser is not
   per account: when user B signs in on a browser user A used, B's sidebar lists A's chat titles (the first
   words of A's questions). B cannot open A's answers (the server refuses), but the titles alone can leak.
3. **Logout does not clean up.** The browser keeps the chat list, projects and open chat after sign-out.
4. **Logout is not a real logout.** The token is only forgotten by the browser; a copied token stays valid
   until it expires (12 h). There is no list of "signed-in devices" and no "sign out everywhere".
5. **Follow-ups depend on the screen.** The context for "write that as a docx" is whatever the browser
   shows; a chat continued on another PC (where it is split into single messages) loses its context.
6. **Clearing the browser deletes all chat organisation** (titles, pins, projects) for good.

## 4. Options

### Option A — Keep chats in the browser, but per account (quick fix)

The chat list key becomes per user (`multeagent_threads_v1:<user id>`), cleared at logout.

- ✅ Fixes **2** (other users' titles) and **3** (logout), about 1 hour of work.
- ❌ Does not fix **1, 4, 5, 6**: every PC still has its own, different history.

### Option B — Chats and projects on the server (recommended)

Two new tables in the `app` schema, each row owned by one account:

| Table | Columns |
|---|---|
| `app.chats` | id, user_id, title, project_id, pinned, created_at, updated_at, deleted_at |
| `app.projects` | id, user_id, name, created_at, deleted_at |
| `app.jobs` (existing) | + `chat_id` — which chat the message belongs to |

API: list / create / rename / pin / delete chats and projects; sending a message names its chat.
The **server builds the follow-up context** from the chat's earlier messages, instead of trusting what the
browser sends.

- ✅ Same history, titles, pins and projects **on every PC and browser**.
- ✅ Strictly per account (every query filtered by `user_id`, like messages today).
- ✅ Survives clearing the browser; included in database backups; search can run on the server.
- ✅ Follow-ups work no matter where the chat is continued.
- Cost: 2 tables + a migration, ~8 API endpoints, `useHistory` talks to the API instead of `localStorage`
  (the hook already hides where the data lives, so the screens do not change). About a day.
- **Old chats:** each browser that has a local chat list can send it once ("import my chats"); messages that
  were never grouped stay one-chat-per-message, as today.

### Option C — B + real sessions and devices

A `app.sessions` table (device name, IP, signed in, last seen); every token carries a session id.

- ✅ "Signed-in devices" list in Settings; sign out one device or **all**; logout really ends the token.
- ✅ The session's server logins (SSH) can be forgotten exactly when that session ends.
- Cost: one table, a check per request (cached), a Settings section. About half a day on top of B.

### What stays per machine (all options)

Things that are about **this screen**, not the account, stay in the browser: which chat is open, sidebar
tab, image profile. The **specialist choice** could go either way (question 3 below).

## 5. Recommendation

1. **Now: Option A's privacy part** (per-account key + clear on logout) — small, and it closes the title leak
   on shared PCs today, whatever we decide next.
2. **Next: Option B** — chats and projects on the server. It fixes the "1 turn" history, works on every PC,
   and makes follow-ups reliable.
3. **Then: Option C** when more than one person uses the system (sessions, sign out everywhere).

## 6. Questions to decide

1. **Option B** (chats on the server): yes?
2. **Option C** (sessions / devices): now together with B, or later?
3. **Specialist choice, image profile:** per **account** (same on every PC) or per **machine**?
4. **Old chats:** import the browser's chat list once (keeps titles and grouping), or start fresh?
5. **Retention:** keep history forever, or delete messages after N days? Should an **admin** be able to see
   other users' chats (support, audit), or never (privacy)?
6. **Sharing:** later, should a chat be shareable with another account (read-only link)?

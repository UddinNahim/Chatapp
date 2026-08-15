# Sockudo + Django-Bolt Chat Demo

Django-Bolt publishes events over HTTP; Sockudo fans them out over WebSocket to browsers.

## Architecture

```text
Browser (demos/sockudo)  ←── WebSocket ──→  Sockudo (:6001)
Django-Bolt (:8000)      ─── HTTP trigger ─→  Sockudo (:6001)
```

| Role | What |
|------|------|
| **Publish** | Django `sockudo.trigger(channel, event, data)` |
| **Subscribe** | Browser Pusher client → channel + event bind |
| **Hub** | Sockudo (Pusher-compatible realtime server) |

Local credentials (must match Sockudo default app) in `config/settings.py` → `SOCKUDO`:

- `app-id` / `app-key` / `app-secret`
- Host `127.0.0.1`, port `6001`

---

## Prerequisites

- Docker (Sockudo + Redis)
- Project deps via `uv`
- Three terminals

---

## 1. Start Sockudo + Redis

```bash
cd sockudo
docker compose up sockudo
```

Local compose uses **memory** drivers (no Redis required). That avoids
`Failed to connect to Redis: Temporary failure in name resolution`.

Health check:

```bash
curl -f http://127.0.0.1:6001/up
```

| Service | URL |
|---------|-----|
| API / WebSocket | `http://127.0.0.1:6001` |
| Metrics | `http://127.0.0.1:9601/metrics` |

App credentials must match Django `SOCKUDO`: `app-id` / `app-key` / `app-secret`.

If you previously hit the Redis DNS crash loop:

```bash
cd sockudo
docker compose down
docker compose up sockudo
```

Optional Redis (multi-node later): `docker compose up sockudo redis` and set
`ADAPTER_DRIVER` / `CACHE_DRIVER` / `QUEUE_DRIVER` back to `redis` in
`sockudo/docker-compose.yml`.

---

## 2. Start Django-Bolt

From repo root `learn_django-bolt`:

```bash
uv run python manage.py runbolt --host 127.0.0.1 --port 8000 --dev
```

| | URL |
|--|-----|
| API | http://127.0.0.1:8000 |
| Docs | http://127.0.0.1:8000/docs |

Use `--dev` so code changes reload.

---

## 3. Start the demo pages

```bash
cd demos/sockudo
python3 -m http.server 5500 --bind 127.0.0.1
```

| Page | URL |
|------|-----|
| Group chat | http://127.0.0.1:5500/ — **3 client cards** (Alice / Bob / Carol) |
| One-to-one DM + inbox | http://127.0.0.1:5500/dm.html — **2 client cards** (User 1 / User 2) |

---

## Demo A — Group chat (public lobby)

**Files**

- `config/api.py` → `POST /messages`
- `demos/sockudo/index.html`

| | Value |
|--|--------|
| Channel | `chat-general` |
| Event | `message.new` |

**Test**

1. Open http://127.0.0.1:5500/ — wait until all three cards show **subscribed**
2. Send from Alice’s card
3. Alice, Bob, and Carol logs should all show the message

No need for multiple browser tabs — each card is its own WebSocket client.

```bash
curl -i -X POST http://127.0.0.1:8000/messages \
  -H "Content-Type: application/json" \
  -d '{"username":"bob","text":"salam"}'
```

Anyone on `chat-general` receives every message.

---

## Demo B — One-to-one DM (private channel)

**Files**

- `config/api.py` → `POST /pusher/auth`, `POST /dm/messages`
- `demos/sockudo/dm.html`

| | Value |
|--|--------|
| Channel | `private-chat.{min_id}.{max_id}` (e.g. `private-chat.1.2`) |
| Event | `message.new` |
| Demo identity | JSON `from_user_id` + auth form `user_id` (learning only) |

**Test**

1. Open http://127.0.0.1:5500/dm.html
2. Wait until both cards show **subscribed** (`private-chat.1.2`)
3. Send from User 1 — both cards receive
4. Send from User 2 — both cards receive

Two cards on one page = two authenticated clients (no second tab needed).

```bash
curl -i -X POST http://127.0.0.1:8000/dm/messages \
  -H "Content-Type: application/json" \
  -d '{"from_user_id":1,"to_user_id":2,"text":"salam"}'
```

Auth check (form body, like pusher-js):

```bash
curl -i -X POST http://127.0.0.1:8000/pusher/auth \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "socket_id=1.1&channel_name=private-chat.1.2&user_id=1"
```

User-inbox channel (only that user):

```bash
curl -i -X POST http://127.0.0.1:8000/pusher/auth \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "socket_id=1.1&channel_name=private-user.2&user_id=2"
```

---

## Demo C — Notifications (user channel + inbox)

**Files**

- `notifications/models.py` → `Notification` rows in SQLite
- `config/api.py` → `GET /notifications`, `POST /notifications/read-all`
- `demos/sockudo/dm.html` → bell, toast, inbox list

| | Value |
|--|--------|
| Chat channel | `private-chat.1.2` / `message.new` |
| Inbox channel | `private-user.{id}` / `notification.new` |

**Test**

1. Open http://127.0.0.1:5500/dm.html — both cards **subscribed**
2. Send from User 1
3. Both logs show the DM
4. **Only User 2** gets a red badge, toast, and inbox row
5. Refresh the page — User 2 badge/list come back from `GET /notifications`
6. User 2 **Mark read** — badge clears

```bash
curl -s http://127.0.0.1:8000/notifications?user_id=2
curl -i -X POST http://127.0.0.1:8000/notifications/read-all \
  -H "Content-Type: application/json" \
  -d '{"user_id":2}'
```

Sender is never notified. Chat event ≠ inbox event.

---

## Other API routes

| Method | Path | Sockudo |
|--------|------|---------|
| `POST` | `/users` | channel `users`, event `user.created` |
| `GET` | `/users` | — |
| `GET` | `/users/{id}` | — |
| `POST` | `/messages` | `chat-general` / `message.new` |
| `POST` | `/pusher/auth` | `private-chat.*` or `private-user.{id}` signature |
| `POST` | `/dm/messages` | chat `message.new` + inbox `notification.new` |
| `GET` | `/notifications?user_id=` | persisted inbox |
| `POST` | `/notifications/{id}/read` | mark one read |
| `POST` | `/notifications/read-all` | clear badge |

---

## Ports

| Service | Port |
|---------|------|
| Django-Bolt | `8000` |
| Sockudo WS/API | `6001` |
| Sockudo metrics | `9601` |
| Demo HTML | `5500` |

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Connection failed | `curl -f http://127.0.0.1:6001/up` |
| Send OK, no UI event | Subscribe first; channel + event names must match |
| Browser POST blocked | Endpoints use `@cors(origins=["*"])`; check Network tab |
| No broadcast after code change | Restart with `runbolt --dev` |
| Sockudo crash loop / Redis DNS / push memory | Restart: `docker compose down && docker compose up sockudo` (needs `PUSH_ALLOW_MEMORY_DRIVERS=true`) |
| DM subscribe 403 | `user_id` must match the DM pair, or own `private-user.{id}` |
| Auth status 0 / Failed to fetch | CORS or Bolt down — hard-refresh `dm.html`; confirm `:8000` is up |

**Order:** page must be **Connected + subscribed** before Send/POST. Events are not queued for late subscribers.

---

## Mental model

- **HTTP** = write / publish (Django → Sockudo)
- **WebSocket** = live receive (Browser ← Sockudo)
- Same **channel + event** on both sides
- Demos do not persist chat messages; **notifications** are saved in Django
- Chat channel = live message; `private-user.{id}` = badge / toast / inbox

# Deploying NetRescue on Render

Two services: a **Python web service** (the API) and a **static site** (the dashboard).
`render.yaml` at the repo root describes both; the values below are what it sets.

## Why the browser shows "request was blocked before it reached the API"

The API only accepts browser calls from origins it is told about. With `CORS_ORIGINS`
empty it allows loopback origins only, so a page served from
`https://netrescue-1.onrender.com` fails the CORS preflight. Browsers report that
identically to "server unreachable". Setting `CORS_ORIGINS` fixes it.

A second gate follows: almost every lab calls routes that need the `operator` role, and a
browser cannot hold a secret. See *Authorization model* below.

## Backend service (`netrescue`, Python web service)

| Setting | Value |
|---|---|
| Root directory | repo root |
| Build command | `pip install -r requirements.txt` |
| Start command | `uvicorn apps.api.main:app --host 0.0.0.0 --port $PORT` |
| Health check path | `/health` |

| Env var | Example (non-secret) | Notes |
|---|---|---|
| `PYTHON_VERSION` | `3.11.9` | The code needs Python 3.11+. |
| `CORS_ORIGINS` | `https://netrescue-1.onrender.com` | Exact origin(s), comma-separated. No `*`, no path. Invalid values stop startup with a message. |
| `CORS_ALLOW_LOOPBACK` | `true` (default) | Set `false` to serve only `CORS_ORIGINS`. |
| `PUBLIC_DEMO` | `true` | Enables the hosted-demo authorization model below. |
| `API_AUTH_TOKEN` | *(secret)* | Optional but recommended. Unlocks the non-simulation operator routes for callers who send it. **Backend only.** |
| `API_SECRET_KEY` | *(secret)* | Generate; never reuse the example value. |
| `DATABASE_URL` | *(secret)* | Optional. Without PostgreSQL the API starts in degraded mode: simulations work, run history is in-process. |

Generate secrets locally and paste them into the Render dashboard (do not commit them):

```
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## Frontend service (`netrescue-1`, static site)

| Setting | Value |
|---|---|
| Root directory | `apps/dashboard` |
| Build command | `npm ci && npm run build` |
| Publish directory | `dist` |
| Rewrite rule | `/*` → `/index.html` (rewrite) |

| Env var | Example | Notes |
|---|---|---|
| `VITE_API_BASE_URL` | `https://netrescue.onrender.com` | Origin only. **Build-time**: redeploy the static site after changing it. |

`VITE_*` values are compiled into public JavaScript. Never put `API_AUTH_TOKEN` or any
secret in a `VITE_*` variable. If `VITE_API_BASE_URL` is missing on a non-local host the
site shows a configuration error instead of silently calling `localhost`.

## Authorization model

Roles above `readonly` are claimed with `X-NetRescue-Role`. The server decides whether to
believe the claim:

- **`API_AUTH_TOKEN` set**: the claim is honoured only with a matching `X-NetRescue-Token`.
- **`PUBLIC_DEMO=true`**: a caller without the token may be `operator` **only** on the
  simulation routes listed in `netrescue/security/demo.py` (routing / TCP / shaping /
  TCP-vs-UDP *simulation* / integration labs, seeded fault scenarios, hysteresis sweep,
  scheduler comparison, traffic lab, multipath transfer, hysteresis playground). Every other
  write route stays `readonly` for them: real ping/traceroute benchmark runs, deleting run
  history, `tc/netem`, the recovery lab, and the real loopback-socket experiment. Only the
  `operator` role can be granted this way.
- Both unset: legacy loopback trust model, intended for local use only.

Public demo caveats: the multipath and playground labs keep one in-process session on the
server, shared by all visitors, and the API has no rate limiting. Treat it as a demo, not a
multi-tenant service.

## What a hosted backend can and cannot do

| Feature | On Render |
|---|---|
| Routing, TCP congestion, traffic shaping, TCP-vs-UDP **simulation**, integration lab | Works; results are simulations. |
| Seeded fault scenarios, hysteresis sweep, scheduler comparison | Works; labelled simulated. |
| Multipath transfer, hysteresis playground | Works in-process on the server (loopback sockets there, not on your device). |
| Live ping / traceroute / adapter info | Runs on the **Render container**, not your network; the Linux image may lack the tools, in which case the API says so. Needs the token for benchmark runs. |
| `tc/netem` emulation, real loopback-socket TCP/UDP experiment on your machine | **Not possible remotely.** Run NetRescue locally (Linux for `tc/netem`; needs root). |

The dashboard shows a notice whenever it is connected to a non-local API.

## Verify

```
curl -s https://netrescue.onrender.com/health
# Preflight from the frontend origin; expect 200 and access-control-allow-origin: https://netrescue-1.onrender.com
curl -si -X OPTIONS https://netrescue.onrender.com/api/labs/info \
  -H "Origin: https://netrescue-1.onrender.com" \
  -H "Access-Control-Request-Method: GET" \
  -H "Access-Control-Request-Headers: x-netrescue-role,x-netrescue-principal"
# An unapproved origin must NOT get an allow-origin header (expect 400)
curl -si -X OPTIONS https://netrescue.onrender.com/api/labs/info \
  -H "Origin: https://evil.example.com" -H "Access-Control-Request-Method: GET" | head -5
# Demo operator on a simulation route (expect 200), and denied on a host route (expect 403)
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://netrescue.onrender.com/api/labs/shaping/simulate \
  -H "X-NetRescue-Role: operator" -H "Content-Type: application/json" -d '{}'   # 200 or 422 (never 403)
curl -s -o /dev/null -w "%{http_code}\n" -X DELETE https://netrescue.onrender.com/api/diagnostics/runs \
  -H "X-NetRescue-Role: operator"                                                # 403
```

Then open the frontend and run a lab; the browser console must show no CORS error. Free
instances sleep: the first request after idle can take about a minute.

## Production checklist

- [ ] `CORS_ORIGINS` is the exact frontend origin (scheme + host, no trailing path).
- [ ] `PUBLIC_DEMO` set deliberately (`true` for the public demo, otherwise unset).
- [ ] `API_AUTH_TOKEN` and `API_SECRET_KEY` generated and stored only in Render.
- [ ] `VITE_API_BASE_URL` set on the static site; site redeployed after changing it.
- [ ] SPA rewrite `/*` → `/index.html` configured.
- [ ] `PYTHON_VERSION` ≥ 3.11.
- [ ] `/health` returns `ok`; preflight checks above behave as described.
- [ ] No `.env` or secrets committed (`.env` is git-ignored; only `.env.example` is tracked).

## Troubleshooting: "Connected to the hosted API" but features fail

Reads (`GET`) working while every run/simulate button fails means the API is up and CORS is
fine, but writes are being denied. Check from any machine with `curl` (no Python needed):

```
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://netrescue.onrender.com/api/labs/shaping/simulate \
  -H "Origin: https://netrescue-1.onrender.com" -H "X-NetRescue-Role: operator" \
  -H "Content-Type: application/json" -d '{}'
```

- `403` with `lacks permission 'write'` → `API_AUTH_TOKEN` is set but `PUBLIC_DEMO` is not (or the
  backend has not been redeployed with the version that supports it). Set `PUBLIC_DEMO=true`, redeploy.
- `200` / `422` → authorised (422 only means the empty body is invalid).
- `502` / no response after ~100 s → the instance is asleep or the request is too slow; retry.

Expected in hosted demo mode: simulation labs, seeded fault scenarios, hysteresis sweep, multipath
and playground **work**; **Run Diagnostics / benchmarks, the real TCP-vs-UDP experiment, run-history
delete, `tc/netem`, and the recovery lab return 403 by design** and the UI labels them as local-only.

## Verifying without installing Python

- **Backend tests:** push to GitHub; `.github/workflows/ci.yml` runs `pytest` on Python 3.11, plus the
  frontend type-check, tests and build. Nothing to install locally.
- **Deployed API:** the `curl` commands above and the Verify section need only `curl`
  (included with Windows 10/11; use `curl.exe` in PowerShell).
- **Browser:** open the site, press F12 → Network, run a lab; each call should be 200/201 and the
  console should show no CORS errors. A 403 on a write is the case described above.
- **Interactive API docs:** `https://netrescue.onrender.com/docs` can send the same requests from the
  browser (add the `X-NetRescue-Role: operator` header via "Try it out" to mimic the site).

## Real diagnostics: run NetRescue locally

On the public site, **Run Diagnostics, Run benchmark, DNS resolve, ping, traceroute and adapter
inspection are disabled**. They measure the machine the API runs on, which on Render is a hosting
container, not your device, and letting anonymous visitors aim probes from it would make the server a
scanning relay. With `PUBLIC_DEMO=true` those routes (and the real TCP-vs-UDP experiment) return 403 unless
the caller holds `API_AUTH_TOKEN`. They are labelled *real* in the UI only when they ran on your own machine.

What stays public, and is labelled *simulated*: seeded fault scenarios, the hysteresis sweep and the
scheduler comparison. The scheduler comparison replays modelled delays in real time, so on the public demo it
is limited to 64 kB, 10 repetitions, 8 paths and one run at a time (larger values return 422; a second
concurrent run returns 429).

To measure your own network (Windows 10/11, needs Python 3.11+ and Node 20+):

```
# terminal 1 - API, loopback only
py -m venv .venv
.venvScriptsactivate
pip install -r requirements.txt
uvicorn apps.api.main:app --host 127.0.0.1 --port 8000

# terminal 2 - dashboard (no VITE_API_BASE_URL needed: it defaults to http://127.0.0.1:8000)
cd appsdashboard
npm ci
npm run dev
```

Open http://localhost:3000, go to Network Diagnostics and press **Run Diagnostics**. Leave `PUBLIC_DEMO`
unset locally. Results then come from your machine's own `ping`, `tracert`, DNS resolver and adapters.

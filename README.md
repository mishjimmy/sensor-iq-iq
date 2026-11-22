# SensorIQ Monitor

A read-only dashboard for ETC SensorIQ relay panels, with live circuit status pushed into the EIS Grist doc.

- **Dashboard (`/`)**: every panel's 48 slots, colour-coded on / off / tripped / empty / offline, with live amps. Totals per circuit, per room (from the Grist panel schedule), per panel and for the site. Updates every 5 s.
- **Usage stats (`/stats`)**: charts over 6 h / 24 h / 7 d / 30 d:
  - site power over time
  - energy per panel per hour or day (with a table view)
  - top circuits by average current, peak current, kWh or hours on
  - energy by served room
  - an hour-by-day usage heatmap
  - breaker trips and panel outages
- **Panel management (`/panels`)**: add, edit, disable or remove panels by IP, name, room and Grist panel number. Each panel gets a "Test connection" button.
- **Grist sync**: writes live status to `Panel_Circuit_Live` and `Panel_Live`. Formula columns in `Panel_Schedule_Export` and `Fixture_Schedule` look those tables up.

## Read-only guarantee

The service only ever sends **HTTP GET** to a panel, and only to `/api/info`, `/api/outputs` and (on demand) `/api/config`. This is enforced in `app/sensoriq.py` and checked by `tests/test_readonly.py`. Nothing in the service can switch, set levels or change config on a panel. "Editing panels" in the UI only edits the local `panels.yaml`.

## Setup

```bash
python -m venv .venv
.venv/bin/pip install -e .            # Windows: .venv\Scripts\pip install -e .
cp .env.example .env                  # then fill in GRIST_API_KEY, GRIST_SERVER, ADMIN_TOKEN
cp panels.yaml.example panels.yaml    # or start empty and add panels in the UI
.venv/bin/python -m app.main          # serves on http://<server>:8080
```

### Dokploy (recommended)

1. **Create Application** with your GitHub repo, branch `main` and build type **Dockerfile** (`./Dockerfile`).
2. **Environment** tab:
   ```
   GRIST_API_KEY=<key>
   GRIST_SERVER=https://<your-grist-host>/o/eisv2
   ADMIN_TOKEN=<long random string>
   ```
   The image already sets `PANELS_FILE=/data/panels.yaml` and `HISTORY_DB=/data/history.db`.
   **Don't set them yourself.** A relative value such as `panels.yaml` points outside the volume, and the container can't write there.
3. **Advanced → Volumes**: add a **named volume** (e.g. `sensoriq-data`) mounted at **`/data`**.
   - Use a directory volume, **not** a File Mount of `panels.yaml`. The UI saves that file by atomic rename, which fails on a single-file bind mount.
   - Without the volume, panels and history are lost on every redeploy.
4. **Advanced → Replicas: 1.** State is in-process, so two replicas would both poll the panels and both write to Grist.
5. **Domains**: point a host at container port **8080**. Live updates use Server-Sent Events, which pass through Traefik without extra config.
6. **Optional:** turn on **Security → Basic Auth** to restrict who can view the dashboard. `ADMIN_TOKEN` only protects panel editing.
7. **Before the first deploy**, check from the server that containers can reach the panels:
   ```bash
   docker run --rm curlimages/curl -s http://10.64.41.205/api/info
   ```
8. Deploy, open `/panels`, and add each panel with its EIS panel number.

The container has a built-in `HEALTHCHECK` on `/healthz`.

### Plain Docker

```bash
docker build -t sensoriq-monitor .
docker run -d --name sensoriq --restart unless-stopped -p 8080:8080 \
  --env-file .env -v sensoriq-data:/data sensoriq-monitor
```

`panels.yaml` and `history.db` live in the `/data` volume.

### systemd (Linux, no Docker)

```ini
# /etc/systemd/system/sensoriq-monitor.service
[Unit]
Description=SensorIQ panel monitor
After=network-online.target

[Service]
WorkingDirectory=/opt/sensoriq-monitor
EnvironmentFile=/opt/sensoriq-monitor/.env
ExecStart=/opt/sensoriq-monitor/.venv/bin/python -m app.main
Restart=always
User=sensoriq

[Install]
WantedBy=multi-user.target
```

## Configuration (environment variables)

| Variable | Default | Notes |
|---|---|---|
| `GRIST_API_KEY` | *(blank)* | Grist → Profile → API key. Needs edit access to the doc. If blank, sync is off and the dashboard still works. |
| `GRIST_SERVER` | *(blank)* | Grist base URL, e.g. `https://grist.example.com` or `https://grist.example.com/o/eisv2` |
| `GRIST_DOC_ID` | `hvoLt8XVgH1nGhrV7KAH6B` | EIS OVERALL [UPDATED] |
| `GRIST_SYNC_INTERVAL_S` | 30 | Push period. Only changed circuits are sent. |
| `GRIST_MAP_REFRESH_S` | 600 | How often the circuit→room map is re-read from `Panel_Schedule_Export` |
| `PANELS_FILE` | `panels.yaml` | Panel registry |
| `POLL_INTERVAL_S` | 5 | |
| `POLL_TIMEOUT_S` | 3 | |
| `OFFLINE_AFTER_FAILURES` | 3 | After this many failed polls a panel is marked offline and retried about every 30 s |
| `HISTORY_DB` | `history.db` (`/data/history.db` in Docker) | SQLite file behind `/stats` |
| `HISTORY_DAYS` | 90 | Retention. Expect about 10 MB per panel per 30 days (11 panels × 90 days ≈ 330 MB). |
| `ADMIN_TOKEN` | *(blank)* | Required to edit panels from the UI. If blank, the UI is view-only and you edit the file instead. |
| `HOST` / `PORT` | `0.0.0.0` / `8080` | |

## Adding panels

You can add panels from **`/panels`** in the UI (enter the admin token), or by editing `panels.yaml`. Edits to the file are picked up within about 5 s, with no restart needed.

```yaml
panels:
  - name: EER01 Panel 12
    ip: 10.64.41.205
    room: EER01          # where the panel physically lives
    grist_panel: 12      # EIS panel number -> Grist circuits "1201".."1248"
    slot_offset: 0       # circuit number = slot + offset
    enabled: true
```

**How a panel is identified in Grist:** `grist_panel` is the EIS panel number used in `Panel_Schedule_Export.Panel` (11–14, 21–26, 31…). The circuit key is `PPCC`, so slot 18 on `grist_panel: 12` becomes circuit **`1218`**. That matches `Panel_Schedule_Export.Circuit` and `Fixture_Schedule.Device_Power_Circuit`.

Validation rules:
- The IP and `grist_panel` must each be unique.
- A bad hand edit is reported on the dashboard, and the last good config keeps running.

## Grist schema (in EIS OVERALL [UPDATED])

These were created once and are owned by this service:

| Table | Key | Columns |
|---|---|---|
| `Panel_Circuit_Live` | `Circuit` | Panel_No, Circuit_No, Status (on/off/tripped/empty/offline), Current_A, Power_W, Voltage, Panel_Name, Updated_At (last change), Raw (the panel's full `/api/outputs` JSON for the slot) |
| `Panel_Live` | `Panel_No` | Name, IP, Room, Online, Last_Seen, Total_A, Tripped_Count, Errors |

These formula columns were added to existing tables. No existing data is written.

| Table | Column | Shows |
|---|---|---|
| `Panel_Schedule_Export` | `Live_Status`, `Live_A`, `Live_Updated`, `Live_Raw` | live state of each circuit, plus the raw panel JSON |
| `Fixture_Schedule` | `Circuit_Live_Status`, `Circuit_Live_A` | live state of the circuit feeding each fixture |

Status shows `no data` until a panel with that `grist_panel` is configured and synced. When a panel stops answering, the service writes `offline` to all of its circuits. Grist formulas can't time out on their own, so this is how staleness shows up there.

`Circuit_Live_A` is the whole circuit's current, shared by every fixture on that circuit.

## Usage history

Every poll feeds an in-memory accumulator. When a time bucket closes, it is written to SQLite:

| Table | Granularity | Contents |
|---|---|---|
| `panel_min` | 1 minute per panel | average and peak W, average A, tripped count |
| `circuit_15m` | 15 minutes per circuit | average and peak A, average W, fraction of time on. Circuits that never drew current are skipped. |
| `events` | one row per change | breaker trips and recoveries, and panel offline/online changes. Routine on/off switching is not logged. |

Limitations:
- Energy (kWh) is integrated from the 1-minute averages. Time when a panel or the service was down is a gap, not zero.
- History starts when the service starts. Nothing is backfilled.
- `/stats?range=7d` (or `6h`, `24h`, `30d`) links straight to a range.

## API

- `GET /api/state`: full JSON snapshot
- `GET /api/stream`: Server-Sent Events, one snapshot per poll
- `GET /api/history?range=6h|24h|7d|30d`: aggregated history for the charts
- `GET /healthz`: liveness and panel counts
- `GET /api/panels`: registry
- `POST /api/panels`, `PUT /api/panels/{ip}`, `DELETE /api/panels/{ip}`: needs the `X-Admin-Token` header
- `POST /api/panels/test {"ip": …}`: reads `/api/info` from a panel. Needs the token.
- `GET /api/panels/{ip}/config`: the panel's XML config (read-only passthrough)

## SensorIQ API notes

These were observed on firmware 4.5.0.8 (`sensoriq-brick2.0`).

`/api/outputs` returns one entry per slot with:
- `outputStatus` (`on`/`off`) and `breakerStatus` (`closed`/`tripped`)
- `load.current` in **mA**, `load.power` in **mW**, `load.voltage` in V
- `type` (`"None"` = empty slot)

`/api/info` has the model, slot count, incoming voltage and `currentErrors` (e.g. "Breaker tripped 6").

## Tests

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

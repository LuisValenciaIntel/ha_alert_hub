# Home Alert Hub

Installable mobile-friendly web app for Home Assistant alerts.

## Features

- Login page with session-based authentication.
- Notification feed with text and optional images.
- Home Assistant ingestion endpoint for alerts and camera snapshots.
- Progressive Web App (PWA) support with manifest and service worker.
- Browser push notifications after the user signs in and enables notifications.
- SQLite storage for users, alerts, and push subscriptions.

## Project layout

- `app.py` - runner and admin/token helper commands.
- `notifications_app/__init__.py` - Flask app factory and routes.
- `notifications_app/db.py` - SQLite schema and data access.
- `notifications_app/webpush.py` - VAPID key generation and Web Push delivery.
- `notifications_app/templates/` - login and notification views.
- `notifications_app/static/` - PWA assets, styles, and client-side logic.
- `tests/test_app.py` - basic app tests.

## Setup

```cmd
cd C:\projects\ipss\repos\notifications_page
python -m pip install -r requirements.txt
```

## Option 1: Run with Docker

Copy the sample environment file and update the values you want to use:

```cmd
cd C:\projects\ipss\repos\notifications_page
copy .env.example .env
```

Build and start the app:

```cmd
cd C:\projects\ipss\repos\notifications_page
docker compose up --build -d
```

Stop it later:

```cmd
cd C:\projects\ipss\repos\notifications_page
docker compose down
```

The container keeps persistent data in:

- `instance/` for the SQLite database, token, and generated credentials
- `media/` for saved camera snapshots

## First run

Start the application:

```cmd
cd C:\projects\ipss\repos\notifications_page
python app.py
```

On first run the app will:

- create `instance/notifications.db`
- generate a Home Assistant automation token in `instance/automation_token.txt`
- create an initial `admin` account and store the generated password in `instance/initial_admin_password.txt`

You can also create or update credentials manually:

```cmd
python app.py init-admin --username myuser --password MyStrongPassword123!
```

Print the automation token later:

```cmd
python app.py show-token
```

Open the app in your browser:

```text
http://127.0.0.1:5000
```

After logging in on your phone, tap **Enable notifications**. If your browser supports install prompts, tap **Install app** to add it to the home screen.

## Send alerts from Home Assistant

Use the generated automation token as either `Authorization: Bearer <token>` or `X-API-Key: <token>`.

### JSON message example

```cmd
curl -X POST http://127.0.0.1:5000/api/ingest ^
  -H "Content-Type: application/json" ^
  -H "Authorization: Bearer YOUR_TOKEN" ^
  -d "{\"title\":\"Front Door\",\"message\":\"Motion detected\",\"source\":\"home-assistant\",\"image_url\":\"https://example.local/snapshot.jpg\"}"
```

### JSON with base64 image

```cmd
curl -X POST http://127.0.0.1:5000/api/ingest ^
  -H "Content-Type: application/json" ^
  -H "Authorization: Bearer YOUR_TOKEN" ^
  -d "{\"title\":\"Garage\",\"message\":\"Camera snapshot\",\"image_base64\":\"...base64 bytes...\",\"image_mime\":\"image/jpeg\"}"
```

### Home Assistant `rest_command` example

```yaml
rest_command:
  push_home_alert:
    url: "http://YOUR_SERVER_IP:5000/api/ingest"
    method: POST
    headers:
      Authorization: "Bearer !secret home_alert_hub_token"
      Content-Type: "application/json"
    payload: >
      {
        "title": "{{ title }}",
        "message": "{{ message }}",
        "source": "home-assistant",
        "image_url": "{{ image_url }}"
      }
```

Example automation using that command:

```yaml
action:
  - service: rest_command.push_home_alert
    data:
      title: "Front door"
      message: "Motion detected"
      image_url: "https://YOUR_HA_HOST/local/snapshots/front-door.jpg"
```

## Option 2: Home Assistant automation examples

### Example A: Text-only alert

```yaml
rest_command:
  push_home_alert_text:
    url: "http://YOUR_SERVER_IP:5000/api/ingest"
    method: POST
    headers:
      Authorization: "Bearer !secret home_alert_hub_token"
      Content-Type: "application/json"
    payload: >
      {
        "title": "{{ title }}",
        "message": "{{ message }}",
        "source": "home-assistant"
      }

automation:
  - alias: Send washer finished alert to Home Alert Hub
    trigger:
      - platform: state
        entity_id: binary_sensor.washer_finished
        to: "on"
    action:
      - service: rest_command.push_home_alert_text
        data:
          title: "Washer"
          message: "Laundry cycle finished"
```

### Example B: Camera snapshot alert

This example first saves a snapshot from a Home Assistant camera and then sends the public URL to the app.

```yaml
rest_command:
  push_home_alert_snapshot:
    url: "http://YOUR_SERVER_IP:5000/api/ingest"
    method: POST
    headers:
      Authorization: "Bearer !secret home_alert_hub_token"
      Content-Type: "application/json"
    payload: >
      {
        "title": "{{ title }}",
        "message": "{{ message }}",
        "source": "home-assistant",
        "image_url": "{{ image_url }}"
      }

automation:
  - alias: Send front door camera snapshot to Home Alert Hub
    trigger:
      - platform: state
        entity_id: binary_sensor.front_door_motion
        to: "on"
    action:
      - service: camera.snapshot
        target:
          entity_id: camera.front_door
        data:
          filename: "/config/www/snapshots/front-door-last.jpg"
      - delay: "00:00:02"
      - service: rest_command.push_home_alert_snapshot
        data:
          title: "Front door"
          message: "Motion detected"
          image_url: "https://YOUR_HA_HOST/local/snapshots/front-door-last.jpg"
```

### Example C: Send uploaded file instead of a URL

If you prefer not to expose the image from Home Assistant, the app also accepts multipart form uploads to `/api/ingest` using an `image` file field. That is usually easiest from a custom script or shell command step.

## Notes

- Browser push notifications usually require HTTPS when accessed from a phone outside `localhost`.
- For Android, Chrome-based browsers generally provide the best PWA install and push support.
- If you expose the app on your home network or internet, place it behind HTTPS and set a strong `SECRET_KEY`.

## HTTPS / reverse-proxy deployment for phone push notifications

If you want push notifications to work reliably on a phone, serve the app through HTTPS with a stable hostname such as `https://alerts.example.com`.

### Why HTTPS matters

- mobile browsers generally require HTTPS for service workers and push subscriptions
- the installed PWA should be opened from the same final HTTPS URL every time
- the `PUBLIC_BASE_URL` value should match that public HTTPS address

Example:

```text
PUBLIC_BASE_URL=https://alerts.example.com
```

### Recommended deployment shape

Run the Flask app only on the local machine or Docker network, then put a reverse proxy in front of it:

```text
Phone Browser -> HTTPS reverse proxy -> Home Alert Hub on http://127.0.0.1:5000
```

### Reverse proxy checklist

- use a real DNS name or a stable local hostname
- terminate TLS at the reverse proxy
- forward requests to `http://127.0.0.1:5000`
- keep `PUBLIC_BASE_URL` set to the public `https://...` URL
- allow uploads large enough for camera snapshots
- avoid changing domains after users install the app and enable notifications

### Environment example for HTTPS

If you are using Docker Compose, your `.env` file should look similar to this:

```dotenv
SECRET_KEY=replace-with-a-long-random-string
APP_ADMIN_USERNAME=admin
APP_ADMIN_PASSWORD=ChangeMe123!
HOME_ASSISTANT_API_TOKEN=replace-with-your-token-or-leave-empty-for-auto-generation
PUBLIC_BASE_URL=https://alerts.example.com
VAPID_SUBJECT=mailto:alerts@example.com
POLL_INTERVAL_SECONDS=20
```

### Option A: Nginx reverse proxy

An example config is included at `deploy/nginx.conf.example`.

Typical steps:

1. point your DNS name to the server
2. start the app on port `5000`
3. install the Nginx config
4. obtain a certificate with Let's Encrypt
5. reload Nginx

Key parts of the Nginx setup:

- redirect HTTP to HTTPS
- use your TLS certificate files
- proxy all traffic to `127.0.0.1:5000`
- pass `X-Forwarded-Proto https`

### Option B: Caddy reverse proxy

An example config is included at `deploy/Caddyfile.example`.

Caddy is often the easiest option because it can automatically request and renew HTTPS certificates when your domain is public.

High-level flow:

1. point your DNS name to the server
2. install Caddy
3. copy `deploy/Caddyfile.example` to your live Caddyfile
4. replace `alerts.example.com` with your domain
5. start or reload Caddy

### Docker + reverse proxy example

Start the app container:

```cmd
cd C:\projects\ipss\repos\notifications_page
docker compose up --build -d
```

Then expose only the reverse proxy publicly. The reverse proxy should forward to the container port mapping on `127.0.0.1:5000`.

### Home Assistant URL updates when using HTTPS

Once you deploy the app behind HTTPS, update your Home Assistant commands to send alerts to the same public HTTPS base URL.

Example:

```yaml
rest_command:
  push_home_alert:
    url: "https://alerts.example.com/api/ingest"
    method: POST
    headers:
      Authorization: "Bearer !secret home_alert_hub_token"
      Content-Type: "application/json"
```

### Mobile browser notes

- install the app from the final HTTPS URL, not from `http://127.0.0.1:5000`
- after login, tap **Enable notifications** once from that installed or open HTTPS app
- if you later change the domain, users may need to reinstall the PWA and re-enable notifications
- iPhone and Android browser support differs; Android Chrome usually provides the smoothest install and push experience

### Quick validation after deployment

After you finish the reverse-proxy setup, verify these in order:

1. `https://your-domain/health` returns JSON
2. `https://your-domain/login` loads without certificate warnings
3. the PWA can be installed from the phone
4. after login, tapping **Enable notifications** succeeds
5. a test POST to `https://your-domain/api/ingest` creates an alert in the feed

## Test

```cmd
cd C:\projects\ipss\repos\notifications_page
python -m unittest discover -s tests
```




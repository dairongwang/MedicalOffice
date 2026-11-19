"""End-to-end tests (``python dev.py e2e``): a real browser against an isolated stack.

The session starts, on free ports:

    the API          a throw-away SQLite database in a temporary folder, APP_ENV=test, the dummy data
                     (SEED_FAKE_ACCOUNTS=true), the built-in fakes for e-mail / SMS / payments; it also serves
                     the patients' app (apps/web production build — built once if missing)
    the staff dashboard                  apps/staff/serve.py, pointed at that API
    the office administration dashboard  apps/admin/serve.py, pointed at that API

Nothing of the developer's own database or ports is touched.

The browser (first that works): ``E2E_BROWSER_PATH`` (a Chrome / Chromium binary) · Playwright's own
Chromium (``python -m playwright install chromium``) · the Chromium packaged in the npm module
@sparticuz/chromium (``cd e2e && npm install`` — for machines that cannot reach Playwright's download
server). ``E2E_HEADED=1`` shows the window.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "services" / "account"
WEB = ROOT / "apps" / "web"
WEB_DIST = WEB / "dist" / "web" / "browser"
E2E = ROOT / "e2e"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(url: str, timeout: float = 120, proc: subprocess.Popen | None = None) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(f"{url}: the process exited with code {proc.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=2) as res:
                if res.status < 500:
                    return
        except Exception:  # noqa: BLE001 - not up yet
            time.sleep(0.3)
    raise RuntimeError(f"{url} did not come up within {timeout} s")


class Stack:
    """Where everything runs, plus a few helpers the tests share."""

    def __init__(self, api: str, staff: str, admin: str, data_dir: Path, database_url: str, web: bool):
        self.api, self.staff, self.admin = api, staff, admin
        self.data_dir, self.database_url, self.web = data_dir, database_url, web

    def get(self, path: str) -> dict:
        with urllib.request.urlopen(self.api + path, timeout=10) as res:
            return json.loads(res.read())

    def outbox(self, to: str) -> list[dict]:
        """What the built-in e-mail / SMS fake "sent" to ``to`` (newest first)."""
        return self.get(f"/api/v1/messages/fake/outbox.json?to={urllib.request.quote(to)}")["messages"]

    def service_python(self, code: str) -> str:
        """Run ``code`` with the service's modules against the stack's database (e.g. to put a visit on
        the agenda that already took place — the booking page only books future slots)."""
        env = {**os.environ, "DATABASE_URL": self.database_url, "DATA_DIR": str(self.data_dir), "APP_ENV": "test"}
        env["TOKEN_SECRET"] = "dummy-e2e-token-secret"
        out = subprocess.run(
            [sys.executable, "-c", code], cwd=str(SERVICE), env=env, capture_output=True, text=True, check=False
        )
        if out.returncode:
            raise RuntimeError(out.stderr)
        return out.stdout.strip()

    def visit_today(self, minutes_ago: int = 45, provider_email: str = "dummy-doctor@example.com") -> int:
        """A visit of Pat Dummy with the doctor that started ``minutes_ago`` minutes ago. Returns its id."""
        code = f"""
import json
from datetime import datetime, timedelta, timezone
import models
from database import SessionLocal
with SessionLocal() as db:
    pat = db.query(models.Patient).filter_by(email_norm="dummy-patient@example.com").one()
    doc = db.query(models.Worker).filter_by(email_norm="{provider_email}").one()
    start = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes={minutes_ago})
    a = models.Appointment(patient_id=pat.id, provider_id=doc.id, start_at=start, end_at=start + timedelta(minutes=30),
                           status="scheduled", reason="Sore throat (dummy e2e visit)")
    db.add(a)
    db.commit()
    print(a.id)
"""
        return int(self.service_python(code))


def _stale(index: Path) -> bool:
    """Is the build older than any source / config file of the app?"""
    built = index.stat().st_mtime
    sources = [*(WEB / "src").rglob("*"), *(WEB / "public").rglob("*"), WEB / "angular.json", WEB / "release.json"]
    return any(p.is_file() and p.stat().st_mtime > built for p in sources if p.name != "release-info.ts")


def _build_web() -> bool:
    """The patients' app is served by the API from its production build; (re)built when missing or older
    than the sources."""
    index = WEB_DIST / "index.html"
    if index.is_file() and not _stale(index):
        return True
    npm = shutil.which("npm")
    if not npm or not (WEB / "node_modules").is_dir():
        return False
    code = subprocess.call([npm, "run", "build"], cwd=str(WEB), env={**os.environ, "NG_CLI_ANALYTICS": "false"})
    return code == 0 and (WEB_DIST / "index.html").is_file()


@pytest.fixture(scope="session")
def stack(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("e2e-data")
    database_url = f"sqlite:///{(data_dir / 'e2e.db').as_posix()}"
    api_port, staff_port, admin_port = free_port(), free_port(), free_port()
    api = f"http://127.0.0.1:{api_port}"
    staff = f"http://127.0.0.1:{staff_port}"
    admin = f"http://127.0.0.1:{admin_port}"
    web = _build_web()
    env = {
        **os.environ,
        "APP_ENV": "test",
        "DATA_DIR": str(data_dir),
        "DATABASE_URL": database_url,
        "TOKEN_SECRET": "dummy-e2e-token-secret",
        "SEED_FAKE_ACCOUNTS": "true",
        "REMINDER_CHECK_SECONDS": "0",
        "CORS_ORIGINS": f"{staff},{admin}",
        "STAFF_URL": f"{staff}/",
        "ADMIN_DASHBOARD_URL": f"{admin}/",
        "WEB_APP_URL": f"{api}/",
        "PYTHONUTF8": "1",
    }
    if web:
        env["WEB_DIST"] = str(WEB_DIST)
    log = open(data_dir / "stack.log", "w", encoding="utf-8")  # noqa: SIM115 - closed at the end
    procs = [
        subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(api_port)],
            cwd=str(SERVICE),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        ),
        subprocess.Popen(
            [sys.executable, str(ROOT / "apps" / "staff" / "serve.py"), "--port", str(staff_port), "--api", api],
            stdout=log,
            stderr=subprocess.STDOUT,
        ),
        subprocess.Popen(
            [sys.executable, str(ROOT / "apps" / "admin" / "serve.py"), "--port", str(admin_port), "--api", api],
            stdout=log,
            stderr=subprocess.STDOUT,
        ),
    ]
    try:
        wait_for(f"{api}/api/v1/health", proc=procs[0])
        wait_for(f"{staff}/", proc=procs[1])
        wait_for(f"{admin}/", proc=procs[2])
        yield Stack(api, staff, admin, data_dir, database_url, web)
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
        log.close()


def _launch_options() -> dict:
    options: dict = {"headless": not os.environ.get("E2E_HEADED")}
    if os.environ.get("E2E_BROWSER_PATH"):
        options["executable_path"] = os.environ["E2E_BROWSER_PATH"]
        return options
    return options


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import Error, sync_playwright

    with sync_playwright() as p:
        options = _launch_options()
        try:
            b = p.chromium.launch(**options)
        except Error:
            # No Playwright browser: the Chromium inside the npm package (cd e2e && npm install).
            node = shutil.which("node")
            if not node or not (E2E / "node_modules" / "@sparticuz").is_dir():
                raise
            info = json.loads(
                subprocess.run(
                    [node, "extract-chromium.mjs"], cwd=str(E2E), capture_output=True, text=True, check=True
                ).stdout
            )
            b = p.chromium.launch(**options, executable_path=info["path"], args=info["args"])
        yield b
        b.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    pg = context.new_page()
    pg.set_default_timeout(20_000)
    yield pg
    context.close()


def staff_sign_in(page, stack, email: str, password: str) -> None:
    page.goto(f"{stack.staff}/#/login")
    page.fill("#login-email", email)
    page.fill("#login-password", password)
    page.click("#btn-login")
    page.wait_for_selector("#btn-logout")


def admin_sign_in(page, stack, email: str = "dummy-admin@example.com", password: str = "Dummy-Admin-pass-123") -> None:
    page.goto(f"{stack.admin}/#/login")
    page.fill("#login-email", email)
    page.fill("#login-password", password)
    page.click("#btn-login")
    page.wait_for_selector("#btn-logout")


def draw_signature(page, selector: str = "canvas.sig-canvas") -> None:
    canvas = page.locator(selector)
    canvas.scroll_into_view_if_needed()
    box = canvas.bounding_box()
    x, y = box["x"] + 30, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    for i in range(1, 30):
        page.mouse.move(x + i * 10, y + (15 if i % 2 else -15))
    page.mouse.up()

"""Outgoing messages — e-mail and text messages (SMS) — for two-factor codes.

Two channels, each a small strategy with a real provider and a built-in fake:

    channel  real provider                                  stand-in when it is not configured
    email    SMTP (smtplib: any mail server / SendGrid /   FakeOutbox — outside prod only
             Mailgun / SES / Microsoft 365 …)
    sms      Twilio REST API (urllib, no SDK)              FakeOutbox — outside prod only

Everything is standard library and behaves the same on Windows, macOS and Linux (and in the
containers). No provider SDK is required; the one seam each real adapter has (``smtp_factory``
for e-mail, ``transport`` for Twilio) is what the tests replace with stubs.

The **fake outbox** (config.MESSAGING_FAKE_PROVIDERS: on in dev/test, opt-in for qa/staging,
never in prod — its inbox page shows codes to whoever opens it) keeps the
last messages in ``DATA_DIR/message_outbox.json`` (so they survive API restarts and the
``python dev.py outbox`` command can read them) and serves them under
``/api/v1/messages/fake/outbox`` — a page that works in any browser, phone and tablet included,
so a tester signing in on a phone can read the code on the same phone. Each fake delivery is
also printed to the API log. Nothing leaves the machine.
"""

from __future__ import annotations

import base64
import html
import json
import logging
import os
import re
import secrets
import shutil
import smtplib
import ssl
import threading
import urllib.parse
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse

import config
import httpclient

log = logging.getLogger("account.messaging")

EMAIL = "email"
SMS = "sms"
CHANNELS = (EMAIL, SMS)
OUTBOX_PATH = "/api/v1/messages/fake/outbox"


class MessagingError(Exception):
    """The provider could not take the message (unreachable, refused, misconfigured)."""


@dataclass
class Attachment:
    """A file sent with an e-mail (e.g. a prescription PDF to a pharmacy). Text messages carry none."""

    filename: str
    content_type: str
    data: bytes


@dataclass
class Message:
    channel: str  # email | sms
    to: str
    body: str
    subject: str = ""
    id: str = field(default_factory=lambda: secrets.token_hex(8))
    sent_at: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    provider: str = ""
    # What was attached: [{filename, content_type, size, url}] (the url: the fake outbox's copy).
    attachments: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------- the fake


class FakeOutbox:
    """Where messages go while no provider is configured. Thread-safe; the JSON file is
    replaced atomically (``os.replace`` — also atomic on Windows) so a reader never sees half a file."""

    KEEP = 200

    def __init__(self) -> None:
        self._lock = threading.Lock()

    @property
    def path(self):
        return config.DATA_DIR / "message_outbox.json"

    @property
    def files_dir(self):
        return config.DATA_DIR / "message_outbox_files"

    def file_path(self, message_id: str, filename: str):
        """Where the fake keeps an attachment — None for names that could leave the folder."""
        if not re.fullmatch(r"[0-9a-f]{16}", message_id or "") or not re.fullmatch(r"[\w.\- ]{1,120}", filename or ""):
            return None
        if filename.startswith("."):
            return None
        return self.files_dir / message_id / filename

    def _read(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [m for m in data if isinstance(m, dict)] if isinstance(data, list) else []

    def _write(self, messages: list[dict[str, Any]]) -> None:
        try:
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(messages[-self.KEEP :], indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
            for dropped in messages[: -self.KEEP] if len(messages) > self.KEEP else []:
                shutil.rmtree(self.files_dir / str(dropped.get("id", "")), ignore_errors=True)
        except OSError as exc:  # pragma: no cover - read-only data dir
            log.warning("could not write the fake outbox: %s", exc)

    def deliver(self, message: Message, files: list[Attachment] | None = None) -> str:
        message.provider = f"fake-{message.channel}"
        for f in files or []:
            target = self.file_path(message.id, f.filename)
            if target is None:
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(f.data)
            except OSError as exc:  # pragma: no cover - read-only data dir
                log.warning("could not keep the attachment %s: %s", f.filename, exc)
        with self._lock:
            messages = self._read()
            messages.append(asdict(message))
            self._write(messages)
        what = f"e-mail to {message.to}: {message.subject}" if message.channel == EMAIL else f"SMS to {message.to}"
        # The API log is the quickest place a developer looks (any OS, any terminal — a legacy
        # Windows code page gets "?" instead of a crash).
        line = f"[fake {message.channel}] {what} - {message.body.splitlines()[0]}"
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            print(line.encode("ascii", "replace").decode("ascii"), flush=True)
        return message.id

    def messages(self, *, to: str | None = None, channel: str | None = None) -> list[dict[str, Any]]:
        """Newest first."""
        with self._lock:
            found = self._read()
        if to:
            found = [m for m in found if m.get("to", "").lower() == to.lower()]
        if channel:
            found = [m for m in found if m.get("channel") == channel]
        return list(reversed(found))

    def latest(self, *, to: str | None = None, channel: str | None = None) -> dict[str, Any] | None:
        found = self.messages(to=to, channel=channel)
        return found[0] if found else None

    def clear(self) -> None:
        with self._lock:
            self._write([])
            shutil.rmtree(self.files_dir, ignore_errors=True)


OUTBOX = FakeOutbox()


# ---------------------------------------------------------------------------- real providers


def _smtp_connect() -> smtplib.SMTP:
    context = ssl.create_default_context()
    if config.SMTP_SECURITY == "ssl":
        server: smtplib.SMTP = smtplib.SMTP_SSL(
            config.SMTP_HOST, config.SMTP_PORT, timeout=config.SMTP_TIMEOUT_SECONDS, context=context
        )
    else:
        server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=config.SMTP_TIMEOUT_SECONDS)
        if config.SMTP_SECURITY == "starttls":
            server.starttls(context=context)
    if config.SMTP_USERNAME:
        server.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
    return server


class HttpTransport(httpclient.HttpTransport):
    """The shared client, with the provider's unreachability reported as a MessagingError."""

    def request(self, method, url, *, headers=None, data=None):
        try:
            return super().request(method, url, headers=headers, data=data)
        except httpclient.TransportError as exc:
            raise MessagingError(f"the text-message provider could not be reached ({exc})") from exc


# ---------------------------------------------------------------------------- the strategies


class Channel:
    """One way of reaching the user. ``available`` = the real provider is configured or the
    fake stands in; ``fake`` = the fake answers (no message leaves the machine)."""

    name = "base"
    label = ""

    def configured(self) -> bool:  # the real provider has what it needs
        raise NotImplementedError

    @property
    def fake(self) -> bool:
        return not self.configured() and config.MESSAGING_FAKE_PROVIDERS

    @property
    def available(self) -> bool:
        return self.configured() or self.fake

    def send(self, to: str, body: str, subject: str = "", attachments: list[Attachment] | None = None) -> Message:
        """Send the message; ``attachments`` go with an e-mail (a text message carries none)."""
        if not self.available:
            raise MessagingError(f"{self.label} is not set up on this server")
        files = list(attachments or []) if self.name == EMAIL else []
        message = Message(channel=self.name, to=to, body=body, subject=subject)
        message.attachments = [
            {
                "filename": f.filename,
                "content_type": f.content_type,
                "size": len(f.data),
                "url": f"{OUTBOX_PATH}/{message.id}/files/{urllib.parse.quote(f.filename)}" if self.fake else None,
            }
            for f in files
        ]
        if self.fake:
            OUTBOX.deliver(message, files)
        else:
            message.provider = self._deliver(message, files)
        return message

    def _deliver(self, message: Message, attachments: list[Attachment]) -> str:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        return {"channel": self.name, "label": self.label, "available": self.available, "fake": self.fake}


class EmailChannel(Channel):
    name = EMAIL
    label = "E-mail"

    def __init__(self, smtp_factory: Callable[[], smtplib.SMTP] = _smtp_connect):
        self.smtp_factory = smtp_factory

    def configured(self) -> bool:
        return bool(config.SMTP_HOST)

    def _deliver(self, message: Message, attachments: list[Attachment]) -> str:
        mail = EmailMessage()
        mail["From"] = config.EMAIL_FROM
        mail["To"] = message.to
        mail["Subject"] = message.subject
        mail["Message-ID"] = make_msgid(domain=(config.EMAIL_FROM.rsplit("@", 1)[-1].strip(" >") or None))
        mail["Auto-Submitted"] = "auto-generated"  # RFC 3834: no out-of-office replies
        mail.set_content(message.body)
        for f in attachments:
            maintype, _, subtype = f.content_type.partition("/")
            mail.add_attachment(f.data, maintype=maintype, subtype=subtype or "octet-stream", filename=f.filename)
        try:
            server = self.smtp_factory()
            try:
                server.send_message(mail)
            finally:
                try:
                    server.quit()
                except (smtplib.SMTPException, OSError):
                    pass
        except (smtplib.SMTPException, OSError) as exc:
            raise MessagingError(f"the mail server refused or could not be reached ({exc})") from exc
        return "smtp"


class SmsChannel(Channel):
    """Twilio Programmable Messaging (``POST /2010-04-01/Accounts/{sid}/Messages.json``)."""

    name = SMS
    label = "Text message"

    def __init__(self, transport: HttpTransport | None = None):
        self.transport = transport or HttpTransport()

    def configured(self) -> bool:
        return bool(
            config.TWILIO_ACCOUNT_SID
            and config.TWILIO_AUTH_TOKEN
            and (config.TWILIO_FROM or config.TWILIO_MESSAGING_SERVICE_SID)
        )

    def _deliver(self, message: Message, attachments: list[Attachment]) -> str:
        fields = {"To": message.to, "Body": message.body}
        if config.TWILIO_MESSAGING_SERVICE_SID:
            fields["MessagingServiceSid"] = config.TWILIO_MESSAGING_SERVICE_SID
        else:
            fields["From"] = config.TWILIO_FROM
        auth = base64.b64encode(f"{config.TWILIO_ACCOUNT_SID}:{config.TWILIO_AUTH_TOKEN}".encode()).decode()
        status, body = self.transport.request(
            "POST",
            f"{config.TWILIO_API_BASE}/2010-04-01/Accounts/{urllib.parse.quote(config.TWILIO_ACCOUNT_SID)}/Messages.json",
            headers={"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"},
            data=urllib.parse.urlencode(fields).encode(),
        )
        if status >= 400:
            raise MessagingError(f"the text-message provider refused the message ({body.get('message') or status})")
        return str(body.get("sid") or "twilio")


EMAIL_CHANNEL = EmailChannel()
SMS_CHANNEL = SmsChannel()


def channel(name: str) -> Channel:
    if name == EMAIL:
        return EMAIL_CHANNEL
    if name == SMS:
        return SMS_CHANNEL
    raise ValueError(name)


def outbox_url() -> str | None:
    """Where a fake delivery can be read (only while the fake is on)."""
    return OUTBOX_PATH if config.MESSAGING_FAKE_PROVIDERS else None


# ---------------------------------------------------------------------------- the fake's pages

router = APIRouter(prefix="/messages/fake", include_in_schema=False)


def _guard() -> None:
    if not config.MESSAGING_FAKE_PROVIDERS or config.IS_PROD:
        raise HTTPException(status_code=404)


@router.get("/outbox.json")
def outbox_json(to: str | None = Query(default=None), channel: str | None = Query(default=None)):
    """The fake's messages, newest first (end-to-end tests read codes here)."""
    _guard()
    return {"messages": OUTBOX.messages(to=to, channel=channel)}


@router.post("/outbox/clear")
def outbox_clear():
    _guard()
    OUTBOX.clear()
    return {"ok": True}


@router.get("/outbox/{message_id}/files/{filename}")
def outbox_file(message_id: str, filename: str):
    """An attachment of a message the fake "sent" (e.g. a prescription PDF e-mailed to a pharmacy)."""
    _guard()
    target = OUTBOX.file_path(message_id, filename)
    if target is None or not target.is_file():
        raise HTTPException(status_code=404)
    media = next(
        (
            a.get("content_type")
            for m in OUTBOX.messages()
            if m.get("id") == message_id
            for a in m.get("attachments", [])
            if a.get("filename") == filename
        ),
        "application/octet-stream",
    )
    return FileResponse(target, media_type=media, filename=filename, headers={"Cache-Control": "no-store"})


@router.get("/outbox", response_class=HTMLResponse)
def outbox_page(to: str | None = Query(default=None)):
    """A test inbox for every e-mail and text the fake "sent". Refreshes itself; sized for phones."""
    _guard()
    rows = []
    for m in OUTBOX.messages(to=to)[:50]:
        icon = "✉️" if m.get("channel") == EMAIL else "💬"
        subject = f"<div class=subj>{html.escape(m.get('subject') or '')}</div>" if m.get("subject") else ""
        files = "".join(
            f'<div class=att>📎 <a href="{html.escape(a["url"])}" target=_blank>'
            f"{html.escape(a.get('filename', ''))}</a> ({int(a.get('size') or 0) // 1024 + 1} KB)</div>"
            for a in m.get("attachments") or []
            if a.get("url")
        )
        rows.append(
            f"<li class=msg data-channel='{html.escape(m.get('channel', ''))}'>"
            f"<div class=meta>{icon} <strong>{html.escape(m.get('to', ''))}</strong> · "
            f"{html.escape(m.get('sent_at', ''))}</div>{subject}"
            f"<pre>{html.escape(m.get('body', ''))}</pre>{files}</li>"
        )
    body = "".join(rows) or "<li class=empty>No messages yet. Ask for a code in the app; it shows up here.</li>"
    return HTMLResponse(
        f"""<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta http-equiv=refresh content=5>
<title>Test inbox — built-in fake</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:640px;margin:0 auto;padding:16px;line-height:1.45;color:#111827}}
.note{{background:#fff3cd;border:1px solid #ffe69c;padding:8px 12px;border-radius:8px;font-size:14px}}
ul{{list-style:none;padding:0;display:grid;gap:10px}}
.msg{{border:1px solid #e2e5ea;border-radius:10px;padding:10px 12px;background:#fff}}
.meta{{font-size:13px;color:#6b7280;overflow-wrap:anywhere}} .subj{{font-weight:600;margin-top:4px}}
.att{{margin-top:6px;font-size:14px}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:15px/1.45 ui-monospace,Menlo,Consolas,monospace;margin:6px 0 0}}
@media (prefers-color-scheme:dark){{body{{background:#111318;color:#e5e7eb}}
.msg{{background:#1a1d24;border-color:#2a2f3a}}}}
</style>
<h2>Test inbox</h2>
<p class=note><strong>Built-in fake.</strong> No e-mail server or text-message provider is configured,
so nothing was sent — these are the messages the app would have delivered (newest first, refreshes
every 5 s). Configure SMTP / Twilio and the real ones are used instead.</p>
<ul id=messages>{body}</ul></html>"""
    )

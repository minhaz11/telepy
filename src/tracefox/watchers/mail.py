from __future__ import annotations

import email
from contextvars import ContextVar
from email import policy
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..entry import EntryType, IncomingEntry
from ..utils import ensure_list
from .base import Watcher, safely

if TYPE_CHECKING:
    from ..core import Tracefox

_inside: ContextVar[bool] = ContextVar("tracefox_mail_inside", default=False)


def _addresses(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [a.strip() for a in value.split(",") if a.strip()]
    return [str(v) for v in ensure_list(value)]


def parse_raw(raw: str) -> Dict[str, Any]:
    message = email.message_from_string(raw, policy=policy.default)
    html: Optional[str] = None
    text: Optional[str] = None
    attachments = []
    for part in message.walk() if message.is_multipart() else [message]:
        if part.is_multipart():
            continue
        if part.get_content_disposition() == "attachment":
            attachments.append(part.get_filename())
            continue
        try:
            body = part.get_content()
        except Exception:
            continue
        if part.get_content_type() == "text/html" and html is None:
            html = body
        elif part.get_content_type() == "text/plain" and text is None:
            text = body
    return {
        "from": _addresses(message.get("From")),
        "replyTo": _addresses(message.get("Reply-To")),
        "to": _addresses(message.get("To")),
        "cc": _addresses(message.get("Cc")),
        "bcc": _addresses(message.get("Bcc")),
        "subject": str(message.get("Subject") or ""),
        "html": html if html is not None else _text_to_html(text or ""),
        "attachments": attachments,
    }


def _text_to_html(text: str) -> str:
    import html as _html

    return f"<pre style=\"white-space: pre-wrap; font-family: inherit\">{_html.escape(text)}</pre>"


class MailWatcher(Watcher):
    """Outgoing mail: Django email backends, smtplib (Flask-Mail, plain SMTP), aiosmtplib (fastapi-mail)."""

    name = "mail"

    def register(self) -> None:
        self.when_imported("django.core.mail", self._register_django)
        self.when_imported("smtplib", self._register_smtplib)
        self.when_imported("aiosmtplib", self._register_aiosmtplib)

    @staticmethod
    def record_content(tracefox: "Tracefox", content: Dict[str, Any]) -> None:
        watcher = tracefox.watcher("mail")
        if watcher is not None:
            watcher.record(content)

    @safely
    def record(self, content: Dict[str, Any]) -> None:
        if not self.active:
            return
        data = {
            "mailable": content.get("mailable"),
            "queued": bool(content.get("queued", False)),
            "from": _addresses(content.get("from")),
            "replyTo": _addresses(content.get("replyTo") or content.get("reply_to")),
            "to": _addresses(content.get("to")),
            "cc": _addresses(content.get("cc")),
            "bcc": _addresses(content.get("bcc")),
            "subject": content.get("subject") or "",
            "html": content.get("html") or "",
            "raw": content.get("raw") or "",
            "attachments": content.get("attachments") or [],
        }
        entry = IncomingEntry(EntryType.MAIL, data)
        entry.add_tags(data["to"])
        if data["mailable"]:
            entry.add_tags([data["mailable"]])
        self.tracefox.record(entry)

    def record_raw(self, raw: str, mailable: Optional[str] = None, **overrides: Any) -> None:
        content = parse_raw(raw)
        content.update({k: v for k, v in overrides.items() if v})
        content["raw"] = raw
        content["mailable"] = mailable
        self.record(content)

    # -- Django ----------------------------------------------------------------------

    def _register_django(self, mail: Any) -> None:
        watcher = self

        def record_message(message: Any) -> None:
            try:
                raw = message.message().as_string()
            except Exception:
                raw = ""
            html = None
            for alternative in getattr(message, "alternatives", []) or []:
                content, mimetype = (alternative[0], alternative[1]) if isinstance(alternative, tuple) else (alternative.content, alternative.mimetype)
                if mimetype == "text/html":
                    html = content
            if html is None:
                html = message.body if getattr(message, "content_subtype", "plain") == "html" else _text_to_html(message.body or "")
            watcher.record({
                "mailable": type(message).__name__,
                "from": message.from_email,
                "replyTo": message.reply_to,
                "to": message.to,
                "cc": message.cc,
                "bcc": message.bcc,
                "subject": message.subject,
                "html": html,
                "raw": raw,
                "attachments": [a[0] if isinstance(a, tuple) else getattr(a, "filename", None) or getattr(a, "name", str(a))
                                for a in getattr(message, "attachments", [])],
            })

        def make(original):
            def send_messages(self, messages, *args, **kwargs):
                if _inside.get():
                    return original(self, messages, *args, **kwargs)
                token = _inside.set(True)
                try:
                    result = original(self, messages, *args, **kwargs)
                finally:
                    _inside.reset(token)
                for message in list(messages or []):
                    record_message(message)
                return result
            return send_messages

        # Wrap every backend class (get_connection() and Django 6.1+ MAILERS both end up in send_messages).
        from django.core.mail.backends.base import BaseEmailBackend

        def wrap(cls):
            if "send_messages" in cls.__dict__:
                self.patch(cls, "send_messages", make)

        def subclasses(cls):
            for sub in cls.__subclasses__():
                yield sub
                yield from subclasses(sub)

        for backend in [BaseEmailBackend, *subclasses(BaseEmailBackend)]:
            wrap(backend)

        def make_init_subclass(_original):
            def __init_subclass__(cls, **kwargs):
                super(BaseEmailBackend, cls).__init_subclass__(**kwargs)
                wrap(cls)
            return classmethod(__init_subclass__)

        self.patch(BaseEmailBackend, "__init_subclass__", make_init_subclass)

    # -- smtplib / aiosmtplib --------------------------------------------------------

    def _register_smtplib(self, smtplib: Any) -> None:
        watcher = self

        def make_sendmail(original):
            def sendmail(self, from_addr, to_addrs, msg, *args, **kwargs):
                result = original(self, from_addr, to_addrs, msg, *args, **kwargs)
                if not _inside.get():
                    raw = msg.decode("utf-8", "replace") if isinstance(msg, bytes) else str(msg)
                    watcher.record_raw(raw, "SMTP", to=_addresses(to_addrs) if not parse_raw(raw)["to"] else None)
                return result
            return sendmail

        self.patch(smtplib.SMTP, "sendmail", make_sendmail)

    def _register_aiosmtplib(self, aiosmtplib: Any) -> None:
        watcher = self

        def make(original):
            async def send_message(self, message, *args, **kwargs):
                token = _inside.set(True)
                try:
                    result = await original(self, message, *args, **kwargs)
                finally:
                    _inside.reset(token)
                watcher.record_raw(message.as_string(), type(message).__name__)
                return result
            return send_message

        self.patch(aiosmtplib.SMTP, "send_message", make)

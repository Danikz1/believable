"""Brief delivery — Telegram and email delivery of intelligence briefs."""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sqlalchemy.orm import Session

from src.db.models import Briefs

logger = logging.getLogger(__name__)


def deliver_telegram(session: Session, brief: Briefs, bot_token: str, chat_id: str) -> bool:
    """Send brief to Telegram channel/chat."""
    import httpx

    # Convert markdown to Telegram-friendly format (truncate if needed)
    text = brief.content_markdown
    if len(text) > 4000:
        text = text[:3900] + "\n\n*... [Read full brief on dashboard]*"

    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        r = httpx.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }, timeout=30)
        r.raise_for_status()

        brief.delivered_telegram = True
        session.commit()
        logger.info(f"Brief delivered to Telegram: {chat_id}")
        return True
    except Exception as e:
        logger.error(f"Telegram delivery failed: {e}")
        return False


def deliver_email(
    session: Session,
    brief: Briefs,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_pass: str,
    from_addr: str,
    to_addrs: list[str],
) -> bool:
    """Send brief via email (SMTP)."""
    try:
        if not from_addr or not to_addrs:
            raise ValueError("Email delivery requires from_addr and at least one recipient")

        # Convert markdown to simple HTML
        html_body = _markdown_to_html(brief.content_markdown)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = brief.title
        msg["From"] = from_addr
        msg["To"] = ", ".join(to_addrs)

        # Plain text version
        msg.attach(MIMEText(brief.content_markdown, "plain"))
        # HTML version
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_addr, to_addrs, msg.as_string())

        brief.delivered_email = True
        session.commit()
        logger.info(f"Brief delivered via email to {len(to_addrs)} recipients")
        return True
    except Exception as e:
        logger.error(f"Email delivery failed: {e}")
        return False


def _markdown_to_html(md: str) -> str:
    """Simple markdown to HTML conversion."""
    import re

    html = md
    # Headers
    html = re.sub(r'^### (.+)$', r'<h3>\1</h3>', html, flags=re.MULTILINE)
    html = re.sub(r'^## (.+)$', r'<h2>\1</h2>', html, flags=re.MULTILINE)
    html = re.sub(r'^# (.+)$', r'<h1>\1</h1>', html, flags=re.MULTILINE)
    # Bold
    html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html)
    # Italic
    html = re.sub(r'\*(.+?)\*', r'<em>\1</em>', html)
    # Links
    html = re.sub(r'\[(.+?)\]\((.+?)\)', r'<a href="\2">\1</a>', html)
    # Blockquotes
    html = re.sub(r'^> (.+)$', r'<blockquote>\1</blockquote>', html, flags=re.MULTILINE)
    # List items
    html = re.sub(r'^- (.+)$', r'<li>\1</li>', html, flags=re.MULTILINE)
    # Paragraphs
    html = html.replace('\n\n', '</p><p>')
    html = f'<p>{html}</p>'

    return f"""
    <html>
    <head>
        <style>
            body {{ font-family: -apple-system, sans-serif; max-width: 700px; margin: 0 auto; padding: 20px; color: #1a1a1a; }}
            h1 {{ color: #1a56db; }}
            h2 {{ color: #374151; border-bottom: 1px solid #e5e7eb; padding-bottom: 8px; }}
            blockquote {{ border-left: 3px solid #3b82f6; padding-left: 12px; color: #4b5563; }}
            a {{ color: #2563eb; }}
            li {{ margin: 4px 0; }}
        </style>
    </head>
    <body>{html}</body>
    </html>
    """

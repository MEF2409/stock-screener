"""Send alerts to Slack/Discord webhooks and/or email when scan runs complete."""

import os
import smtplib
from datetime import datetime
from email.mime.text import MIMEText

import requests


SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")  # works for Discord too if you append /slack
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
ALERT_EMAIL_TO = os.getenv("ALERT_EMAIL_TO")
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")


def _format_slack(results: dict) -> dict:
    """Slack/Discord-compatible message payload."""
    total = sum(len(v) for v in results.values() if isinstance(v, list))
    blocks = [{
        "type": "header",
        "text": {"type": "plain_text", "text": f"📈 Market Pulse — {total} signals"},
    }]
    for scanner, label in (("runaway_gap", "🚀 Momentum"),
                           ("bullish_div", "🔄 Reversal"),
                           ("bearish_div", "⚠️ Caution"),
                           ("gap_up_normal_vol", "📉 Fade")):
        flags = results.get(scanner, [])
        if not flags:
            continue
        tickers = ", ".join(f["ticker"] for f in flags[:15])
        more = f" (+{len(flags) - 15} more)" if len(flags) > 15 else ""
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"*{label}* ({len(flags)})\n`{tickers}{more}`"},
        })
    return {"blocks": blocks, "text": f"Market Pulse: {total} signals today"}


def _format_text(results: dict) -> str:
    total = sum(len(v) for v in results.values() if isinstance(v, list))
    lines = [f"Market Pulse — {datetime.now().strftime('%Y-%m-%d')}",
             f"{total} total signals", ""]
    for scanner, label in (("runaway_gap", "Momentum"),
                           ("bullish_div", "Reversal"),
                           ("bearish_div", "Caution"),
                           ("gap_up_normal_vol", "Fade")):
        flags = results.get(scanner, [])
        lines.append(f"  {label} ({len(flags)}): {', '.join(f['ticker'] for f in flags) or '—'}")
    return "\n".join(lines)


def send_slack(results: dict) -> bool:
    if not SLACK_WEBHOOK_URL:
        return False
    try:
        r = requests.post(SLACK_WEBHOOK_URL, json=_format_slack(results), timeout=10)
        return r.ok
    except Exception as e:
        print(f"Slack alert failed: {e}")
        return False


def send_discord(results: dict) -> bool:
    if not DISCORD_WEBHOOK_URL:
        return False
    try:
        r = requests.post(DISCORD_WEBHOOK_URL, json={"content": "```\n" + _format_text(results) + "\n```"},
                          timeout=10)
        return r.ok
    except Exception as e:
        print(f"Discord alert failed: {e}")
        return False


def send_email(results: dict) -> bool:
    if not (ALERT_EMAIL_TO and SMTP_HOST and SMTP_USER and SMTP_PASSWORD):
        return False
    try:
        msg = MIMEText(_format_text(results))
        msg["Subject"] = f"Market Pulse: {sum(len(v) for v in results.values() if isinstance(v, list))} signals"
        msg["From"] = SMTP_USER
        msg["To"] = ALERT_EMAIL_TO
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASSWORD)
            s.send_message(msg)
        return True
    except Exception as e:
        print(f"Email alert failed: {e}")
        return False


def send_all(results: dict) -> dict:
    """Fire all configured alert channels. Returns {channel: success_bool}."""
    return {
        "slack": send_slack(results) if SLACK_WEBHOOK_URL else None,
        "discord": send_discord(results) if DISCORD_WEBHOOK_URL else None,
        "email": send_email(results) if ALERT_EMAIL_TO else None,
    }

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


_SETUP_META = (
    ("runaway_gap", "🚀 Momentum", "long"),
    ("bullish_div", "🔄 Reversal", "long"),
    ("bearish_div", "⚠️ Caution", "short"),
    ("gap_up_normal_vol", "📉 Fade", "short"),
)


def _gap_pct(flag: dict) -> float | None:
    """For gap-based setups: today's open vs prior close. Returns % or None."""
    o = flag.get("open")
    c = flag.get("close")  # for Fade scanner this is yesterday's close (gap reference)
    if o is None or c is None or c == 0:
        return None
    return (float(o) - float(c)) / float(c) * 100


def _format_signal_line(scanner_key: str, side: str, flag: dict) -> str:
    """One human-readable line per ticker with the most actionable fact."""
    parts = [f"*{flag['ticker']}*"]
    gap = _gap_pct(flag) if scanner_key in ("runaway_gap", "gap_up_normal_vol") else None
    if gap is not None:
        sign = "+" if gap >= 0 else ""
        parts.append(f"gap {sign}{gap:.1f}%")
    rsi = flag.get("rsi")
    if rsi is not None:
        parts.append(f"RSI {rsi:.0f}")
    if (flag.get("catalyst") or "").lower() == "earnings":
        parts.append("⚡ EARNINGS")
    return " · ".join(parts)


def _format_slack(results: dict) -> dict:
    """Slack/Discord-compatible message payload with per-ticker context."""
    total = sum(len(v) for v in results.values() if isinstance(v, list))
    blocks = [{
        "type": "header",
        "text": {"type": "plain_text", "text": f"📈 Market Pulse — {total} signals"},
    }]
    for scanner, label, side in _SETUP_META:
        flags = results.get(scanner, [])
        if not flags:
            continue
        # Sort: earnings catalysts first (most time-sensitive)
        flags = sorted(flags, key=lambda f: 0 if (f.get("catalyst") or "").lower() == "earnings" else 1)
        lines = [_format_signal_line(scanner, side, f) for f in flags[:15]]
        more = f"\n_…and {len(flags) - 15} more_" if len(flags) > 15 else ""
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"*{label}* ({len(flags)})\n" + "\n".join(lines) + more},
        })
    return {"blocks": blocks, "text": f"Market Pulse: {total} signals today"}


def _format_text(results: dict) -> str:
    total = sum(len(v) for v in results.values() if isinstance(v, list))
    lines = [f"Market Pulse — {datetime.now().strftime('%Y-%m-%d')}",
             f"{total} total signals", ""]
    for scanner, label, side in _SETUP_META:
        flags = results.get(scanner, [])
        lines.append(f"  {label.replace('🚀 ','').replace('🔄 ','').replace('⚠️ ','').replace('📉 ','')} ({len(flags)})")
        if not flags:
            continue
        flags = sorted(flags, key=lambda f: 0 if (f.get("catalyst") or "").lower() == "earnings" else 1)
        for f in flags[:20]:
            tag = " [EARNINGS]" if (f.get("catalyst") or "").lower() == "earnings" else ""
            gap = _gap_pct(f) if scanner in ("runaway_gap", "gap_up_normal_vol") else None
            gap_s = f" gap {('+' if gap >= 0 else '')}{gap:.1f}%" if gap is not None else ""
            lines.append(f"    - {f['ticker']}{gap_s}{tag}")
        if len(flags) > 20:
            lines.append(f"    …and {len(flags) - 20} more")
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

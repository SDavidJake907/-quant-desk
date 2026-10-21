"""Push an alert to the operator's phone through ntfy.sh.

ntfy (https://ntfy.sh) delivers a POST to everyone subscribed to a topic in
its app; no account, no key. The topic name is the only secret, so it lives
in .env (SCANNER_NTFY_TOPIC) and should be long and random.

An alert must never break the job that sends it: `send` swallows every
failure, prints it, and returns False.
"""

from __future__ import annotations

import os
import subprocess

import httpx

PRIORITIES = ("min", "low", "default", "high", "urgent")


def _ascii(text: str) -> str:
    """HTTP header values must be latin-1; keep titles plain."""
    return (text.replace("—", "-").replace("–", "-").replace("→", "->")
            .encode("latin-1", "replace").decode("latin-1"))


def request(server: str, topic: str, title: str, message: str, *,
            priority: str = "default", tags: tuple[str, ...] = ()) -> tuple[str, dict[str, str], bytes]:
    headers = {"Title": _ascii(title),
               "Priority": priority if priority in PRIORITIES else "default"}
    if tags:
        headers["Tags"] = ",".join(tags)
    return f"{server.rstrip('/')}/{topic}", headers, message.encode("utf-8")


async def send(client: httpx.AsyncClient, *, server: str, topic: str, title: str, message: str,
               priority: str = "default", tags: tuple[str, ...] = ()) -> bool:
    if not topic:
        print(f"  (no SCANNER_NTFY_TOPIC set; alert not pushed) {title}: {message}")
        return False
    url, headers, body = request(server, topic, title, message, priority=priority, tags=tags)
    try:
        response = await client.post(url, headers=headers, content=body, timeout=15.0)
        response.raise_for_status()
        return True
    except httpx.HTTPError as exc:
        print(f"  ! alert not delivered ({exc}): {title}")
        return False


#: A Windows notification on this PC; a stale alert clears itself after 8 hours.
TOAST_PS = (
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null; "
    "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
    "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
    "$x = $t.GetElementsByTagName('text'); $x.Item(0).AppendChild($t.CreateTextNode($env:HC_TITLE)) > $null; "
    "$x.Item(1).AppendChild($t.CreateTextNode($env:HC_BODY)) > $null; "
    "$n = [Windows.UI.Notifications.ToastNotification]::new($t); "
    "$n.ExpirationTime = [DateTimeOffset]::Now.AddHours(8); "   # a stale alert clears itself
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
    "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe').Show($n)")


def toast(title: str, body: str) -> bool:
    """Show a Windows notification. Like `send`, never raises."""
    env = {**os.environ, "HC_TITLE": title, "HC_BODY": body[:250]}
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", TOAST_PS],
                       check=False, env=env, capture_output=True, timeout=30)
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"  (windows notification failed: {exc})")
        return False

"""Push an alert to the operator's phone through ntfy.sh.

ntfy (https://ntfy.sh) delivers a POST to everyone subscribed to a topic in
its app; no account, no key. The topic name is the only secret, so it lives
in .env (SCANNER_NTFY_TOPIC) and should be long and random.

An alert must never break the job that sends it: `send` swallows every
failure, prints it, and returns False.
"""

from __future__ import annotations

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

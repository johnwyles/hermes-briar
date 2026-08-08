import os
import json
import asyncio
import aiohttp
from typing import Optional, Dict, Any, List
from gateway.platforms.base import (
    BasePlatformAdapter,
    SendResult,
    MessageEvent,
    MessageType,
)
from gateway.config import Platform, PlatformConfig


class BriarAdapter(BasePlatformAdapter):
    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform("briar"))
        extra = config.extra or {}
        self.api_url = os.getenv("BRIAR_API_URL", extra.get("api_url", ""))
        self.contact_id = os.getenv("BRIAR_CONTACT_ID", extra.get("contact_id", ""))
        self.allowed_users = self._parse_allowed_users()
        self._session: Optional[aiohttp.ClientSession] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._last_timestamp: float = 0.0

    def _parse_allowed_users(self) -> List[str]:
        raw = os.getenv("BRIAR_ALLOWED_USERS", "")
        if not raw:
            return []
        return [u.strip() for u in raw.split(",") if u.strip()]

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        if not self.api_url or not self.contact_id:
            return False
        try:
            session = aiohttp.ClientSession()
            # Verify bridge reachability
            async with session.get(f"{self.api_url}/status", timeout=5) as resp:
                if resp.status != 200:
                    await session.close()
                    return False
            self._session = session
            self._mark_connected()
            self._poll_task = asyncio.create_task(self._poll_messages())
            return True
        except Exception:
            self._session = None
            return False

    async def disconnect(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        await self._safe_disconnect()
        self._mark_disconnected()

    async def _safe_disconnect(self):
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        if not self._session or self._session.closed:
            return SendResult(success=False, error="not connected")
        chat = chat_id or self.contact_id
        payload = {
            "contact_id": chat,
            "message": content,
            "reply_to": reply_to,
        }
        try:
            async with self._session.post(
                f"{self.api_url}/send",
                json=payload,
                timeout=15,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return SendResult(success=True, message_id=str(data.get("id", "")))
                text = await resp.text()
                return SendResult(success=False, error=f"HTTP {resp.status}: {text}")
        except Exception as e:
            return SendResult(success=False, error=str(e))

    async def send_typing(self, chat_id):
        # Briar has no typing indicator in this minimal bridge
        return None

    async def get_chat_info(self, chat_id):
        return {
            "name": chat_id or self.contact_id,
            "type": "dm",
            "chat_id": chat_id or self.contact_id,
        }

    async def _poll_messages(self):
        backoff = 1
        while True:
            try:
                if not self._session or self._session.closed:
                    await asyncio.sleep(backoff)
                    continue
                params = {"contact_id": self.contact_id}
                if self._last_timestamp:
                    params["since"] = str(self._last_timestamp)
                async with self._session.get(
                    f"{self.api_url}/messages",
                    params=params,
                    timeout=20,
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        messages = data if isinstance(data, list) else data.get("messages", [])
                        new_messages = [m for m in messages if m.get("timestamp", 0) > self._last_timestamp]
                        for msg in new_messages:
                            await self._handle_incoming(msg)
                        if new_messages:
                            self._last_timestamp = max(
                                self._last_timestamp,
                                max(m.get("timestamp", 0) for m in new_messages),
                            )
                        backoff = 1
                    else:
                        backoff = min(backoff * 2, 60)
                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _handle_incoming(self, msg: Dict[str, Any]):
        text = msg.get("message") or msg.get("text") or ""
        sender = msg.get("contact_id") or msg.get("sender") or ""
        if not text:
            return
        if self.allowed_users and sender not in self.allowed_users:
            return
        event = MessageEvent(
            platform=Platform("briar"),
            chat_id=sender,
            sender_id=sender,
            text=text,
            message_type=MessageType.TEXT,
            raw=msg,
            timestamp=msg.get("timestamp"),
        )
        await self.dispatch(event)


def check_requirements() -> bool:
    return bool(os.getenv("BRIAR_API_URL")) and bool(os.getenv("BRIAR_CONTACT_ID"))


def validate_config(config) -> bool:
    extra = getattr(config, "extra", {}) or {}
    api_url = os.getenv("BRIAR_API_URL", extra.get("api_url", ""))
    contact_id = os.getenv("BRIAR_CONTACT_ID", extra.get("contact_id", ""))
    return bool(api_url and contact_id)


def _env_enablement() -> Optional[Dict[str, Any]]:
    api_url = os.getenv("BRIAR_API_URL", "").strip()
    contact_id = os.getenv("BRIAR_CONTACT_ID", "").strip()
    home = os.getenv("BRIAR_HOME_CHANNEL", "").strip()
    if not (api_url and contact_id):
        return None
    seed: Dict[str, Any] = {"api_url": api_url, "contact_id": contact_id}
    if home:
        seed["home_channel"] = {"chat_id": home, "name": "Home"}
    return seed


def register(ctx):
    ctx.register_platform(
        name="briar",
        label="Briar",
        adapter_factory=lambda cfg: BriarAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        required_env=["BRIAR_API_URL", "BRIAR_CONTACT_ID"],
        install_hint="Provide a Briar API bridge reachable at BRIAR_API_URL",
        env_enablement_fn=_env_enablement,
        cron_deliver_env_var="BRIAR_HOME_CHANNEL",
        allowed_users_env="BRIAR_ALLOWED_USERS",
        allow_all_env="",
        max_message_length=4000,
        platform_hint="You are chatting via Briar. Keep replies concise.",
        emoji="🪐",
    )

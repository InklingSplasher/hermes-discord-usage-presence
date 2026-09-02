"""Discord presence controller for Codex or Claude account usage.

The Discord SDK is deliberately imported only while building an activity. The
Hermes Discord adapter owns that dependency and supplies its native ``Bot`` to
``attach`` through the public plugin platform-handler API.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from typing import Any, Optional


logger = logging.getLogger(__name__)

DEFAULT_PROVIDER = "codex"
PROVIDER_IDS = {"codex": "openai-codex", "claude": "anthropic"}
PROVIDER_ALIASES = {
    "codex": "codex",
    "openai-codex": "codex",
    "claude": "claude",
    "anthropic": "claude",
}

DEFAULT_WINDOW = "auto"
SUPPORTED_WINDOWS = {
    "auto",
    "session",
    "weekly",
    "fable_week",
    "opus_week",
    "sonnet_week",
}
WINDOW_LABELS = {
    "session": ("session", "Session"),
    "weekly": ("weekly", "Weekly"),
    "current session": ("session", "Current session"),
    "current week": ("weekly", "Current week"),
    "fable week": ("fable_week", "Fable week"),
    "current week (fable)": ("fable_week", "Fable week"),
    "opus week": ("opus_week", "Opus week"),
    "current week (opus)": ("opus_week", "Opus week"),
    "sonnet week": ("sonnet_week", "Sonnet week"),
    "current week (sonnet)": ("sonnet_week", "Sonnet week"),
}
AUTO_WINDOW_FALLBACKS = {
    "codex": ("session", "weekly"),
    "claude": ("fable_week", "opus_week", "weekly", "session", "sonnet_week"),
}
WINDOW_FALLBACKS = {
    "session": ("session", "weekly", "fable_week", "opus_week", "sonnet_week"),
    "weekly": ("weekly", "fable_week", "opus_week", "sonnet_week", "session"),
    "fable_week": ("fable_week", "weekly", "session", "opus_week", "sonnet_week"),
    "opus_week": ("opus_week", "weekly", "session", "fable_week", "sonnet_week"),
    "sonnet_week": ("sonnet_week", "weekly", "session", "fable_week", "opus_week"),
}

DEFAULT_INTERVAL_SECONDS = 300
MIN_INTERVAL_SECONDS = 60
DEFAULT_BAR_WIDTH = 10
MIN_BAR_WIDTH = 1
MAX_BAR_WIDTH = 30


def _integer_setting(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


@dataclass(frozen=True)
class PresenceSettings:
    """Validated, startup-snapshotted plugin settings."""

    window: str = DEFAULT_WINDOW
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    bar_width: int = DEFAULT_BAR_WIDTH
    provider: str = DEFAULT_PROVIDER

    @classmethod
    def from_context(cls, ctx: Any) -> "PresenceSettings":
        raw_provider = ctx.get_config("provider", DEFAULT_PROVIDER)
        provider = PROVIDER_ALIASES.get(
            str(raw_provider or "").strip().lower(), DEFAULT_PROVIDER
        )

        raw_window = ctx.get_config("window", DEFAULT_WINDOW)
        window = str(raw_window or "").strip().lower()
        if window not in SUPPORTED_WINDOWS:
            window = DEFAULT_WINDOW

        interval = _integer_setting(
            ctx.get_config("interval_seconds", DEFAULT_INTERVAL_SECONDS),
            DEFAULT_INTERVAL_SECONDS,
        )
        interval = max(MIN_INTERVAL_SECONDS, interval)

        bar_width = _integer_setting(
            ctx.get_config("bar_width", DEFAULT_BAR_WIDTH),
            DEFAULT_BAR_WIDTH,
        )
        bar_width = max(MIN_BAR_WIDTH, min(MAX_BAR_WIDTH, bar_width))
        return cls(
            window=window,
            interval_seconds=interval,
            bar_width=bar_width,
            provider=provider,
        )


@dataclass
class _BotState:
    bot: Any
    on_ready: Any
    on_disconnect: Any
    on_resumed: Any = None
    task: Optional[asyncio.Task] = None
    generation: int = 0
    last_good_text: Optional[str] = None


def _finite_percent(value: Any) -> Optional[int]:
    """Return a rounded 0..100 percentage, rejecting booleans and non-finite values."""
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(numeric):
        return None
    return int(round(max(0.0, min(100.0, numeric))))


def select_usage_window(
    snapshot: Any,
    preference: str,
    provider: str = DEFAULT_PROVIDER,
) -> Optional[tuple[str, int]]:
    """Select a valid Codex/Claude window using the configured fallback order."""
    valid: dict[str, tuple[str, int]] = {}
    for window in getattr(snapshot, "windows", ()) or ():
        label = str(getattr(window, "label", "") or "").strip()
        window_info = WINDOW_LABELS.get(label.casefold())
        if window_info is None:
            continue
        percent = _finite_percent(getattr(window, "used_percent", None))
        if percent is not None:
            key, canonical = window_info
            valid[key] = (canonical, percent)

    provider_key = PROVIDER_ALIASES.get(str(provider).strip().lower(), DEFAULT_PROVIDER)
    if preference == "auto":
        fallback_order = AUTO_WINDOW_FALLBACKS[provider_key]
    else:
        fallback_order = WINDOW_FALLBACKS.get(
            preference, AUTO_WINDOW_FALLBACKS[provider_key]
        )
    return next((valid[key] for key in fallback_order if key in valid), None)


def format_presence(label: str, used_percent: int, bar_width: int) -> str:
    """Format a compact usage bar for Discord's watching activity."""
    percent = max(0, min(100, int(used_percent)))
    width = max(MIN_BAR_WIDTH, min(MAX_BAR_WIDTH, int(bar_width)))
    filled = max(0, min(width, int(round(percent * width / 100))))
    bar = "█" * filled + "░" * (width - filled)
    window = {
        "session": "5h",
        "weekly": "7d",
        "current session": "5h",
        "current week": "7d",
        "fable week": "7d Fable",
        "opus week": "7d Opus",
        "sonnet week": "7d Sonnet",
    }.get(label.casefold(), label)
    return f"{window} {bar} {percent}% USED"


class PresenceController:
    """Own Discord listeners and one supervised refresh loop per Bot identity."""

    def __init__(self, ctx: Any, settings: PresenceSettings):
        self._ctx = ctx
        self._settings = settings
        self._states: dict[int, _BotState] = {}
        self._closed = False

    def attach(self, bot: Any, adapter: Any) -> None:
        """Attach to a Hermes-provided Discord Bot; ``adapter`` is intentionally unused."""
        del adapter
        if self._closed or bot is None:
            return

        key = id(bot)
        existing = self._states.get(key)
        if existing is not None and existing.bot is bot:
            if self._is_ready(bot):
                self._start(existing)
            return
        if existing is not None:
            self._detach(existing)

        async def on_ready() -> None:
            state = self._states.get(key)
            if state is not None and state.bot is bot and not self._closed:
                self._start(state)

        async def on_disconnect() -> None:
            state = self._states.get(key)
            if state is not None and state.bot is bot:
                self._stop(state)

        async def on_resumed() -> None:
            state = self._states.get(key)
            if state is not None and state.bot is bot and not self._closed:
                self._start(state)

        state = _BotState(
            bot=bot,
            on_ready=on_ready,
            on_disconnect=on_disconnect,
            on_resumed=on_resumed,
        )
        self._states[key] = state
        bot.add_listener(on_ready, "on_ready")
        bot.add_listener(on_disconnect, "on_disconnect")
        bot.add_listener(on_resumed, "on_resumed")

        # Hermes currently wires platform handlers after Discord is ready.
        # Start now rather than waiting for a future reconnect event.
        if self._is_ready(bot):
            self._start(state)

    @staticmethod
    def _is_ready(bot: Any) -> bool:
        try:
            return bool(bot.is_ready())
        except Exception:
            logger.debug("Could not read Discord Bot readiness", exc_info=True)
            return False

    def _start(self, state: _BotState) -> None:
        if self._closed:
            return
        current = state.task
        if current is not None and not current.done():
            return

        state.generation += 1
        generation = state.generation
        task = self._ctx.spawn_task(
            self._run(state.bot),
            name=f"discord-usage-presence:{id(state.bot)}:{generation}",
        )
        state.task = task
        key = id(state.bot)
        task.add_done_callback(
            lambda done, key=key, generation=generation: self._task_done(
                key, generation, done
            )
        )

    def _stop(self, state: _BotState) -> None:
        task = state.task
        # Clear ownership before cancellation. If on_ready races ahead and
        # installs a replacement, the old task's callback cannot erase it.
        state.task = None
        if task is not None and not task.done():
            task.cancel()

    def _task_done(self, key: int, generation: int, task: asyncio.Task) -> None:
        state = self._states.get(key)
        if (
            state is not None
            and state.generation == generation
            and state.task is task
        ):
            state.task = None

    async def _run(self, bot: Any) -> None:
        while not self._closed:
            state = self._states.get(id(bot))
            if state is None or state.bot is not bot:
                return
            await self._refresh(state)
            await asyncio.sleep(self._settings.interval_seconds)

    async def _refresh(self, state: _BotState) -> bool:
        """Fetch and apply one presence; leave the last good presence on any failure."""
        try:
            import agent.account_usage

            snapshot = await asyncio.to_thread(
                agent.account_usage.fetch_account_usage,
                PROVIDER_IDS[self._settings.provider],
            )
            selected = select_usage_window(
                snapshot,
                self._settings.window,
                self._settings.provider,
            )
            if selected is None:
                logger.debug(
                    "No valid account usage window for %s", self._settings.provider
                )
                return False

            label, percent = selected
            text = format_presence(label, percent, self._settings.bar_width)
            activity = self._make_activity(text)
            await state.bot.change_presence(activity=activity)
            state.last_good_text = text
            return True
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Could not refresh Discord account usage presence; keeping the last good presence",
                exc_info=True,
            )
            return False

    @staticmethod
    def _make_activity(text: str) -> Any:
        import discord

        return discord.Activity(type=discord.ActivityType.watching, name=text)

    def _detach(self, state: _BotState) -> None:
        self._stop(state)
        try:
            state.bot.remove_listener(state.on_ready, "on_ready")
        except Exception:
            logger.debug("Could not remove Discord on_ready listener", exc_info=True)
        try:
            state.bot.remove_listener(state.on_disconnect, "on_disconnect")
        except Exception:
            logger.debug("Could not remove Discord on_disconnect listener", exc_info=True)
        try:
            state.bot.remove_listener(state.on_resumed, "on_resumed")
        except Exception:
            logger.debug("Could not remove Discord on_resumed listener", exc_info=True)

    def close(self) -> None:
        """Remove listeners and cancel work without changing Discord's current activity."""
        if self._closed:
            return
        self._closed = True
        states = list(self._states.values())
        self._states.clear()
        for state in states:
            self._detach(state)


__all__ = [
    "PresenceController",
    "PresenceSettings",
    "format_presence",
    "select_usage_window",
]

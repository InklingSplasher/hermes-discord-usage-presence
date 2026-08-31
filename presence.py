"""Discord presence controller for OpenAI Codex account usage.

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

DEFAULT_WINDOW = "session"
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

    @classmethod
    def from_context(cls, ctx: Any) -> "PresenceSettings":
        raw_window = ctx.get_config("window", DEFAULT_WINDOW)
        window = str(raw_window or "").strip().lower()
        if window not in {"session", "weekly"}:
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
        return cls(window=window, interval_seconds=interval, bar_width=bar_width)


@dataclass
class _BotState:
    bot: Any
    on_ready: Any
    on_disconnect: Any
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


def select_usage_window(snapshot: Any, preference: str) -> Optional[tuple[str, int]]:
    """Select the preferred valid Session/Weekly window, falling back to the other."""
    valid: dict[str, tuple[str, int]] = {}
    for window in getattr(snapshot, "windows", ()) or ():
        label = str(getattr(window, "label", "") or "").strip()
        normalized = label.casefold()
        if normalized not in {"session", "weekly"}:
            continue
        percent = _finite_percent(getattr(window, "used_percent", None))
        if percent is not None:
            canonical = "Session" if normalized == "session" else "Weekly"
            valid[normalized] = (canonical, percent)

    requested = preference if preference in {"session", "weekly"} else DEFAULT_WINDOW
    alternate = "weekly" if requested == "session" else "session"
    return valid.get(requested) or valid.get(alternate)


def format_presence(label: str, used_percent: int, bar_width: int) -> str:
    """Format a compact usage bar for Discord's watching activity."""
    percent = max(0, min(100, int(used_percent)))
    width = max(MIN_BAR_WIDTH, min(MAX_BAR_WIDTH, int(bar_width)))
    filled = max(0, min(width, int(round(percent * width / 100))))
    bar = "█" * filled + "░" * (width - filled)
    window = {"session": "5h", "weekly": "7d"}.get(label.casefold(), label)
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

        state = _BotState(
            bot=bot,
            on_ready=on_ready,
            on_disconnect=on_disconnect,
        )
        self._states[key] = state
        bot.add_listener(on_ready, "on_ready")
        bot.add_listener(on_disconnect, "on_disconnect")

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
                agent.account_usage.fetch_account_usage, "openai-codex"
            )
            selected = select_usage_window(snapshot, self._settings.window)
            if selected is None:
                logger.debug("No valid Session or Weekly Codex usage window")
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
                "Could not refresh Discord Codex usage presence; keeping the last good presence",
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

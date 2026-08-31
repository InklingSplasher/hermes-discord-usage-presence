import asyncio
from pathlib import Path
import shutil
import sys
from types import ModuleType, SimpleNamespace

import pytest

import presence


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


class FakeContext:
    def __init__(self, config=None):
        self.config = config or {}
        self.platform_handlers = []
        self.unload_callbacks = []
        self.tasks = []

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    def register_platform_handler(self, platform, factory):
        self.platform_handlers.append((platform, factory))

    def on_unload(self, callback):
        self.unload_callbacks.append(callback)

    def spawn_task(self, coro, *, name=None):
        task = asyncio.create_task(coro, name=name)
        self.tasks.append(task)
        return task


class FakeBot:
    def __init__(self, ready=False, fail_presence=False):
        self.ready = ready
        self.fail_presence = fail_presence
        self.listeners = {}
        self.add_calls = []
        self.remove_calls = []
        self.presence_calls = []

    def is_ready(self):
        return self.ready

    def add_listener(self, callback, name):
        self.listeners[name] = callback
        self.add_calls.append((callback, name))

    def remove_listener(self, callback, name):
        if self.listeners.get(name) is callback:
            del self.listeners[name]
        self.remove_calls.append((callback, name))

    async def change_presence(self, *, activity):
        self.presence_calls.append(activity)
        if self.fail_presence:
            raise RuntimeError("Discord unavailable")


class FakeActivity:
    def __init__(self, *, type, name):
        self.type = type
        self.name = name


@pytest.fixture
def fake_discord(monkeypatch):
    module = ModuleType("discord")
    module.Activity = FakeActivity
    module.ActivityType = SimpleNamespace(watching="watching")
    monkeypatch.setitem(sys.modules, "discord", module)
    return module


def snapshot(*windows):
    return SimpleNamespace(windows=windows)


def window(label, used_percent):
    return SimpleNamespace(label=label, used_percent=used_percent)


def test_settings_defaults_and_invalid_values_fall_back():
    settings = presence.PresenceSettings.from_context(
        FakeContext(
            {
                "provider": "other",
                "window": "month",
                "interval_seconds": True,
                "bar_width": "bad",
            }
        )
    )

    assert settings == presence.PresenceSettings(
        window="auto", interval_seconds=300, bar_width=10, provider="codex"
    )


def test_settings_normalize_aliases_and_enforce_bounds():
    low = presence.PresenceSettings.from_context(
        FakeContext(
            {
                "provider": " Anthropic ",
                "window": " Opus_Week ",
                "interval_seconds": 2,
                "bar_width": 0,
            }
        )
    )
    high = presence.PresenceSettings.from_context(
        FakeContext(
            {
                "provider": "openai-codex",
                "interval_seconds": "900",
                "bar_width": 999,
            }
        )
    )

    assert low == presence.PresenceSettings("opus_week", 60, 1, "claude")
    assert high == presence.PresenceSettings("auto", 900, 30, "codex")


def test_window_selection_prefers_requested_valid_window():
    selected = presence.select_usage_window(
        snapshot(window("Session", 12.4), window("Weekly", 67.6)), "weekly"
    )

    assert selected == ("Weekly", 68)


def test_window_selection_falls_back_to_other_available_window():
    assert presence.select_usage_window(
        snapshot(window("Weekly", 44)), "session"
    ) == ("Weekly", 44)
    assert presence.select_usage_window(
        snapshot(window("Session", float("nan")), window("Weekly", 55)), "session"
    ) == ("Weekly", 55)
    assert presence.select_usage_window(
        snapshot(window("Session", 22), window("Weekly", None)), "weekly"
    ) == ("Session", 22)


def test_claude_auto_prefers_fable_then_opus_and_supports_explicit_windows():
    windows = snapshot(
        window("Current session", 12),
        window("Current week", 34),
        window("Fable week", 56),
        window("Opus week", 78),
        window("Sonnet week", 90),
    )

    assert presence.select_usage_window(windows, "auto", "claude") == (
        "Fable week",
        56,
    )
    assert presence.select_usage_window(
        snapshot(window("Current week", 34), window("Opus week", 78)),
        "auto",
        "anthropic",
    ) == ("Opus week", 78)
    assert presence.select_usage_window(windows, "sonnet_week", "claude") == (
        "Sonnet week",
        90,
    )


def test_window_selection_rejects_missing_nonfinite_and_unrelated_windows():
    selected = presence.select_usage_window(
        snapshot(
            window("Session", float("inf")),
            window("Weekly", None),
            window("Daily", 25),
        ),
        "session",
    )

    assert selected is None


def test_presence_format_is_clamped_and_uses_requested_bar_width():
    assert presence.format_presence("Session", -4, 10) == (
        "5h ░░░░░░░░░░ 0% USED"
    )
    assert presence.format_presence("Session", 21, 10) == (
        "5h ██░░░░░░░░ 21% USED"
    )
    assert presence.format_presence("Weekly", 104, 10) == (
        "7d ██████████ 100% USED"
    )
    assert presence.format_presence("Current session", 50, 4) == (
        "5h ██░░ 50% USED"
    )
    assert presence.format_presence("Fable week", 63, 10) == (
        "7d Fable ██████░░░░ 63% USED"
    )
    assert presence.format_presence("Opus week", 34, 10) == (
        "7d Opus ███░░░░░░░ 34% USED"
    )


def test_refresh_fetches_off_loop_and_uses_watching_activity(monkeypatch, fake_discord):
    async def run():
        ctx = FakeContext()
        bot = FakeBot()
        controller = presence.PresenceController(
            ctx, presence.PresenceSettings(window="session", bar_width=4)
        )
        state = presence._BotState(bot, None, None)
        calls = []

        account_usage = ModuleType("agent.account_usage")

        def fetch_account_usage(provider):
            calls.append(provider)
            return snapshot(window("Weekly", 49.6))

        account_usage.fetch_account_usage = fetch_account_usage
        agent = ModuleType("agent")
        agent.__path__ = []
        agent.account_usage = account_usage
        monkeypatch.setitem(sys.modules, "agent", agent)
        monkeypatch.setitem(sys.modules, "agent.account_usage", account_usage)

        offloads = []

        async def fake_to_thread(function, *args):
            offloads.append((function, args))
            return function(*args)

        monkeypatch.setattr(presence.asyncio, "to_thread", fake_to_thread)

        result = await controller._refresh(state)

        assert result is True
        assert calls == ["openai-codex"]
        assert offloads == [(fetch_account_usage, ("openai-codex",))]
        assert bot.presence_calls[0].type == fake_discord.ActivityType.watching
        assert bot.presence_calls[0].name == "7d ██░░ 50% USED"
        assert state.last_good_text == bot.presence_calls[0].name

    asyncio.run(run())


def test_refresh_uses_anthropic_for_claude_and_claude_auto_window(
    monkeypatch, fake_discord
):
    async def run():
        ctx = FakeContext()
        bot = FakeBot()
        controller = presence.PresenceController(
            ctx,
            presence.PresenceSettings(
                window="auto", bar_width=4, provider="claude"
            ),
        )
        state = presence._BotState(bot, None, None)
        calls = []

        account_usage = ModuleType("agent.account_usage")

        def fetch_account_usage(provider):
            calls.append(provider)
            return snapshot(
                window("Current week", 20),
                window("Opus week", 49.6),
            )

        account_usage.fetch_account_usage = fetch_account_usage
        agent = ModuleType("agent")
        agent.__path__ = []
        agent.account_usage = account_usage
        monkeypatch.setitem(sys.modules, "agent", agent)
        monkeypatch.setitem(sys.modules, "agent.account_usage", account_usage)

        result = await controller._refresh(state)

        assert result is True
        assert calls == ["anthropic"]
        assert bot.presence_calls[0].type == fake_discord.ActivityType.watching
        assert bot.presence_calls[0].name == "7d Opus ██░░ 50% USED"

    asyncio.run(run())


def test_refresh_failures_keep_last_good_presence(monkeypatch, fake_discord):
    async def run():
        ctx = FakeContext()
        bot = FakeBot(fail_presence=True)
        state = presence._BotState(bot, None, None, last_good_text="old activity")
        controller = presence.PresenceController(ctx, presence.PresenceSettings())

        account_usage = ModuleType("agent.account_usage")
        account_usage.fetch_account_usage = lambda provider: snapshot(window("Session", 50))
        agent = ModuleType("agent")
        agent.__path__ = []
        agent.account_usage = account_usage
        monkeypatch.setitem(sys.modules, "agent", agent)
        monkeypatch.setitem(sys.modules, "agent.account_usage", account_usage)

        assert await controller._refresh(state) is False
        assert state.last_good_text == "old activity"

        account_usage.fetch_account_usage = lambda provider: None
        bot.fail_presence = False
        bot.presence_calls.clear()
        assert await controller._refresh(state) is False
        assert bot.presence_calls == []
        assert state.last_good_text == "old activity"

        def failed_fetch(provider):
            raise RuntimeError("usage unavailable")

        account_usage.fetch_account_usage = failed_fetch
        assert await controller._refresh(state) is False
        assert bot.presence_calls == []
        assert state.last_good_text == "old activity"

    asyncio.run(run())


def test_attach_ready_bot_is_immediate_and_idempotent():
    async def run():
        ctx = FakeContext()
        bot = FakeBot(ready=True)
        controller = presence.PresenceController(ctx, presence.PresenceSettings())
        started = asyncio.Event()
        release = asyncio.Event()

        async def fake_run(attached_bot):
            assert attached_bot is bot
            started.set()
            await release.wait()

        controller._run = fake_run
        controller.attach(bot, object())
        await started.wait()
        controller.attach(bot, object())

        assert [name for _, name in bot.add_calls] == ["on_ready", "on_disconnect"]
        assert len(ctx.tasks) == 1
        controller.close()
        await asyncio.sleep(0)
        assert ctx.tasks[0].cancelled()

    asyncio.run(run())


def test_disconnect_reconnect_replaces_task_and_stale_callback_is_harmless():
    async def run():
        ctx = FakeContext()
        bot = FakeBot(ready=True)
        controller = presence.PresenceController(ctx, presence.PresenceSettings())

        async def wait_forever(attached_bot):
            await asyncio.Event().wait()

        controller._run = wait_forever
        controller.attach(bot, None)
        first = ctx.tasks[-1]
        await bot.listeners["on_disconnect"]()
        assert first.cancelled() or first.cancelling()

        await bot.listeners["on_ready"]()
        second = ctx.tasks[-1]
        assert second is not first
        state = controller._states[id(bot)]
        assert state.task is second

        first_generation = state.generation - 1
        controller._task_done(id(bot), first_generation, first)
        assert state.task is second

        controller.close()
        await asyncio.sleep(0)

    asyncio.run(run())


def test_unload_removes_listeners_cancels_tasks_and_never_clears_activity():
    async def run():
        ctx = FakeContext()
        bot = FakeBot(ready=True)
        controller = presence.PresenceController(ctx, presence.PresenceSettings())
        controller._run = lambda attached_bot: asyncio.Event().wait()
        controller.attach(bot, None)
        task = ctx.tasks[-1]

        controller.close()
        controller.close()
        await asyncio.sleep(0)

        assert {name for _, name in bot.remove_calls} == {"on_ready", "on_disconnect"}
        assert bot.listeners == {}
        assert task.cancelled()
        assert bot.presence_calls == []

    asyncio.run(run())


def test_register_uses_public_discord_handler_and_lifecycle_apis():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "hermes_presence_test_plugin",
        PLUGIN_ROOT / "__init__.py",
        submodule_search_locations=[str(PLUGIN_ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        ctx = FakeContext()
        module.register(ctx)
    finally:
        sys.modules.pop(spec.name, None)
        sys.modules.pop(f"{spec.name}.presence", None)

    assert len(ctx.platform_handlers) == 1
    assert ctx.platform_handlers[0][0] == "discord"
    assert len(ctx.unload_callbacks) == 1


def test_real_plugin_manager_discovers_and_loads_root_manifest(tmp_path, monkeypatch):
    plugins_module = pytest.importorskip("hermes_cli.plugins")
    plugin_copy = tmp_path / "plugins" / "hermes-discord-usage-presence"
    shutil.copytree(
        PLUGIN_ROOT,
        plugin_copy,
        ignore=shutil.ignore_patterns(".git", ".pytest_cache", "__pycache__", "*.pyc"),
    )
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(bundled))
    monkeypatch.setenv("HERMES_ENABLE_PROJECT_PLUGINS", "0")

    manager = plugins_module.PluginManager()
    manifests = manager._scan_directory(tmp_path / "plugins", source="user")
    assert len(manifests) == 1
    assert manifests[0].name == "hermes-discord-usage-presence"
    assert manifests[0].key == "hermes-discord-usage-presence"
    assert manifests[0].manifest_version == 1
    assert manifests[0].config_schema == {
        "provider": {
            "type": "string",
            "default": "codex",
            "enum": ["codex", "claude"],
            "description": "Account usage provider queried through Hermes",
        },
        "window": {
            "type": "string",
            "default": "auto",
            "enum": [
                "auto",
                "session",
                "weekly",
                "fable_week",
                "opus_week",
                "sonnet_week",
            ],
            "description": "Preferred usage window; auto uses provider-specific defaults",
        },
        "interval_seconds": {
            "type": "integer",
            "default": 300,
            "minimum": 60,
            "description": "Seconds between usage refreshes",
        },
        "bar_width": {
            "type": "integer",
            "default": 10,
            "minimum": 1,
            "maximum": 30,
            "description": "Number of cells in the usage bar",
        },
    }

    manager._load_plugin(manifests[0])
    loaded = manager._plugins["hermes-discord-usage-presence"]
    assert loaded.enabled is True
    assert loaded.error is None
    assert len(manager.get_platform_handler_factories("discord")) == 1
    assert manager.unload("hermes-discord-usage-presence") is True

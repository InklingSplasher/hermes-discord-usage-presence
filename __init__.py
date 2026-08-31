"""Hermes Discord usage-presence plugin entrypoint."""

if __package__:
    from .presence import PresenceController, PresenceSettings
else:  # pytest may collect a hyphenated standalone repository as bare __init__
    from presence import PresenceController, PresenceSettings


def register(ctx):
    """Register the Discord-native attachment without importing discord.py."""
    settings = PresenceSettings.from_context(ctx)
    controller = PresenceController(ctx, settings)
    ctx.register_platform_handler("discord", controller.attach)
    ctx.on_unload(controller.close)


__all__ = ["PresenceController", "PresenceSettings", "register"]

# Hermes Discord Usage Presence

A standalone native plugin for Hermes that shows OpenAI Codex account usage as
the Discord bot's watching activity. It uses Hermes' existing Discord client
and account-usage fetcher; it does not patch the Discord adapter and does not
install or vendor `discord.py`.

Example activities with the default 10-cell bar:

```text
Watching 5h ██░░░░░░░░ 21% USED
Watching 7d ████░░░░░░ 40% USED
```

The plugin is compatible with Hermes 0.20.6 and newer.

## Install and enable

Publish or fork this repository on GitHub. These commands work unchanged in
fish, bash, and zsh:

```console
hermes plugins install InklingSplasher/hermes-discord-usage-presence --no-enable
hermes plugins enable hermes-discord-usage-presence
hermes gateway restart
```

Hermes clones Git sources itself. A full HTTPS source is also accepted:

```console
hermes plugins install https://github.com/InklingSplasher/hermes-discord-usage-presence.git --no-enable
```

The manifest is at the repository root, so the cloned repository is directly
discoverable as a native Hermes plugin. Enabling, disabling, or changing the
settings requires a gateway restart before the Discord bot uses the new state.

## Configure

Defaults are `session`, 300 seconds, and a 10-cell bar:

```console
hermes config set plugins.entries.hermes-discord-usage-presence.settings.window weekly
hermes config set plugins.entries.hermes-discord-usage-presence.settings.interval_seconds 300
hermes config set plugins.entries.hermes-discord-usage-presence.settings.bar_width 10
hermes gateway restart
```

Settings:

| Setting | Values | Default | Behavior |
| --- | --- | --- | --- |
| `window` | `session` or `weekly` | `session` | Preferred window, not a hard requirement |
| `interval_seconds` | integer, minimum 60 | `300` | Refresh cadence; smaller values become 60 |
| `bar_width` | integer from 1 to 30 | `10` | Width of the filled/empty usage bar |

The requested window is a preference. If it is missing or has no finite usage
percentage, the plugin automatically uses the other valid Session/Weekly
window. This matters for account types such as Max accounts that may not expose
a five-hour Session window. The activity says `5h <bar> N% USED` for the
actual Session window or `7d <bar> N% USED` for the actual Weekly window,
including after fallback. Percentages are rounded and clamped to 0–100.

If the usage fetch fails, neither window is valid, or Discord rejects a
presence update, the plugin leaves the last successfully applied activity in
place and retries on the next interval. Reconnects trigger an immediate refresh.
Unloading the plugin cancels its work and removes its listeners but deliberately
does not clear or replace the bot's current Discord activity.

## Authentication and security

- The plugin reads no token files directly. It calls Hermes'
  `agent.account_usage.fetch_account_usage("openai-codex")`, which uses the
  active profile's existing OpenAI Codex authentication.
- It does not store, log, or transmit OpenAI or Discord credentials. Network
  access for the usage lookup remains in Hermes' account-usage implementation.
- It uses the Discord `Bot` already owned by the Hermes adapter and needs no
  separate bot token or extra privileged Discord intent.
- Discord presence is public to people who can see the bot. Enabling this
  plugin intentionally exposes the selected window name and rounded usage
  percentage; disable it if that operational metadata is sensitive.
- Native Hermes plugins execute in the gateway process with the user's
  privileges. Review plugin source before enabling any third-party fork.

## Development

Run the focused tests with the Hermes test wrapper when developing beside a
Hermes checkout:

```console
/path/to/hermes-agent/scripts/run_tests.sh "$PWD/tests" -q
hermes plugins doctor . --ci
```

The project intentionally has no runtime dependencies of its own.

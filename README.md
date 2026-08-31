# Hermes Discord Usage Presence

A standalone native plugin for Hermes that shows OpenAI Codex or Anthropic
Claude account usage as the Discord bot's watching activity. It uses Hermes'
existing Discord client and account-usage fetcher; it does not patch the Discord
adapter and does not install or vendor `discord.py`.

Example activities with the default 10-cell bar:

```text
Watching 5h ██░░░░░░░░ 21% USED
Watching 7d ████░░░░░░ 40% USED
Watching 7d Fable ██████░░░░ 63% USED
Watching 7d Opus ███░░░░░░░ 34% USED
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

## Update

Git-installed plugins are not updated automatically. Pull a newer version and
restart the gateway with:

```console
hermes plugins update hermes-discord-usage-presence
hermes gateway restart
```

## Configure

Defaults are provider `codex`, window `auto`, 300 seconds, and a 10-cell bar.
To use Claude instead:

```console
hermes config set plugins.entries.hermes-discord-usage-presence.settings.provider claude
hermes config set plugins.entries.hermes-discord-usage-presence.settings.window auto
hermes config set plugins.entries.hermes-discord-usage-presence.settings.interval_seconds 300
hermes config set plugins.entries.hermes-discord-usage-presence.settings.bar_width 10
hermes gateway restart
```

Settings:

| Setting | Values | Default | Behavior |
| --- | --- | --- | --- |
| `provider` | `codex` or `claude` | `codex` | Hermes account-usage source |
| `window` | `auto`, `session`, `weekly`, `fable_week`, `opus_week`, or `sonnet_week` | `auto` | Preferred window, with fallback when unavailable |
| `interval_seconds` | integer, minimum 60 | `300` | Refresh cadence; smaller values become 60 |
| `bar_width` | integer from 1 to 30 | `10` | Width of the filled/empty usage bar |

With `codex`, `auto` prefers the five-hour Session window and falls back to
the seven-day Weekly window. With `claude`, `auto` prefers a Fable weekly
limit when Hermes exposes one, then Opus, the all-model weekly window, the
five-hour session, and finally Sonnet. An explicitly selected window is still a
preference: the plugin falls back instead of removing a previously successful
presence when that account does not expose the requested limit.

The activity labels the actual selected window as `5h`, `7d`, `7d Fable`,
`7d Opus`, or `7d Sonnet`, including after fallback. Percentages are rounded
and clamped to 0–100.

If the usage fetch fails, no supported window is valid, or Discord rejects a
presence update, the plugin leaves the last successfully applied activity in
place and retries on the next interval. Reconnects trigger an immediate refresh.
Unloading the plugin cancels its work and removes its listeners but deliberately
does not clear or replace the bot's current Discord activity.

## Authentication and security

- The plugin reads no token files directly. It calls Hermes'
  `agent.account_usage.fetch_account_usage()` with `openai-codex` or
  `anthropic`, using the active profile's existing authentication.
- Claude account limits require an OAuth-backed Anthropic login; Anthropic API
  keys do not expose subscription usage through Hermes.
- It does not store, log, or transmit OpenAI, Anthropic, or Discord credentials.
  Network access for the usage lookup remains in Hermes' account-usage
  implementation.
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

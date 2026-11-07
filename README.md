# ULTIMATE V3 — All-in-One Discord Bot

Railway-compatible, Python 3.11+, `discord.py` 2.x, SQLite, slash commands only.
This is a feature-rich starter, not a byte-for-byte Zyrex clone.

## V3 features
- Moderation: ban, kick, timeout, untimeout, purge, warnings/history
- Anti-spam and join-burst anti-raid detection
- Anti-nuke audit-log monitoring for channel/role deletion, bans, kicks and bot additions
- Trusted-user allow-list and security event history
- Link protection: server-configured blocked domains plus heuristic detection for common Nitro/gift scams, URL shorteners and punycode domains
- Welcome/logging, tickets with persistent buttons, giveaways, reaction roles
- Join-to-create temporary voice rooms and owner controls
- XP/levels, simple economy, custom text-trigger responses
- Optional Lavalink music; optional OAuth dashboard starter
- SQLite persistence and Railway deployment configuration

## Discord setup
Create a Discord application and bot. Enable **Server Members Intent** and **Message Content Intent** in the Developer Portal. Invite with `bot` and `applications.commands` scopes. Grant only permissions needed by enabled modules. The bot role must be above roles it moderates or assigns.

## Railway deployment
1. Push this folder to a GitHub repository and deploy it on Railway.
2. Add Railway Variables:
   - `DISCORD_TOKEN` — bot token
   - `BOT_OWNER_ID` — your numeric Discord user ID
   - `DB_PATH=/data/ultimate_v3.db`
   - `PYTHON_VERSION=3.11`
3. Add a Railway Volume mounted at `/data` to preserve SQLite data across redeploys.
4. Start command: `python main.py` (already set in `railway.toml`).
5. In Discord, use `/setlogs`, `/security`, `/antinuke on`, `/trustuser`, `/blockdomain`, and `/ticketpanel`.

## Required permissions and intents
Enable Server Members and Message Content privileged intents. Depending on features, grant View Audit Log, Manage Messages, Moderate Members, Manage Channels, Manage Roles, Kick Members, Ban Members, and Connect/Speak. Anti-nuke requires **View Audit Log**; link filtering requires **Manage Messages**.

## Optional music
Music requires a separately hosted Lavalink node and environment variables `LAVALINK_URI` and `LAVALINK_PASSWORD`. The bot does not provide or operate the Lavalink server.

## Optional dashboard
`dashboard.py` remains an OAuth login/server-list starter. It is not a full live configuration dashboard. Configure the OAuth environment variables listed in the V2 guide before exposing it; use HTTPS and a strong session secret.

## Important safety/operational notes
- Anti-nuke is detection plus cautious response, not a guarantee against every attack. It cannot undo deleted channels/roles and cannot timeout administrators or the server owner.
- Link detection is heuristic and can produce false positives. Review reports and maintain the domain blocklist carefully.
- SQLite on Railway needs a persistent volume mounted at `/data`; without it, data may be lost on redeploy.
- Never put the bot token in source code or commit it. If exposed, regenerate it in the Developer Portal.
- Test in a private server and verify role hierarchy/permissions before production.

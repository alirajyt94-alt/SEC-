"""
ULTIMATE V2 — All-in-One Discord Bot
Python 3.11+ | discord.py 2.x | SQLite | optional Lavalink music
Slash commands only. Never put your bot token in source control.
"""
import os, asyncio, random, logging
from datetime import datetime, timedelta, timezone
from collections import defaultdict, deque

import discord
from discord import app_commands
from discord.ext import commands, tasks
import aiosqlite

try:
    import wavelink
except ImportError:
    wavelink = None

TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_ID = int(os.getenv("BOT_OWNER_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "ultimate_v2.db")
LAVALINK_URI = os.getenv("LAVALINK_URI", "")
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD", "")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ultimate-v3")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.voice_states = True

class UltimateBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.db = None
        self.spam = defaultdict(deque)
        self.raid_joins = defaultdict(deque)

    async def setup_hook(self):
        self.db = await aiosqlite.connect(DB_PATH)
        await self.db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS config(guild_id INTEGER, key TEXT, value TEXT, PRIMARY KEY(guild_id,key));
        CREATE TABLE IF NOT EXISTS warnings(id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER,user_id INTEGER,mod_id INTEGER,reason TEXT,created TEXT);
        CREATE TABLE IF NOT EXISTS custom_commands(guild_id INTEGER,name TEXT,response TEXT,PRIMARY KEY(guild_id,name));
        CREATE TABLE IF NOT EXISTS reaction_roles(guild_id INTEGER,message_id INTEGER,emoji TEXT,role_id INTEGER,PRIMARY KEY(message_id,emoji));
        CREATE TABLE IF NOT EXISTS tickets(channel_id INTEGER PRIMARY KEY,guild_id INTEGER,opener_id INTEGER,claimed_by INTEGER);
        CREATE TABLE IF NOT EXISTS temp_voice(channel_id INTEGER PRIMARY KEY,guild_id INTEGER,owner_id INTEGER);
        CREATE TABLE IF NOT EXISTS giveaways(message_id INTEGER PRIMARY KEY,channel_id INTEGER,guild_id INTEGER,ends_at TEXT,winners INTEGER,prize TEXT,ended INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS economy(guild_id INTEGER,user_id INTEGER,wallet INTEGER DEFAULT 0,bank INTEGER DEFAULT 0,last_daily TEXT,PRIMARY KEY(guild_id,user_id));
        CREATE TABLE IF NOT EXISTS levels(guild_id INTEGER,user_id INTEGER,xp INTEGER DEFAULT 0,level INTEGER DEFAULT 0,PRIMARY KEY(guild_id,user_id));
        CREATE TABLE IF NOT EXISTS trusted_users(guild_id INTEGER,user_id INTEGER,PRIMARY KEY(guild_id,user_id));
        CREATE TABLE IF NOT EXISTS security_events(id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER,actor_id INTEGER,action TEXT,created TEXT,details TEXT);
        CREATE TABLE IF NOT EXISTS blocked_domains(guild_id INTEGER,domain TEXT,PRIMARY KEY(guild_id,domain));
        """)
        await self.db.commit()
        # Persistent views survive restarts because their custom IDs are stable.
        self.add_view(TicketPanel())
        self.add_view(TicketControls())
        await self.tree.sync()
        giveaway_worker.start()
        await self.connect_music()

    async def connect_music(self):
        if not wavelink or not LAVALINK_URI or not LAVALINK_PASSWORD:
            log.info("Music disabled: install wavelink and configure Lavalink variables to enable it.")
            return
        try:
            node = wavelink.Node(uri=LAVALINK_URI, password=LAVALINK_PASSWORD)
            await wavelink.Pool.connect(client=self, nodes=[node])
            log.info("Connected to Lavalink.")
        except Exception:
            log.exception("Could not connect to Lavalink; music commands will be unavailable.")

    async def close(self):
        if self.db:
            await self.db.close()
        await super().close()

bot = UltimateBot()

def em(title, desc="", color=discord.Color.blurple()):
    return discord.Embed(title=title, description=desc, color=color, timestamp=discord.utils.utcnow())

async def cfg(gid, key, value=None):
    if value is None:
        cur = await bot.db.execute("SELECT value FROM config WHERE guild_id=? AND key=?", (gid,key))
        row = await cur.fetchone()
        return row[0] if row else None
    await bot.db.execute("INSERT OR REPLACE INTO config(guild_id,key,value) VALUES(?,?,?)",(gid,key,str(value)))
    await bot.db.commit()

async def owner_only(i):
    if OWNER_ID and i.user.id == OWNER_ID:
        return True
    msg = "⛔ Owner-only command."
    if i.response.is_done(): await i.followup.send(msg, ephemeral=True)
    else: await i.response.send_message(msg, ephemeral=True)
    return False

async def moderator(i):
    p = i.user.guild_permissions
    if p.administrator or p.manage_guild:
        return True
    if i.response.is_done(): await i.followup.send("⛔ You need Manage Server permission.",ephemeral=True)
    else: await i.response.send_message("⛔ You need Manage Server permission.",ephemeral=True)
    return False

async def log_event(guild, title, description):
    channel_id = await cfg(guild.id, "log_channel")
    channel = guild.get_channel(int(channel_id)) if channel_id else None
    if channel:
        try: await channel.send(embed=em(title, description, discord.Color.orange()))
        except discord.HTTPException: pass

@bot.event
async def on_ready():
    log.info("Ready as %s (%s)", bot.user, bot.user.id)

# -------------------- SECURITY / LOGGING --------------------
@bot.event
async def on_member_join(member):
    now = datetime.now(timezone.utc)
    joins = bot.raid_joins[member.guild.id]
    joins.append(now)
    while joins and (now - joins[0]).total_seconds() > 15: joins.popleft()

    welcome_id = await cfg(member.guild.id, "welcome_channel")
    welcome = member.guild.get_channel(int(welcome_id)) if welcome_id else None
    if welcome:
        await welcome.send(embed=em("👋 Welcome!", f"Welcome {member.mention} to **{member.guild.name}**!"))

    raid_mode = await cfg(member.guild.id, "anti_raid")
    threshold = int(await cfg(member.guild.id, "raid_threshold") or 8)
    if raid_mode == "on" and len(joins) >= threshold:
        await cfg(member.guild.id, "raid_lockdown", "on")
        await log_event(member.guild, "🚨 Possible raid detected", f"{len(joins)} members joined within 15 seconds.")
        # Do not mass-ban: notify staff and optionally enable verification lockdown.
        if member.guild.system_channel:
            try: await member.guild.system_channel.send("🚨 **Possible raid detected.** Staff have been notified.")
            except discord.HTTPException: pass

@bot.event
async def on_member_remove(member):
    await log_event(member.guild, "Member left", f"{member} (`{member.id}`)")

@bot.event
async def on_member_ban(guild, user):
    await log_event(guild, "Member banned", f"{user} (`{user.id}`)")

@bot.event
async def on_guild_channel_delete(channel):
    await log_event(channel.guild, "Channel deleted", f"{channel.name} (`{channel.id}`)")

@bot.event
async def on_guild_role_delete(role):
    await log_event(role.guild, "Role deleted", f"{role.name} (`{role.id}`)")

async def original_on_message(message):
    if message.author.bot or not message.guild:
        return
    gid, uid = message.guild.id, message.author.id

    # Custom commands are exact-name text triggers; bot commands remain slash-only.
    # Message Content Intent must be enabled in the Developer Portal.
    cur = await bot.db.execute("SELECT response FROM custom_commands WHERE guild_id=? AND name=?",
                               (gid, message.content.strip().lower()))
    row = await cur.fetchone()
    if row:
        await message.channel.send(row[0][:1900])

    if await cfg(gid, "anti_spam") == "on":
        q = bot.spam[(gid,uid)]
        now = datetime.now(timezone.utc)
        q.append(now)
        while q and (now-q[0]).total_seconds() > 7: q.popleft()
        if len(q) >= 6:
            q.clear()
            if message.guild.me.guild_permissions.moderate_members:
                try:
                    await message.author.timeout(timedelta(minutes=2), reason="Anti-spam protection")
                    await log_event(message.guild, "Anti-spam action", f"Timed out {message.author.mention} for message flooding.")
                except discord.HTTPException: pass

    await bot.db.execute("INSERT OR IGNORE INTO levels(guild_id,user_id,xp,level) VALUES(?,?,0,0)",(gid,uid))
    cur = await bot.db.execute("SELECT xp,level FROM levels WHERE guild_id=? AND user_id=?",(gid,uid))
    xp, level = await cur.fetchone()
    xp += random.randint(8,15)
    new_level = xp // 100
    await bot.db.execute("UPDATE levels SET xp=?,level=? WHERE guild_id=? AND user_id=?",(xp,new_level,gid,uid))
    await bot.db.commit()
    if new_level > level:
        await message.channel.send(f"🎉 {message.author.mention} reached **Level {new_level}**!")

@bot.event
async def on_raw_reaction_add(payload):
    if payload.guild_id is None or (bot.user and payload.user_id == bot.user.id): return
    cur = await bot.db.execute("SELECT role_id FROM reaction_roles WHERE guild_id=? AND message_id=? AND emoji=?",
                               (payload.guild_id,payload.message_id,str(payload.emoji)))
    row = await cur.fetchone()
    guild = bot.get_guild(payload.guild_id)
    member = guild.get_member(payload.user_id) if guild else None
    role = guild.get_role(row[0]) if row and guild else None
    if member and role:
        try: await member.add_roles(role, reason="Reaction role")
        except discord.HTTPException: pass

@bot.event
async def on_raw_reaction_remove(payload):
    if payload.guild_id is None: return
    cur = await bot.db.execute("SELECT role_id FROM reaction_roles WHERE guild_id=? AND message_id=? AND emoji=?",
                               (payload.guild_id,payload.message_id,str(payload.emoji)))
    row = await cur.fetchone()
    guild = bot.get_guild(payload.guild_id)
    member = guild.get_member(payload.user_id) if guild else None
    role = guild.get_role(row[0]) if row and guild else None
    if member and role:
        try: await member.remove_roles(role, reason="Reaction role removed")
        except discord.HTTPException: pass

# -------------------- BASIC COMMANDS --------------------
@bot.tree.command(name="ping",description="Show bot latency")
async def ping(i): await i.response.send_message(embed=em("🏓 Pong",f"{round(bot.latency*1000)} ms"))

@bot.tree.command(name="serverinfo",description="Show server information")
async def serverinfo(i):
    g=i.guild
    await i.response.send_message(embed=em(g.name,f"Members: **{g.member_count}**\nChannels: **{len(g.channels)}**\nRoles: **{len(g.roles)}**\nOwner: <@{g.owner_id}>"))

@bot.tree.command(name="userinfo",description="Show member information")
async def userinfo(i, member: discord.Member=None):
    m=member or i.user
    e=em(str(m),f"ID: `{m.id}`\nJoined: {discord.utils.format_dt(m.joined_at,'R') if m.joined_at else 'Unknown'}\nCreated: {discord.utils.format_dt(m.created_at,'R')}")
    e.set_thumbnail(url=m.display_avatar.url)
    await i.response.send_message(embed=e)

# -------------------- MODERATION --------------------
@bot.tree.command(name="ban",description="Ban a member")
@app_commands.default_permissions(ban_members=True)
async def ban(i, member: discord.Member, reason: str="No reason supplied"):
    if not await moderator(i): return
    await member.ban(reason=reason)
    await i.response.send_message(f"🔨 Banned {member.mention}. Reason: {reason}")
    await log_event(i.guild,"Member banned",f"{member} by {i.user}: {reason}")

@bot.tree.command(name="kick",description="Kick a member")
@app_commands.default_permissions(kick_members=True)
async def kick(i, member: discord.Member, reason: str="No reason supplied"):
    if not await moderator(i): return
    await member.kick(reason=reason)
    await i.response.send_message(f"👢 Kicked {member.mention}.")
    await log_event(i.guild,"Member kicked",f"{member} by {i.user}: {reason}")

@bot.tree.command(name="timeout",description="Timeout a member for up to 7 days")
@app_commands.default_permissions(moderate_members=True)
async def timeout(i, member: discord.Member, minutes: app_commands.Range[int,1,10080], reason: str="No reason supplied"):
    if not await moderator(i): return
    await member.timeout(timedelta(minutes=minutes),reason=reason)
    await i.response.send_message(f"🔇 Timed out {member.mention} for {minutes} minutes.")

@bot.tree.command(name="untimeout",description="Remove a timeout")
@app_commands.default_permissions(moderate_members=True)
async def untimeout(i, member: discord.Member):
    if not await moderator(i): return
    await member.timeout(None)
    await i.response.send_message(f"🔊 Removed timeout for {member.mention}.")

@bot.tree.command(name="purge",description="Delete up to 100 messages")
@app_commands.default_permissions(manage_messages=True)
async def purge(i, amount: app_commands.Range[int,1,100]):
    if not await moderator(i): return
    await i.response.defer(ephemeral=True)
    deleted=await i.channel.purge(limit=amount)
    await i.followup.send(f"Deleted {len(deleted)} messages.",ephemeral=True)

@bot.tree.command(name="warn",description="Issue and store a warning")
@app_commands.default_permissions(moderate_members=True)
async def warn(i, member: discord.Member, reason: str):
    if not await moderator(i): return
    await bot.db.execute("INSERT INTO warnings(guild_id,user_id,mod_id,reason,created) VALUES(?,?,?,?,?)",
                         (i.guild.id,member.id,i.user.id,reason,datetime.now(timezone.utc).isoformat()))
    await bot.db.commit()
    await i.response.send_message(f"⚠️ Warned {member.mention}: {reason}")
    await log_event(i.guild,"Warning issued",f"{member.mention} warned by {i.user.mention}: {reason}")

@bot.tree.command(name="warnings",description="View a member's latest warnings")
@app_commands.default_permissions(moderate_members=True)
async def warnings(i, member: discord.Member):
    if not await moderator(i): return
    cur=await bot.db.execute("SELECT reason,created FROM warnings WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 10",(i.guild.id,member.id))
    rows=await cur.fetchall()
    desc="\n".join(f"• {r[0]} ({r[1][:10]})" for r in rows) or "No warnings."
    await i.response.send_message(embed=em(f"Warnings — {member}",desc),ephemeral=True)

# -------------------- CONFIG / SECURITY --------------------
@bot.tree.command(name="setlogs",description="Set the server log channel")
@app_commands.default_permissions(manage_guild=True)
async def setlogs(i, channel: discord.TextChannel):
    if not await moderator(i): return
    await cfg(i.guild.id,"log_channel",channel.id)
    await i.response.send_message(f"✅ Logs will be sent to {channel.mention}.")

@bot.tree.command(name="setwelcome",description="Set the welcome channel")
@app_commands.default_permissions(manage_guild=True)
async def setwelcome(i, channel: discord.TextChannel):
    if not await moderator(i): return
    await cfg(i.guild.id,"welcome_channel",channel.id)
    await i.response.send_message(f"Welcome channel set to {channel.mention}.")

@bot.tree.command(name="security",description="Configure basic anti-spam or anti-raid")
@app_commands.default_permissions(manage_guild=True)
@app_commands.choices(feature=[app_commands.Choice(name="anti_spam",value="anti_spam"),app_commands.Choice(name="anti_raid",value="anti_raid")],
                       state=[app_commands.Choice(name="on",value="on"),app_commands.Choice(name="off",value="off")])
async def security(i, feature: app_commands.Choice[str], state: app_commands.Choice[str]):
    if not await moderator(i): return
    await cfg(i.guild.id,feature.value,state.value)
    await i.response.send_message(f"🛡️ **{feature.name}** is now **{state.name.upper()}**.")

@bot.tree.command(name="raidthreshold",description="Set anti-raid join threshold within 15 seconds")
@app_commands.default_permissions(manage_guild=True)
async def raidthreshold(i, members: app_commands.Range[int,3,30]):
    if not await moderator(i): return
    await cfg(i.guild.id,"raid_threshold",members)
    await i.response.send_message(f"Anti-raid alert threshold set to {members} joins in 15 seconds.")

@bot.tree.command(name="lockdown",description="Enable or disable lockdown alert state")
@app_commands.default_permissions(manage_guild=True)
async def lockdown(i, state: str):
    if not await moderator(i): return
    if state.lower() not in ("on","off"): return await i.response.send_message("Use on or off.",ephemeral=True)
    await cfg(i.guild.id,"raid_lockdown",state.lower())
    await i.response.send_message(f"🚨 Lockdown state: **{state.upper()}**. This flag alerts staff; it does not mass-edit channels.")

@bot.tree.command(name="customcommand",description="Create a custom text-trigger response")
@app_commands.default_permissions(manage_guild=True)
async def customcommand(i, trigger: str, response: str):
    if not await moderator(i): return
    trigger=trigger.strip().lower()
    if len(trigger)>80 or not trigger: return await i.response.send_message("Trigger must be 1–80 characters.",ephemeral=True)
    await bot.db.execute("INSERT OR REPLACE INTO custom_commands(guild_id,name,response) VALUES(?,?,?)",(i.guild.id,trigger,response[:1900]))
    await bot.db.commit()
    await i.response.send_message(f"✅ Custom trigger `{trigger}` saved.")

@bot.tree.command(name="reactionrole",description="Attach a reaction role to a message")
@app_commands.default_permissions(manage_roles=True)
async def reactionrole(i, message_id: str, emoji: str, role: discord.Role):
    if not await moderator(i): return
    try:
        msg=await i.channel.fetch_message(int(message_id))
        await msg.add_reaction(emoji)
    except (ValueError,discord.HTTPException):
        return await i.response.send_message("Could not find that message or add that emoji.",ephemeral=True)
    await bot.db.execute("INSERT OR REPLACE INTO reaction_roles(guild_id,message_id,emoji,role_id) VALUES(?,?,?,?)",(i.guild.id,int(message_id),emoji,role.id))
    await bot.db.commit()
    await i.response.send_message(f"✅ {emoji} now grants {role.mention} on that message.")

# -------------------- TICKETS --------------------
class TicketPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="Open Ticket",style=discord.ButtonStyle.green,emoji="🎫",custom_id="uv2:ticket_open")
    async def open_ticket(self,i,b):
        g=i.guild
        existing=discord.utils.get(g.text_channels,name=f"ticket-{i.user.id}")
        if existing: return await i.response.send_message(f"Already open: {existing.mention}",ephemeral=True)
        perms={g.default_role:discord.PermissionOverwrite(view_channel=False),
               i.user:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True),
               g.me:discord.PermissionOverwrite(view_channel=True,send_messages=True,manage_channels=True)}
        ch=await g.create_text_channel(f"ticket-{i.user.id}",overwrites=perms)
        await bot.db.execute("INSERT OR REPLACE INTO tickets(channel_id,guild_id,opener_id,claimed_by) VALUES(?,?,?,NULL)",(ch.id,g.id,i.user.id))
        await bot.db.commit()
        await ch.send(f"{i.user.mention} Support will be with you soon.",view=TicketControls())
        await i.response.send_message(f"Ticket created: {ch.mention}",ephemeral=True)

class TicketControls(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="Claim",style=discord.ButtonStyle.primary,custom_id="uv2:ticket_claim")
    async def claim(self,i,b):
        if not i.user.guild_permissions.manage_channels: return await i.response.send_message("Staff only.",ephemeral=True)
        await bot.db.execute("UPDATE tickets SET claimed_by=? WHERE channel_id=?",(i.user.id,i.channel.id))
        await bot.db.commit()
        await i.response.send_message(f"Claimed by {i.user.mention}.")
    @discord.ui.button(label="Close",style=discord.ButtonStyle.danger,custom_id="uv2:ticket_close")
    async def close(self,i,b):
        cur=await bot.db.execute("SELECT opener_id FROM tickets WHERE channel_id=?",(i.channel.id,))
        row=await cur.fetchone()
        if not row: return await i.response.send_message("Not a ticket.",ephemeral=True)
        if i.user.id!=row[0] and not i.user.guild_permissions.manage_channels:
            return await i.response.send_message("Only ticket opener or staff.",ephemeral=True)
        await i.response.send_message("Closing in 5 seconds.")
        await asyncio.sleep(5)
        await bot.db.execute("DELETE FROM tickets WHERE channel_id=?",(i.channel.id,))
        await bot.db.commit()
        await i.channel.delete(reason="Ticket closed")

@bot.tree.command(name="ticketpanel",description="Post a ticket panel")
@app_commands.default_permissions(manage_guild=True)
async def ticketpanel(i):
    if not await moderator(i): return
    await i.channel.send(embed=em("🎫 Support Center","Press the button to open a private ticket."),view=TicketPanel())
    await i.response.send_message("Ticket panel posted.",ephemeral=True)

# -------------------- GIVEAWAYS --------------------
@bot.tree.command(name="giveaway",description="Start a giveaway (duration in minutes)")
@app_commands.default_permissions(manage_guild=True)
async def giveaway(i, minutes: app_commands.Range[int,1,10080], winners: app_commands.Range[int,1,20], prize: str):
    if not await moderator(i): return
    end=datetime.now(timezone.utc)+timedelta(minutes=minutes)
    await i.response.send_message(embed=em("🎉 GIVEAWAY",f"**Prize:** {prize}\n**Winners:** {winners}\n**Ends:** {discord.utils.format_dt(end,'R')}\nReact 🎉 to enter!"))
    msg=await i.original_response()
    await msg.add_reaction("🎉")
    await bot.db.execute("INSERT INTO giveaways(message_id,channel_id,guild_id,ends_at,winners,prize,ended) VALUES(?,?,?,?,?,?,0)",(msg.id,msg.channel.id,i.guild.id,end.isoformat(),winners,prize))
    await bot.db.commit()

@tasks.loop(seconds=20)
async def giveaway_worker():
    cur=await bot.db.execute("SELECT message_id,channel_id,winners,prize FROM giveaways WHERE ended=0 AND ends_at<=?",(datetime.now(timezone.utc).isoformat(),))
    for mid,cid,count,prize in await cur.fetchall():
        ch=bot.get_channel(cid)
        if ch:
            try:
                msg=await ch.fetch_message(mid)
                r=discord.utils.get(msg.reactions,emoji="🎉")
                users=[u async for u in r.users()] if r else []
                users=[u for u in users if not u.bot]
                winners=random.sample(users,min(count,len(users))) if users else []
                await ch.send(f"🎉 **{prize}** winner(s): {', '.join(u.mention for u in winners) if winners else 'No valid entries.'}")
            except discord.HTTPException: pass
        await bot.db.execute("UPDATE giveaways SET ended=1 WHERE message_id=?",(mid,))
    await bot.db.commit()

@giveaway_worker.before_loop
async def before_giveaways(): await bot.wait_until_ready()

# -------------------- TEMP VOICE --------------------
@bot.tree.command(name="setjoincreate",description="Configure Join-to-Create and category")
@app_commands.default_permissions(manage_guild=True)
async def setjoincreate(i, trigger: discord.VoiceChannel, category: discord.CategoryChannel):
    if not await moderator(i): return
    await cfg(i.guild.id,"join_create",trigger.id); await cfg(i.guild.id,"temp_category",category.id)
    await i.response.send_message(f"Join-to-create: {trigger.mention}\nCategory: **{category.name}**")

@bot.event
async def on_voice_state_update(member,before,after):
    if after.channel:
        trigger=await cfg(member.guild.id,"join_create")
        if trigger and after.channel.id==int(trigger):
            catid=await cfg(member.guild.id,"temp_category")
            category=member.guild.get_channel(int(catid)) if catid else after.channel.category
            try:
                ch=await member.guild.create_voice_channel(f"🔊 {member.display_name}'s room",category=category)
                await ch.set_permissions(member,connect=True,manage_channels=True,move_members=True)
                await member.move_to(ch)
                await bot.db.execute("INSERT OR REPLACE INTO temp_voice(channel_id,guild_id,owner_id) VALUES(?,?,?)",(ch.id,member.guild.id,member.id))
                await bot.db.commit()
            except discord.HTTPException: log.exception("Temporary voice creation failed")
    if before.channel:
        cur=await bot.db.execute("SELECT owner_id FROM temp_voice WHERE channel_id=?",(before.channel.id,))
        row=await cur.fetchone()
        if row and not before.channel.members:
            try: await before.channel.delete(reason="Temporary voice empty")
            except discord.HTTPException: pass
            await bot.db.execute("DELETE FROM temp_voice WHERE channel_id=?",(before.channel.id,))
            await bot.db.commit()

async def owned_voice(i,ch):
    cur=await bot.db.execute("SELECT owner_id FROM temp_voice WHERE channel_id=?",(ch.id,))
    row=await cur.fetchone()
    if not row or (row[0]!=i.user.id and not i.user.guild_permissions.manage_channels):
        await i.response.send_message("Only the room owner or a channel manager can do that.",ephemeral=True)
        return False
    return True

@bot.tree.command(name="vcname",description="Rename a temporary voice channel")
async def vcname(i, channel: discord.VoiceChannel, name: str):
    if not await owned_voice(i,channel): return
    if not name.strip() or len(name)>90: return await i.response.send_message("Name must be 1–90 characters.",ephemeral=True)
    await channel.edit(name=name); await i.response.send_message("Voice room renamed.",ephemeral=True)

@bot.tree.command(name="vclock",description="Lock a temporary voice channel")
async def vclock(i, channel: discord.VoiceChannel):
    if not await owned_voice(i,channel): return
    await channel.set_permissions(i.guild.default_role,connect=False)
    await i.response.send_message("🔒 Locked.",ephemeral=True)

@bot.tree.command(name="vcunlock",description="Unlock a temporary voice channel")
async def vcunlock(i, channel: discord.VoiceChannel):
    if not await owned_voice(i,channel): return
    await channel.set_permissions(i.guild.default_role,connect=None)
    await i.response.send_message("🔓 Unlocked.",ephemeral=True)

@bot.tree.command(name="vclimit",description="Set temporary voice user limit")
async def vclimit(i, channel: discord.VoiceChannel, limit: app_commands.Range[int,0,99]):
    if not await owned_voice(i,channel): return
    await channel.edit(user_limit=limit)
    await i.response.send_message(f"Limit set to {limit or 'unlimited'}.",ephemeral=True)

# -------------------- ECONOMY / LEVELS --------------------
async def ensure_wallet(gid,uid):
    await bot.db.execute("INSERT OR IGNORE INTO economy(guild_id,user_id,wallet,bank) VALUES(?,?,0,0)",(gid,uid))
    await bot.db.commit()

@bot.tree.command(name="balance",description="View your wallet and bank")
async def balance(i):
    await ensure_wallet(i.guild.id,i.user.id)
    cur=await bot.db.execute("SELECT wallet,bank FROM economy WHERE guild_id=? AND user_id=?",(i.guild.id,i.user.id))
    w,b=await cur.fetchone()
    await i.response.send_message(embed=em("💰 Balance",f"Wallet: **{w:,}**\nBank: **{b:,}**"))

@bot.tree.command(name="daily",description="Claim daily coins")
async def daily(i):
    await ensure_wallet(i.guild.id,i.user.id)
    cur=await bot.db.execute("SELECT last_daily FROM economy WHERE guild_id=? AND user_id=?",(i.guild.id,i.user.id))
    last=(await cur.fetchone())[0]; now=datetime.now(timezone.utc)
    if last and now-datetime.fromisoformat(last)<timedelta(hours=24):
        return await i.response.send_message("Daily reward already claimed. Try again later.",ephemeral=True)
    amount=random.randint(100,250)
    await bot.db.execute("UPDATE economy SET wallet=wallet+?,last_daily=? WHERE guild_id=? AND user_id=?",(amount,now.isoformat(),i.guild.id,i.user.id))
    await bot.db.commit()
    await i.response.send_message(f"🎁 You earned **{amount}** coins.")

@bot.tree.command(name="work",description="Earn coins by working")
async def work(i):
    await ensure_wallet(i.guild.id,i.user.id)
    amount=random.randint(30,120)
    await bot.db.execute("UPDATE economy SET wallet=wallet+? WHERE guild_id=? AND user_id=?",(amount,i.guild.id,i.user.id))
    await bot.db.commit()
    await i.response.send_message(f"🧰 You earned **{amount}** coins.")

@bot.tree.command(name="rank",description="View XP and level")
async def rank(i, member: discord.Member=None):
    m=member or i.user
    cur=await bot.db.execute("SELECT xp,level FROM levels WHERE guild_id=? AND user_id=?",(i.guild.id,m.id))
    row=await cur.fetchone(); xp,level=row if row else (0,0)
    await i.response.send_message(embed=em(f"⭐ {m.display_name}",f"Level: **{level}**\nProgress: **{xp%100}/100 XP**"))

# -------------------- MUSIC (requires external Lavalink) --------------------
def music_ready():
    return wavelink is not None and bool(LAVALINK_URI and LAVALINK_PASSWORD)

@bot.tree.command(name="play",description="Play a song or add it to the queue (Lavalink required)")
async def play(i, query: str):
    if not music_ready(): return await i.response.send_message("🎵 Music is not configured. See README: run Lavalink and set LAVALINK_URI / LAVALINK_PASSWORD.",ephemeral=True)
    if not i.user.voice or not i.user.voice.channel: return await i.response.send_message("Join a voice channel first.",ephemeral=True)
    await i.response.defer()
    player=i.guild.voice_client
    if not player:
        player=await i.user.voice.channel.connect(cls=wavelink.Player)
    tracks=await wavelink.Playable.search(query)
    if not tracks: return await i.followup.send("No tracks found.")
    track=tracks[0]
    await player.queue.put_wait(track)
    if not player.playing:
        next_track=await player.queue.get_wait()
        await player.play(next_track)
    await i.followup.send(f"🎶 Added **{track.title}** to the queue.")

@bot.tree.command(name="skip",description="Skip the current track")
async def skip(i):
    player=i.guild.voice_client
    if not player: return await i.response.send_message("I am not playing in this server.",ephemeral=True)
    if not i.user.voice or i.user.voice.channel != player.channel:
        return await i.response.send_message("Join my voice channel first.",ephemeral=True)
    await player.skip(); await i.response.send_message("⏭️ Skipped.")

@bot.tree.command(name="stop",description="Stop music and leave voice")
async def stop(i):
    player=i.guild.voice_client
    if not player: return await i.response.send_message("I am not in voice.",ephemeral=True)
    if not i.user.voice or i.user.voice.channel != player.channel:
        return await i.response.send_message("Join my voice channel first.",ephemeral=True)
    await player.disconnect(); await i.response.send_message("⏹️ Stopped and disconnected.")


# -------------------- V3 ADVANCED SECURITY --------------------
import re
from urllib.parse import urlparse

SUSPICIOUS_SHORTENERS = {"bit.ly", "tinyurl.com", "t.co", "cutt.ly", "is.gd", "rb.gy"}
SCAM_TERMS = ("free-nitro", "discord-nitro", "steam-gift", "gift-nitro", "discordapp-gift")

async def is_trusted(guild_id: int, user_id: int) -> bool:
    if user_id == guild_id:  # harmless sentinel; actual owner check follows
        return True
    cur = await bot.db.execute("SELECT 1 FROM trusted_users WHERE guild_id=? AND user_id=?", (guild_id, user_id))
    return (await cur.fetchone()) is not None

async def security_alert(guild, title, details):
    await log_event(guild, title, details)
    if guild.system_channel:
        try:
            await guild.system_channel.send(embed=em(title, details, discord.Color.red()))
        except discord.HTTPException:
            pass

async def record_security_event(guild_id, actor_id, action, details):
    await bot.db.execute(
        "INSERT INTO security_events(guild_id,actor_id,action,created,details) VALUES(?,?,?,?,?)",
        (guild_id, actor_id, action, datetime.now(timezone.utc).isoformat(), details[:500])
    )
    await bot.db.commit()

def extract_domains(content: str):
    found = []
    for raw in re.findall(r"https?://[^\s<>()]+|www\.[^\s<>()]+", content, flags=re.I):
        raw = raw.rstrip(".,!?;:)]}")
        candidate = raw if "://" in raw else "https://" + raw
        try:
            host = (urlparse(candidate).hostname or "").lower().strip(".")
            if host:
                found.append(host)
        except ValueError:
            continue
    return found

def looks_like_phishing(content: str, domains):
    lower = content.lower()
    if any(term in lower for term in SCAM_TERMS):
        return True
    for host in domains:
        if host in SUSPICIOUS_SHORTENERS:
            return True
        if host.startswith("xn--") or ".xn--" in host:
            return True
        # Discord lookalike hostnames frequently used in credential theft.
        if ("discord" in host and not (host == "discord.com" or host.endswith(".discord.com")
                                       or host == "discordapp.com" or host.endswith(".discordapp.com"))):
            return True
    return False

@bot.tree.command(name="trustuser", description="Add a trusted user to security allow-list")
@app_commands.default_permissions(manage_guild=True)
async def trustuser(i, member: discord.Member):
    if not await moderator(i): return
    await bot.db.execute("INSERT OR IGNORE INTO trusted_users(guild_id,user_id) VALUES(?,?)", (i.guild.id, member.id))
    await bot.db.commit()
    await i.response.send_message(f"✅ {member.mention} added to the security trusted list.", ephemeral=True)

@bot.tree.command(name="untrustuser", description="Remove a user from security allow-list")
@app_commands.default_permissions(manage_guild=True)
async def untrustuser(i, member: discord.Member):
    if not await moderator(i): return
    await bot.db.execute("DELETE FROM trusted_users WHERE guild_id=? AND user_id=?", (i.guild.id, member.id))
    await bot.db.commit()
    await i.response.send_message(f"✅ {member.mention} removed from the trusted list.", ephemeral=True)

@bot.tree.command(name="antinuke", description="Enable or disable anti-nuke audit-log monitoring")
@app_commands.default_permissions(manage_guild=True)
@app_commands.choices(state=[app_commands.Choice(name="on", value="on"), app_commands.Choice(name="off", value="off")])
async def antinuke(i, state: app_commands.Choice[str]):
    if not await moderator(i): return
    await cfg(i.guild.id, "anti_nuke", state.value)
    await i.response.send_message(
        f"🛡️ Anti-nuke monitoring is now **{state.value.upper()}**. "
        "It detects bursts of destructive admin actions, logs the executor, and applies a cautious timeout where Discord permissions allow.",
        ephemeral=True
    )

@bot.tree.command(name="blockdomain", description="Add a domain to the server's message link blocklist")
@app_commands.default_permissions(manage_guild=True)
async def blockdomain(i, domain: str):
    if not await moderator(i): return
    domain = domain.lower().strip().replace("https://", "").replace("http://", "").split("/")[0]
    if not re.fullmatch(r"[a-z0-9.-]{1,253}", domain) or "." not in domain:
        return await i.response.send_message("Enter a valid domain, e.g. `example.com`.", ephemeral=True)
    await bot.db.execute("INSERT OR IGNORE INTO blocked_domains(guild_id,domain) VALUES(?,?)", (i.guild.id, domain))
    await bot.db.commit()
    await i.response.send_message(f"⛔ Added `{domain}` to the blocklist.", ephemeral=True)

@bot.tree.command(name="unblockdomain", description="Remove a domain from the server's message link blocklist")
@app_commands.default_permissions(manage_guild=True)
async def unblockdomain(i, domain: str):
    if not await moderator(i): return
    domain = domain.lower().strip().replace("https://", "").replace("http://", "").split("/")[0]
    await bot.db.execute("DELETE FROM blocked_domains WHERE guild_id=? AND domain=?", (i.guild.id, domain))
    await bot.db.commit()
    await i.response.send_message(f"✅ Removed `{domain}` from the blocklist.", ephemeral=True)

@bot.tree.command(name="securityevents", description="View the latest anti-nuke and link-security events")
@app_commands.default_permissions(manage_guild=True)
async def securityevents(i):
    if not await moderator(i): return
    cur = await bot.db.execute(
        "SELECT actor_id,action,created,details FROM security_events WHERE guild_id=? ORDER BY id DESC LIMIT 8",
        (i.guild.id,)
    )
    rows = await cur.fetchall()
    body = "\n".join(f"• <@{r[0]}> — **{r[1]}** — {r[3]} ({r[2][:16]})" for r in rows) or "No security events recorded."
    await i.response.send_message(embed=em("🛡️ Recent security events", body), ephemeral=True)

@bot.event
async def on_message(message):
    # Keep the existing XP/custom-trigger/anti-spam handler and add link screening.
    if message.author.bot or not message.guild:
        return
    gid = message.guild.id
    domains = extract_domains(message.content)
    if domains:
        cur = await bot.db.execute("SELECT domain FROM blocked_domains WHERE guild_id=?", (gid,))
        blocked = {r[0] for r in await cur.fetchall()}
        matched = [d for d in domains if any(d == b or d.endswith("." + b) for b in blocked)]
        if matched or looks_like_phishing(message.content, domains):
            if not message.author.guild_permissions.manage_messages:
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
                await record_security_event(gid, message.author.id, "link_block", ", ".join(matched or domains))
                await security_alert(message.guild, "🚫 Suspicious link blocked",
                                     f"Deleted a suspicious link from {message.author.mention} in {message.channel.mention}.\nDomains: `{', '.join(matched or domains)}`")
                return
    # Call the original message handler logic via helper to avoid replacing existing features.
    await original_on_message(message)

# Preserve the previous V2 message handler under a stable reference.


@bot.event
async def on_audit_log_entry_create(entry):
    guild = entry.guild
    if await cfg(guild.id, "anti_nuke") != "on":
        return
    destructive = {
        discord.AuditLogAction.channel_delete: "channel_delete",
        discord.AuditLogAction.role_delete: "role_delete",
        discord.AuditLogAction.ban: "member_ban",
        discord.AuditLogAction.kick: "member_kick",
        discord.AuditLogAction.bot_add: "bot_add",
    }
    label = destructive.get(entry.action)
    if not label:
        return
    actor = entry.user
    if not actor or actor.id == guild.owner_id or (OWNER_ID and actor.id == OWNER_ID):
        return
    if await is_trusted(guild.id, actor.id):
        return
    details = f"Executor: {actor} (`{actor.id}`); target: `{getattr(entry.target, 'id', 'unknown')}`"
    await record_security_event(guild.id, actor.id, label, details)
    # Count recent destructive actions by the same actor; avoid reacting to a single mistake.
    cur = await bot.db.execute(
        "SELECT COUNT(*) FROM security_events WHERE guild_id=? AND actor_id=? AND created>=?",
        (guild.id, actor.id, (datetime.now(timezone.utc)-timedelta(seconds=20)).isoformat())
    )
    count = (await cur.fetchone())[0]
    if count >= 3:
        member = guild.get_member(actor.id)
        if member and guild.me.guild_permissions.moderate_members and not member.guild_permissions.administrator:
            try:
                await member.timeout(timedelta(minutes=10), reason="Anti-nuke: destructive action burst")
            except discord.HTTPException:
                pass
        await security_alert(guild, "🚨 Anti-nuke alert",
                             f"{actor.mention} performed **{count}** destructive actions within 20 seconds.\n{details}\nReview audit logs immediately.")

# -------------------- OWNER --------------------
@bot.tree.command(name="botstats",description="Owner: bot statistics")
async def botstats(i):
    if not await owner_only(i): return
    await i.response.send_message(embed=em("👑 Bot Stats",f"Servers: **{len(bot.guilds)}**\nUsers cached: **{len(bot.users)}**\nLatency: **{round(bot.latency*1000)}ms**"))

@bot.tree.command(name="shutdown",description="Owner: shut down the bot")
async def shutdown(i):
    if not await owner_only(i): return
    await i.response.send_message("Shutting down.")
    await bot.close()

@bot.tree.error
async def app_error(i,error):
    log.exception("Application command failed",exc_info=error)
    msg="Something went wrong. Check the bot logs and permissions."
    try:
        if i.response.is_done(): await i.followup.send(msg,ephemeral=True)
        else: await i.response.send_message(msg,ephemeral=True)
    except discord.HTTPException: pass

async def main():
    if not TOKEN: raise RuntimeError("DISCORD_TOKEN is missing. Set it in your host's environment variables.")
    async with bot:
        await bot.start(TOKEN)

if __name__=="__main__":
    asyncio.run(main())

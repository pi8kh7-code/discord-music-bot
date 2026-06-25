import os
import asyncio
import discord
from discord.ext import commands
import yt_dlp

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")

if not DISCORD_BOT_TOKEN:
    raise RuntimeError(
        "DISCORD_BOT_TOKEN environment variable is not set. "
        "Please add it to your Replit secrets."
    )

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

YDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "postprocessors": [{
        "key": "FFmpegExtractAudio",
        "preferredcodec": "opus",
    }],
}

FFMPEG_OPTIONS = {
    "before_options": (
        "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
    ),
    "options": "-vn",
}

voice_clients: dict[int, discord.VoiceClient] = {}


def fetch_audio_info(query: str) -> dict:
    with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
        if query.startswith("http://") or query.startswith("https://"):
            info = ydl.extract_info(query, download=False)
        else:
            info = ydl.extract_info(f"ytsearch:{query}", download=False)
            if "entries" in info and info["entries"]:
                info = info["entries"][0]
            else:
                return None
        return info


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("Bot is ready. Use !play <song> and !stop in a Discord server.")
    # Send an explicit "leave voice channel" signal to Discord's gateway for
    # every guild. This clears any stale server-side session from a previous
    # run — without this, Discord rejects the next connect() with error 4006.
    for guild in bot.guilds:
        try:
            if guild.voice_client is not None:
                await guild.voice_client.disconnect(force=True)
            # change_voice_state(channel=None) tells Discord's gateway the bot
            # is no longer in any voice channel, resetting the session state.
            await guild.change_voice_state(channel=None)
            print(f"Cleared voice state for {guild.name}")
        except Exception as e:
            print(f"Could not clear voice state for {guild.name}: {e}")


async def _connect_with_retry(
    ctx: commands.Context,
    channel: discord.VoiceChannel,
    guild_id: int,
    max_attempts: int = 2,
) -> discord.VoiceClient | None:
    """
    Try to join a voice channel up to max_attempts times.
    On 4006 (session no longer valid), clear voice state and wait 2s before
    retrying — Discord's voice server needs that gap to expire the old session.
    """
    for attempt in range(1, max_attempts + 1):
        # Force-clear any lingering voice state before each attempt so
        # Discord's voice server doesn't see a duplicate/stale session.
        try:
            if ctx.guild.voice_client is not None:
                await ctx.guild.voice_client.disconnect(force=True)
            await ctx.guild.change_voice_state(channel=None)
            await asyncio.sleep(1.0)   # give Discord time to propagate
        except Exception:
            pass

        try:
            vc = await channel.connect(reconnect=False)
            voice_clients[guild_id] = vc
            return vc
        except discord.errors.ConnectionClosed as e:
            print(f"Voice connect attempt {attempt} failed with code {e.code}")
            if attempt < max_attempts:
                await ctx.send(
                    f"Voice session rejected (error {e.code}), retrying in 2 s..."
                )
                await asyncio.sleep(2.0)
            else:
                voice_clients.pop(guild_id, None)
                await ctx.send(
                    f"Could not join voice after {max_attempts} attempts "
                    f"(Discord error {e.code}). Please try `!play` again in a moment."
                )
                return None
        except discord.ClientException as e:
            await ctx.send(f"Failed to connect to voice channel: {e}")
            return None
        except Exception as e:
            voice_clients.pop(guild_id, None)
            await ctx.send(f"Unexpected error joining voice: {e}")
            return None
    return None


@bot.command(name="play")
async def play(ctx: commands.Context, *, query: str):
    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.send("You need to be in a voice channel to use this command.")
        return

    voice_channel = ctx.author.voice.channel

    guild_id = ctx.guild.id
    vc = voice_clients.get(guild_id)

    if vc is None or not vc.is_connected():
        vc = await _connect_with_retry(ctx, voice_channel, guild_id)
        if vc is None:
            return
    elif vc.channel != voice_channel:
        try:
            await vc.move_to(voice_channel)
        except (discord.errors.ConnectionClosed, Exception) as e:
            voice_clients.pop(guild_id, None)
            await ctx.send(f"Lost voice connection while switching channels. Please try again.")
            return

    if vc.is_playing():
        vc.stop()

    await ctx.send(f"Searching for: **{query}**...")

    try:
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, fetch_audio_info, query)

        if info is None:
            await ctx.send("No results found for your search.")
            return

        audio_url = info.get("url")
        title = info.get("title", "Unknown title")

        if not audio_url:
            await ctx.send("Could not retrieve audio stream. Try a different song.")
            return

        source = discord.FFmpegPCMAudio(audio_url, **FFMPEG_OPTIONS)
        vc.play(
            discord.PCMVolumeTransformer(source, volume=0.5),
            after=lambda e: print(f"Player error: {e}") if e else None,
        )

        await ctx.send(f"Now playing: **{title}**")

    except yt_dlp.utils.DownloadError as e:
        await ctx.send(f"Could not play that song. Error: {e}")
    except Exception as e:
        await ctx.send(f"An unexpected error occurred: {e}")
        print(f"Error in !play: {e}")


@bot.command(name="stop")
async def stop(ctx: commands.Context):
    guild_id = ctx.guild.id
    vc = voice_clients.get(guild_id)

    if vc is None or not vc.is_connected():
        await ctx.send("I'm not in a voice channel right now.")
        return

    if vc.is_playing():
        vc.stop()

    await vc.disconnect()
    voice_clients.pop(guild_id, None)
    await ctx.send("Stopped playback and disconnected from the voice channel.")


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(
            "Missing argument. Usage: `!play <song name or URL>`"
        )
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        await ctx.send(f"An error occurred: {error}")
        print(f"Command error: {error}")


bot.run(DISCORD_BOT_TOKEN)

import os
import asyncio
import discord
from discord.ext import commands
import yt_dlp

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")

if not DISCORD_BOT_TOKEN:
    raise RuntimeError(
        "DISCORD_BOT_TOKEN environment variable is not set. "
        "Please add it to your Railway variables."
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
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}


def fetch_audio_info(query: str):
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
    print("Bot is ready! Use !play <song> and !stop")


@bot.command(name="play")
async def play(ctx: commands.Context, *, query: str):
    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.send("Bạn cần vào voice channel trước!")
        return

    voice_channel = ctx.author.voice.channel

    try:
        if ctx.voice_client is None:
            await voice_channel.connect(timeout=30.0, reconnect=True)
        elif ctx.voice_client.channel != voice_channel:
            await ctx.voice_client.move_to(voice_channel)
    except Exception as e:
        await ctx.send(f"Không vào được voice channel: {e}\nThử lại sau vài giây nhé!")
        return

    vc = ctx.voice_client

    if vc.is_playing():
        vc.stop()

    await ctx.send(f"Đang tìm: **{query}**...")

    try:
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, fetch_audio_info, query)

        if info is None:
            await ctx.send("Không tìm thấy bài hát.")
            return

        audio_url = info.get("url")
        title = info.get("title", "Unknown")

        if not audio_url:
            await ctx.send("Không lấy được link audio. Thử bài khác nhé!")
            return

        source = discord.FFmpegPCMAudio(audio_url, **FFMPEG_OPTIONS)
        vc.play(
            discord.PCMVolumeTransformer(source, volume=0.5),
            after=lambda e: print(f"Player error: {e}") if e else None,
        )

        await ctx.send(f"Đang phát: **{title}**")

    except yt_dlp.utils.DownloadError as e:
        await ctx.send(f"Không phát được bài này: {e}")
    except Exception as e:
        await ctx.send(f"Lỗi: {e}")
        print(f"Error in !play: {e}")


@bot.command(name="stop")
async def stop(ctx: commands.Context):
    if ctx.voice_client is None:
        await ctx.send("Bot chưa ở trong voice channel nào.")
        return

    if ctx.voice_client.is_playing():
        ctx.voice_client.stop()

    await ctx.voice_client.disconnect()
    await ctx.send("Đã dừng và thoát voice channel.")


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Dùng: `!play <tên bài hoặc link>`")
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        await ctx.send(f"Lỗi: {error}")
        print(f"Command error: {error}")


bot.run(DISCORD_BOT_TOKEN)

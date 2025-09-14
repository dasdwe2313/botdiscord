# esta source requer instalar via pip esses 4 packages:
# pip3 install -U yt_dlp spotipy PyNaCl git+https://github.com/Rapptz/discord.py.git

import datetime
import asyncio
import os
import sys
import traceback
from functools import partial
from random import shuffle
from typing import Optional
import discord
import spotipy
from discord import app_commands
from discord.ext import commands
from yt_dlp import YoutubeDL
import re

admsg = '[Entra aqui não doidão](https://images2.alphacoders.com/548/thumb-1920-548568.jpg)'

URL_REG = re.compile(r'https?://(?:www\.)?.+')
YOUTUBE_VIDEO_REG = re.compile(r"(https?://)?(www\.)?youtube\.(com|nl)/watch\?v=([-\w]+)")
spotify_regex = re.compile("https://open.spotify.com?.+(album|playlist|track)/([a-zA-Z0-9]+)")

filters = {
    'nightcore': 'aresample=48000,asetrate=48000*1.25'
}


def utc_time():
    return datetime.datetime.now(datetime.timezone.utc)


def fix_spotify_data(data: dict):
    try:
        return data["track"]
    except KeyError:
        data = {"track": data}
        return data


YDL_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'retries': 5,
    'extract_flat': False,
    'cachedir': False,
    'extractor_args': {
        'youtube': {
            'skip': [
                'hls',
                'dash'
            ],
            'player_skip': [
                'js',
                'configs',
                'webpage'
            ]
        },
        'youtubetab': ['webpage']
    }
}

FFMPEG_OPTIONS = {
    'before_options': '-nostdin'
                      ' -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}


def fix_characters(text: str):
    replaces = [
        ('&quot;', '"'),
        ('&amp;', '&'),
        ('(', '\u0028'),
        (')', '\u0029'),
        ('[', '【'),
        (']', '】'),
        ("  ", " "),
        ("*", '"'),
        ("_", ' '),
        ("{", "\u0028"),
        ("}", "\u0029"),
    ]
    for r in replaces:
        text = text.replace(r[0], r[1])

    return text


ytdl = YoutubeDL(YDL_OPTIONS)
YDL_OPTIONS_PLAYLIST = dict(YDL_OPTIONS)
YDL_OPTIONS_PLAYLIST['extract_flat'] = 'in_playlist'
ytdl_playlist = YoutubeDL(YDL_OPTIONS_PLAYLIST)


def is_requester():
    def predicate(inter):
        player = inter.bot.players.get(inter.guild.id)
        if not player:
            return True
        if inter.author.guild_permissions.manage_channels:
            return True
        if inter.author.voice and not any(
                m for m in inter.author.voice.channel.members if not m.bot and m.guild_permissions.manage_channels):
            return True
        if player.current['requester'] == inter.author:
            return True

    return commands.check(predicate)


class TestBot(commands.Bot):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    async def setup_bot(self):
        await self.wait_until_ready()
        await self.tree.sync()
        print(f'Logado como: {self.user} [{self.user.id}]')

    async def setup_hook(self):
        await self.add_cog(MusicCog(self))
        self.loop.create_task(self.setup_bot())


class YTDLSource(discord.PCMVolumeTransformer):

    def __init__(self, source):
        super().__init__(source)

    @classmethod
    async def source(cls, url, *, ffmpeg_opts):
        return cls(discord.FFmpegPCMAudio(url, **ffmpeg_opts))


class MusicCog(commands.Cog):

    def __init__(self, bot):

        if not hasattr(bot, 'players'):
            bot.players = {}

        self.bot = bot

        try:
            client_id = os.getenv("SPOTIFY_CLIENT_ID") or "1216669999fd47409bb4374b94b024e6"
            client_secret = os.getenv("SPOTIFY_CLIENT_SECRET") or "55781f55f7644847b6ba44d84039f653"

            self.spotify = spotipy.Spotify(
                auth_manager=spotipy.SpotifyClientCredentials(
                    client_id=client_id,
                    client_secret=client_secret
                )
            )
        except Exception as e:
            self.spotify = None
            print(f"Ocorreu um erro ao tentar carregar o suporte ao spotify: {repr(e)}")

    def get_player(self, ctx, create=False):
        try:
            player = self.bot.players[ctx.guild.id]
        except KeyError:
            if create is False:
                return
            player = MusicPlayer(ctx, self)
            self.bot.players[ctx.guild.id] = player

        return player

    async def destroy_player(self, guild_id: int):

        try:
            player: MusicPlayer = self.bot.players[guild_id]
        except KeyError:
            return

        player.exiting = True
        player.loop = False

        try:
            player.timeout_task.cancel()
        except:
            pass

        if player.guild.me.voice:
            await player.guild.voice_client.disconnect()
        elif player.guild.voice_client:
            player.guild.voice_client.cleanup()

        try:
            del self.bot.players[guild_id]
        except KeyError:
            pass

    # searching the item on youtube
    async def search_yt(self, item):

        if (yt_url := YOUTUBE_VIDEO_REG.match(item)):
            item = yt_url.group()

        elif not URL_REG.match(item):
            item = f"ytsearch:{item}"

        to_run = partial(ytdl_playlist.extract_info, url=item, download=False)
        info = await self.bot.loop.run_in_executor(None, to_run)

        try:
            entries = info["entries"]
        except KeyError:
            entries = [info]

        if info["extractor_key"] == "YoutubeSearch":
            entries = entries[:1]

        tracks = []

        for t in entries:

            if not (duration := t.get('duration')):
                continue

            url = t.get('webpage_url') or t['url']

            if not URL_REG.match(url):
                url = f"https://www.youtube.com/watch?v={url}"

            tracks.append(
                {
                    'url': url,
                    'title': fix_characters(t['title']),
                    'uploader': t.get('uploader', 'Desconhecido'),
                    'duration': duration
                }
            )

        return tracks

    @commands.hybrid_command(name="play", aliases=["p", "tocar"], description="Tocar uma música do YouTube/Spotify")
    @app_commands.describe(query="Nome ou link da música")
    async def play(self, ctx, *, query: str):

        if not ctx.author.voice:
            # if voice_channel is None:
            # you need to be connected so that the bot knows where to go
            embedvc = discord.Embed(
                colour=1646116,  # grey
                description='Para tocar uma música, primeiro se conecte a um canal de voz.'
            )
            await ctx.send(embed=embedvc, ephemeral=True)
            return

        query = query.strip("<>")

        try:
            await ctx.defer(ephemeral=True)
            songs = await self.get_spotify_tracks(query)
            if not songs:
                songs = await self.search_yt(query)
        except Exception as e:
            traceback.print_exc()
            embedvc = discord.Embed(
                colour=12255232,  # red
                description=f'**Algo deu errado ao processar sua busca:**\n```css\n{repr(e)}```'
            )
            await ctx.send(embed=embedvc)
            return

        if not songs:
            embedvc = discord.Embed(
                colour=12255232,  # red
                description=f'Não houve resultados para sua busca: **{query}**'
            )
            await ctx.send(embed=embedvc)
            return

        player = self.get_player(ctx, create=True)

        vc_channel = ctx.author.voice.channel

        if (size := len(songs)) > 1:
            txt = f"Você adicionou **{size} músicas** na fila!"
        else:
            txt = f"Você adicionou a música **{songs[0]['title']}** à fila!"

        for song in songs:
            song['requester'] = ctx.author
            player.queue.append(song)

        embedvc = discord.Embed(
            colour=32768,  # green
            description=f"{txt}"
        )
        await ctx.send(embed=embedvc)

        if not ctx.guild.voice_client or not ctx.guild.voice_client.is_connected():
            player.channel = vc_channel
            await vc_channel.connect(reconnect=False)

        if not ctx.guild.voice_client.is_playing() or ctx.guild.voice_client.is_paused():
            await player.process_next()

    @commands.hybrid_command(name="queue", aliases=["q", "fila"], description="Mostra as atuais músicas da fila.")
    async def q(self, ctx):

        player = self.get_player(ctx)

        if not player:
            await ctx.send("Não há players ativo no momento...", ephemeral=True)
            return

        if not player.queue:
            embedvc = discord.Embed(
                colour=1646116,
                description='Não existe músicas na fila no momento.'
            )
            await ctx.send(embed=embedvc, ephemeral=True)
            return

        retval = ""

        def limit(text):
            if len(text) > 30:
                return text[:28] + "..."
            return text

        for n, i in enumerate(player.queue[:20]):
            retval += f'**{n + 1} | `{datetime.timedelta(seconds=i["duration"])}` - ** [{limit(i["title"])}]({i["url"]}) | {i["requester"].mention}\n'

        if (qsize := len(player.queue)) > 20:
            retval += f"\nE mais **{qsize - 20}** música(s)"

        embedvc = discord.Embed(
            colour=12255232,
            description=f"{retval}"
        )
        await ctx.send(embed=embedvc, ephemeral=True)

    @is_requester()
    @commands.hybrid_command(name="skip", aliases=["s", "pular"], description="Pula a música atual que está tocando.")
    async def skip(self, ctx):

        player = self.get_player(ctx)

        if not player:
            await ctx.send("Não há players ativo no momento...", ephemeral=True)
            return

        if not ctx.guild.voice_client or not ctx.guild.voice_client.is_playing():
            await ctx.send("Não estou tocando algo...", ephemeral=True)
            return

        embedvc = discord.Embed(description="**Música pulada.**", color=discord.Colour.green())

        await ctx.send(embed=embedvc)
        player.loop = False
        ctx.guild.voice_client.stop()

    @commands.hybrid_command(aliases=["pausar"], description="Pausar a música")
    async def pause(self, ctx: commands.Context):

        player: MusicPlayer = self.get_player(ctx)

        embed = discord.Embed(color=discord.Colour.red())

        if not player:
            embed.description = "**Não estou tocando algo no momento.**"
            await ctx.send(embed=embed, ephemeral=True)
            return

        if ctx.guild.voice_client.is_paused():
            embed.description = "**A música já está em pausa.**"
            await ctx.send(embed=embed, ephemeral=True)
            return

        ctx.guild.voice_client.pause()

        embed.color = ctx.guild.me.color
        embed.description = "**A música foi pausada com sucesso.**"
        await ctx.send(embed=embed)

    @commands.hybrid_command(aliases=["retomar", "despausar"], description="Despausar a música")
    async def resume(self, ctx: commands.Context):

        player: MusicPlayer = self.get_player(ctx)

        embed = discord.Embed(color=discord.Colour.red())

        if not player:
            embed.description = "**Não estou tocando algo no momento.**"
            await ctx.send(embed=embed, ephemeral=True)
            return

        if not ctx.guild.voice_client.is_paused():
            embed.description = "**A música já está tocando.**"
            await ctx.send(embed=embed, ephemeral=True)
            return

        ctx.guild.voice_client.resume()

        embed.color = ctx.guild.me.color
        embed.description = "**A música foi retomada com sucesso.**"
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="shuffle", aliases=["misturar", "sf"], description="Misturar as músicas da fila")
    async def shuffle_(self, ctx):

        embed = discord.Embed(color=discord.Colour.red())

        player = self.get_player(ctx)

        if not player:
            embed.description = "Não estou tocando algo no momento."
            await ctx.send(embed=embed, ephemeral=True)
            return

        if len(player.queue) < 3:
            embed.description = "A fila tem que ter no mínimo 3 músicas para ser misturada."
            await ctx.send(embed=embed, ephemeral=True)
            return

        shuffle(player.queue)

        embed.description = f"**Você misturou as músicas da fila.**"
        embed.colour = discord.Colour.green()
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="loop", aliases=["l", "repetir"],
                             description="Ativar/Desativar a repetição da música atual")
    async def repeat(self, ctx):

        embed = discord.Embed(color=discord.Colour.red())

        player = self.get_player(ctx)

        if not player:
            embed.description = "Não estou tocando algo no momento."
            await ctx.send(embed=embed, ephemeral=True)
            return

        player.loop = not player.loop

        embed.colour = discord.Colour.green()
        embed.description = f"**Repetição {'ativada para a música atual' if player.loop else 'desativada'}.**"

        await ctx.send(embed=embed)

    @commands.hybrid_command(name="nightcore", aliases=["nc"],
                             description="Ativar/Desativar o efeito nightcore (Música acelerada com tom mais agudo).")
    async def nightcore(self, ctx):

        embed = discord.Embed(color=discord.Colour.red())

        player = self.get_player(ctx)

        if not player:
            embed.description = "Não estou tocando algo no momento."
            await ctx.send(embed=embed, ephemeral=True)
            return

        player.nightcore = not player.nightcore
        player.queue.insert(0, player.current)
        player.no_message = True

        ctx.guild.voice_client.stop()

        embed.description = f"**Efeito nightcore {'ativado' if player.nightcore else 'desativado'}.**"
        embed.colour = discord.Colour.green()

        await ctx.send(embed=embed)

    @commands.hybrid_command(name="stop", aliases=["sp", "parar"],
                             description="Parar o player e me desconectar do canal de voz.")
    async def stop(self, ctx):

        embedvc = discord.Embed(colour=12255232)

        player = self.get_player(ctx)

        if not player:
            embedvc.description = "Não estou tocando algo no momento."
            await ctx.send(embed=embedvc, ephemeral=True)
            return

        if not ctx.me.voice:
            embedvc.description = "Não estou conectado em um canal de voz."
            await ctx.send(embed=embedvc, ephemeral=True)
            return

        if not ctx.author.voice or ctx.author.voice.channel != ctx.me.voice.channel:
            embedvc.description = "Você precisa estar no meu canal de voz atual para usar esse comando."
            await ctx.send(embed=embedvc, ephemeral=True)
            return

        if any(m for m in ctx.me.voice.channel.members if
               not m.bot and m.guild_permissions.manage_channels) and not ctx.author.guild_permissions.manage_channels:
            embedvc.description = "No momento você não tem permissão para usar esse comando."
            await ctx.send(embed=embedvc, ephemeral=True)
            return

        await self.destroy_player(ctx.guild.id)

        embedvc.colour = 1646116
        embedvc.description = "Você parou o player"
        await ctx.send(embed=embedvc)

    @commands.Cog.listener("on_voice_state_update")
    async def player_vc_disconnect(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):

        if member.id != self.bot.user.id:
            return

        if after.channel:
            return

        player: MusicPlayer = self.bot.players.get(member.guild.id)

        if not player:
            return

        if player.exiting:
            return

        embed = discord.Embed(description="**Desligando player por desconexão do canal.**", color=member.color)

        await player.text_channel.send(embed=embed)

        await self.destroy_player(member.guild.id)

    @is_requester()
    @commands.hybrid_command(name="volume", aliases=["vol", "v"], description="Alterar volume da música")
    @app_commands.describe(value="nível entre 5 a 100")
    async def volume(self, ctx, value: int):

        if value < 5 or value > 100:
            return await ctx.send("coloque um valor entre 5 a 100", ephemeral=True)

        vc = ctx.guild.voice_client

        if not vc or not vc.is_connected():
            return await ctx.send('Não estou em um canal de voz!', ephemeral=True)

        player = self.get_player(ctx)

        if vc.source:
            vc.source.volume = value / 100

        player.volume = value / 100
        embed = discord.Embed(description=f"**Volume alterado para {value}%**", color=ctx.guild.me.color)
        await ctx.send(embed=embed)

    @commands.command(name="help", alisases=['ajuda'], description="Comando de ajuda")
    async def help_(self, ctx: commands.Context):
        helptxt = ''
        for command in self.bot.commands:
            helptxt += f'**{command}** - {command.description}\n'
        embedhelp = discord.Embed(
            colour=1646116,  # grey
            title=f'Comandos do {self.bot.user.name}',
            description=helptxt + f'\n{admsg}' if admsg else ''
        )
        try:
            embedhelp.set_thumbnail(url=self.bot.user.avatar.url)
        except AttributeError:
            pass
        await ctx.send(embed=embedhelp)

    @commands.hybrid_command(description="exibir meu link de convite")
    async def invite(self, ctx):

        await ctx.send(
            embed=discord.Embed(
                description=f"[`clique aqui`](https://discord.com/api/oauth2/authorize?client_id={ctx.bot.user.id}&"
                            "permissions=2150950976&scope=bot%20applications.commands) para me adicionar no seu server",
                color=ctx.guild.me.color
            )
        )

    async def get_spotify_tracks(self, query: str):

        if not (matches := spotify_regex.match(query)):
            return

        if not self.spotify:
            return

        url_type, url_id = matches.groups()

        if url_type == "track":
            t = self.spotify.track(url_id)

            return [{
                "title": t['name'],
                "url": t['external_urls']['spotify'],
                "webpage_url": t['external_urls']['spotify'],
                "uploader": t['artists'][0]['name'],
                "duration": t['duration_ms'] / 1000,
                "id": "",
                "thumbnail": t['album']['images'][0]['url'],
                "ie_key": "Spotify",
            }]

        if url_type == "album":
            result = self.spotify.album(url_id)
            tracks = [fix_spotify_data(i) for i in result['tracks']['items']]

        else:  # playlist
            result = self.spotify.playlist(playlist_id=url_id)
            tracks = result['tracks']['items']

        return [{
            "title": i['track']['name'],
            "url": i['track']['external_urls']['spotify'],
            "webpage_url": i['track']['external_urls']['spotify'],
            "uploader": i['track']['artists'][0]['name'],
            "duration": i['track']['duration_ms'] / 1000,
            "id": "",
            "thumbnail": i['track']['album']['images'][0]['url'],
            "ie_key": "Spotify"
        } for i in tracks]

    async def cog_command_error(self, ctx, error: Exception):

        error = getattr(error, 'original', error)

        traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)

        if isinstance(error, commands.CommandNotFound):
            return

        embed = discord.Embed(
            description=f"**Ocorreu um erro ao executar o comando:** ```py\n{repr(error)[:1920]}```",
            color=discord.Colour.red()
        )

        await ctx.send(embed=embed, ephemeral=True)


class MusicPlayer:

    def __init__(self, ctx, cog: MusicCog):
        self.guild_id = ctx.guild.id
        self.cog = cog
        self.bot = cog.bot
        self.guild = ctx.guild
        self.text_channel = ctx.channel
        self.queue = []
        self.current = None
        self.event = asyncio.Event()
        self.now_playing = None
        self.timeout_task = None
        self.channel: Optional[discord.VoiceChannel] = None
        self.disconnect_timeout = 180
        self.loop = False
        self.exiting = False
        self.nightcore = False
        self.fx = []
        self.no_message = False
        self.locked = False
        self.volume = 100

    async def player_timeout(self):
        await asyncio.sleep(self.disconnect_timeout)
        self.exiting = True
        await self.text_channel.send(embed=discord.Embed(
            description="**Saí do canal de voz por inatividade.**",
            color=discord.Colour.yellow())
        )
        self.bot.loop.create_task(self.cog.destroy_player(self.guild_id))

    async def process_next(self):

        self.event.clear()

        if self.locked:
            return

        if self.exiting:
            return

        try:
            self.timeout_task.cancel()
        except:
            pass

        if not self.queue:
            self.timeout_task = self.bot.loop.create_task(self.player_timeout())

            embed = discord.Embed(
                description="As músicas acabaram...",
                color=discord.Colour.red())
            await self.text_channel.send(embed=embed)
            return

        await self.start_play()

    async def renew_url(self):

        info = self.queue.pop(0)

        self.current = info

        try:
            if info['formats']:
                return info
        except KeyError:
            pass

        if info.get("ie_key") == "Spotify":
            url = f"ytsearch:{info['title']} - {info['uploader']}"
        else:
            try:
                url = info['webpage_url']
            except KeyError:
                url = info['url']

        to_run = partial(ytdl.extract_info, url=url, download=False)
        new_info = await self.bot.loop.run_in_executor(None, to_run)

        try:
            # track spotify apenas adicionar link direto sem sobreescrever as infos originais.
            info["formats"] = new_info["entries"][0]["formats"]
        except KeyError:
            info = new_info

        return info

    def ffmpeg_after(self, e):

        if e:
            print(f"ffmpeg error: {e}")

        self.event.set()

    async def start_play(self):

        await self.bot.wait_until_ready()

        if self.exiting:
            return

        self.event.clear()

        try:
            info = await self.renew_url()
        except Exception as e:
            traceback.print_exc()
            try:
                await self.text_channel.send(embed=discord.Embed(
                    description=f"**Ocorreu um erro durante a reprodução da música:\n[{self.current['title']}]({self.current['webpage_url']})** ```css\n{e}\n```",
                    color=discord.Colour.red()))
            except:
                pass
            self.locked = True
            await asyncio.sleep(6)
            self.locked = False
            await self.process_next()
            return

        url = ""
        for format in info['formats']:
            if format['ext'] == 'm4a':
                url = format['url']
                break
        if not url:
            url = info['formats'][0]['url']

        ffmpg_opts = dict(FFMPEG_OPTIONS)

        self.fx = []

        if self.nightcore:
            self.fx.append(filters['nightcore'])

        if self.fx:
            ffmpg_opts['options'] += (f" -af \"" + ", ".join(self.fx) + "\"")

        try:
            if self.channel != self.guild.me.voice.channel:
                self.channel = self.guild.me.voice.channel
                await self.guild.voice_client.move_to(self.channel)
        except AttributeError:
            print("teste: Bot desconectado após obter download da info.")
            return

        source = await YTDLSource.source(url, ffmpeg_opts=ffmpg_opts)
        source.volume = self.volume / 100

        try:
            self.guild.voice_client.play(source, after=lambda e: self.ffmpeg_after(e))

            if self.no_message:
                self.no_message = False
            else:
                try:
                    embed = discord.Embed(
                        description=f"**Tocando agora:**\n[**{info['title']}**]({info['webpage_url']})\n\n**Uploader:** `"
                                    f"{info['uploader']}`\n\n**Duração:** `{datetime.timedelta(seconds=int(info['duration']))}`",
                        color=self.guild.me.colour,
                    )

                    thumb = info.get('thumbnail')

                    if self.loop:
                        embed.description += " **| Repetição:** `ativada`"

                    if self.nightcore:
                        embed.description += " **| Nightcore:** `Ativado`"

                    if admsg:
                        embed.description += f" | {admsg}"

                    if thumb:
                        embed.set_thumbnail(url=thumb)

                    self.now_playing = await self.text_channel.send(embed=embed)

                except Exception:
                    traceback.print_exc()

            await self.event.wait()

            source.cleanup()

        except Exception as e:
            traceback.print_exc()
            try:
                await self.text_channel.send(embed=discord.Embed(
                    description=f"**Ocorreu um erro ao reproduzir música:\n[{self.current['title']}]({self.current['webpage_url']})** ```css\n{e}\n```",
                    color=discord.Colour.red()))
            except:
                pass

        if self.loop:
            self.queue.insert(0, self.current)
            self.no_message = True

        self.current = None

        await self.process_next()


async def setup(bot):
    bot.remove_command("help")
    await bot.add_cog(MusicCog(bot))


if __name__ == "__main__":

    try:
        bot_secret = os.environ["TOKEN"] # não é pra colocar token aqui do lado
    except KeyError:
        bot_secret = "colocar token do bot aqui (dentro das aspas)"

    intents = discord.Intents.default()
    intents.members = True
    intents.message_content = True

    bot = TestBot(command_prefix="!", intents=intents, help_command=None)

    bot.run(bot_secret)

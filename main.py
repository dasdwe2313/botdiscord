import traceback
import discord
from discord.ext import commands
import os
from dotenv import load_dotenv

load_dotenv()

intents = discord.Intents.default()
intents.members = True
intents.message_content = True


class MyBot(commands.Bot):

    def __init__(self, command_prefix: str):
        super().__init__(
            intents=intents,
            command_prefix=commands.when_mentioned_or(command_prefix)
        )

    async def sync_commands(self):
        await self.wait_until_ready()
        await self.tree.sync()
        print(f"Comandos sincronizados com sucesso!")
        print(f"Entramos como: {self.user} [{self.user.id}]")

    async def setup_hook(self):

        # carregar cogs/extensões da pasta cogs
        if os.path.isdir('./cogs'):
            for filename in os.listdir('./cogs'):
                if filename.endswith('.py'):
                    try:
                        await self.load_extension(f'cogs.{filename[:-3]}')
                        print(f"Carregado: {filename}")
                    except Exception:
                        print(f"Falha ao carregar: {filename}")
                        traceback.print_exc()

        self.loop.create_task(self.sync_commands())


client = MyBot(command_prefix=".")

@client.event
async def on_ready():
    print('Entramos como {0.user}'.format(client))

    await client.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening, name="É O ANTARES MELHOR BAILE DA CIDADE (( 2015 )) 160 BPM [ WENDEL CZR ]"))

try:
    bot_secret = os.environ["TOKEN"] # não é pra colocar token aqui do lado
except KeyError:
    bot_secret = "OTA0MDY2MTc2MDk3ODQ5NDM0.GZT0WW.g7bytMDH5B2TNprNfE-dfB1QKsaeEVyb1wJz0Q"

client.run(bot_secret)

from .voicemaster import Voicemaster

async def setup(bot):
    await bot.add_cog(Voicemaster(bot))

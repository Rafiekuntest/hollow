from .music import MusicCog


async def setup(bot):
    await bot.add_cog(MusicCog(bot))

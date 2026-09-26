from .security import Security

async def setup(bot):
    await bot.add_cog(Security(bot))

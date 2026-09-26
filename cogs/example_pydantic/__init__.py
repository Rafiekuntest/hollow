"""
Example cog demonstrating Pydantic config system.
This cog shows how to use the new type-safe configuration.
"""
from .example_pydantic import ExamplePydantic

async def setup(bot):
    await bot.add_cog(ExamplePydantic(bot))
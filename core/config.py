import os
from enum import Enum


class EMOJIS:
    DENY = "<:deny:1547570956191535144>"
    APPROVE = "<:approve:1547570974457733141>"
    WARN = "<:warning:1547570970791772270>"
    COOLDOWN = "<:cooldown:1547572522004914218>"
    PREVIOUS = "<:left:1547570958754390086>"
    NEXT = "<:right:1547570961857904710>"
    NAVIGATE = "<:skipto:1547570966731816980>"
    CANCEL = "<:cancel:1547570977813299290>"
    ONLINE = "<:online:1551949541253582919>"
    OFFLINE = "<:offline:1551949560446713956>"
    MOBILE = "<:mobile:1551949528704229407>"
    STREAMING = "<:streaming:1551949520965869690>"
    SPEAKER = "<:speaker:1551949524719767762>"
    DND = "<:dnd:1551949538296860722>"
    IDLE = "<:idle:1551949556160401428>"

class ButtonStyle(str, Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    SUCCESS = "success"
    DANGER = "danger"
    WARNING = "warning"
    BLURPLE = "blurple"
    GREY = "grey"


class COLORS:
    approve = 0xa7e77c
    warn = 0xfcd33e
    deny = 0xff6463
    neutral = 0x4c5a67


class DEV:
    JOIN_LOG_CHANNEL = "1547941435532255262"
    LEAVE_LOG_CHANNEL = "1550081681904377976"
    GUILD_LOG_CHANNEL = "1533378171750322216"
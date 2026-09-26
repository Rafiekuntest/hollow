import sys, os, discord, re
from discord.ext import commands
from typing import Union, Optional, Tuple, Dict, Any, List

from core.client.embed import Embed


# ──────────────────────────────────────────────────────────────────────────────
# Components V2 helpers (requires discord.py >= 2.6)
# ──────────────────────────────────────────────────────────────────────────────

HAS_CV2 = hasattr(discord.ui, "LayoutView")

# Tags that only exist in the V2 syntax. (`image`, `thumbnail`, `button`, ... are
# deliberately NOT here because the classic embed syntax already uses them.)
V2_TAGS = frozenset({"container", "text", "gallery", "section", "separator", "row"})

# How long views with clickable buttons stay alive. None = until bot restart.
VIEW_TIMEOUT: Optional[float] = None

_TRUE = {"true", "yes", "1", "on"}
_CUSTOM_EMOJI_RE = re.compile(r"^<a?:\w{2,32}:\d{15,25}>$")

_BUTTON_STYLES = {
    "red": "danger", "danger": "danger",
    "green": "success", "success": "success",
    "gray": "secondary", "grey": "secondary", "secondary": "secondary",
    "blue": "primary", "blurple": "primary", "primary": "primary",
    "link": "link", "url": "link",
}


def _groups(s: str) -> List[str]:
    """Contents of every top-level ``{...}`` group in ``s`` (brace-balanced)."""
    out, depth, start = [], 0, 0
    for i, ch in enumerate(s):
        if ch == "{":
            if depth == 0:
                start = i + 1
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                out.append(s[start:i])
    return out


def _tag(group: str) -> Tuple[str, str]:
    """``'text: hello'`` -> ``('text', 'hello')``; ``'separator'`` -> ``('separator', '')``."""
    name, _, args = group.partition(":")
    return name.strip().lower(), args.strip()


def _split(s: str, keep_empty: bool = False) -> List[str]:
    """Split on ``&&`` but only outside of nested braces."""
    parts, buf, depth, i = [], [], 0, 0
    while i < len(s):
        ch = s[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(depth - 1, 0)
        elif ch == "&" and depth == 0 and s.startswith("&&", i):
            parts.append("".join(buf).strip())
            buf, i = [], i + 2
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf).strip())
    return parts if keep_empty else [p for p in parts if p]


def _kv(parts: List[str]) -> Tuple[Dict[str, List[str]], List[str]]:
    """Separate ``key=value`` parts (repeatable keys) from positional parts."""
    kv: Dict[str, List[str]] = {}
    pos: List[str] = []
    for p in parts:
        m = re.match(r"^([A-Za-z_]+)\s*=\s*(.*)$", p, re.S)
        if m:
            kv.setdefault(m.group(1).lower(), []).append(m.group(2).strip())
        else:
            pos.append(p)
    return kv, pos


def _first(kv: Dict[str, List[str]], *keys: str, default: Optional[str] = None) -> Optional[str]:
    for k in keys:
        if kv.get(k):
            return kv[k][0]
    return default


def _bool(v: Optional[str]) -> bool:
    return v is not None and v.strip().lower() in _TRUE


def _text(s: str) -> str:
    return s.replace("\\n", "\n")


def _parse_color(raw: str) -> Optional[int]:
    raw = raw.strip().lower()
    m = re.fullmatch(r"(?:#|0x)?([0-9a-f]{6})", raw)
    if m:
        return int(m.group(1), 16)
    m = re.fullmatch(r"#([0-9a-f]{3})", raw)
    if m:
        return int("".join(c * 2 for c in m.group(1)), 16)
    return None


def _clean_emoji(emoji: Optional[str]) -> Optional[str]:
    """Discord rejects invalid emoji with a 400, so drop anything that clearly isn't one."""
    if not emoji:
        return None
    emoji = emoji.strip()
    if _CUSTOM_EMOJI_RE.match(emoji) or not emoji.isascii():
        return emoji
    return None


_URL_RE = re.compile(r"^(?:https?://[^\s/?#]+\.[^\s/?#]+\S*|attachment://\S+)$", re.I)
_BARE_DOMAIN_RE = re.compile(r"^[\w-]+(?:\.[\w-]+)+(?:[/?#]\S*)?$")


def _clean_url(u: Optional[str]) -> Optional[str]:
    """Return a URL Discord will accept, or None. Bare domains get https:// prepended."""
    if not u:
        return None
    u = u.strip()
    if _URL_RE.match(u):
        return u
    if _BARE_DOMAIN_RE.match(u):
        return "https://" + u
    return None


def _named_args(s: str, keys: Tuple[str, ...], aliases: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """
    Parse ``&&``-separated args that may be named (``name: x``) or positional (``x``).
    Positional args fill ``keys`` in order, so both of these are equivalent:
        name: Bob && icon: https://a.png      /      Bob && https://a.png
    """
    aliases = aliases or {}
    allowed = set(keys) | set(aliases)
    out: Dict[str, str] = {}
    pos = 0
    for p in _split(s, keep_empty=True):
        m = re.match(r"^(\w+)\s*[:=]\s*(.*)$", p, re.S)
        if m and m.group(1).lower() in allowed:
            k = m.group(1).lower()
            out[aliases.get(k, k)] = m.group(2).strip()
        else:
            if p and pos < len(keys):
                out.setdefault(keys[pos], p)
            pos += 1
    return out


def _parse_media(args: str) -> List[Dict[str, Any]]:
    """
    Parse gallery / thumbnail arguments.
        url1 url2            -> two items
        url && description=x -> description applies to the previous url
        url && spoiler       -> spoiler applies to the previous url
    """
    items: List[Dict[str, Any]] = []
    for chunk in _split(args):
        kv, _ = _kv([chunk])
        if "description" in kv or "desc" in kv:
            if items:
                desc = (kv.get("description") or kv.get("desc"))[0]
                items[-1]["description"] = desc[:1024] or None
        elif "spoiler" in kv or chunk.lower() == "spoiler":
            if items:
                items[-1]["spoiler"] = _bool(kv["spoiler"][0]) if "spoiler" in kv else True
        elif "url" in kv:
            items.append({"media": kv["url"][0], "description": None, "spoiler": False})
        else:
            for u in chunk.split():
                items.append({"media": u, "description": None, "spoiler": False})
    cleaned = []
    for it in items:
        url = _clean_url(it["media"])
        if url:
            it["media"] = url
            cleaned.append(it)
    return cleaned


def _make_callback(action: Optional[str], ephemeral: bool):
    payload = None
    if action:
        payload = action.strip()
        # action={{content: hi}}  ->  {content: hi}
        if payload.startswith("{") and payload.endswith("}"):
            payload = payload[1:-1].strip()

    async def callback(interaction: discord.Interaction):
        if not payload:
            await interaction.response.defer()
            return
        await EmbedBuilder.respond(interaction, payload, ephemeral)

    return callback


_BUTTON_KEYS = ("label", "url", "emoji", "style", "id", "custom_id", "row", "action", "disabled", "ephemeral")
_BUTTON_KEY_RE = re.compile(r"^(" + "|".join(_BUTTON_KEYS) + r")\s*:\s*(.*)$", re.I | re.S)


def _build_button(args: str, used_ids: set, classic: bool = False) -> discord.ui.Button:
    # accept both `label=x` and `label: x`
    parts = []
    for p in _split(args):
        m = _BUTTON_KEY_RE.match(p)
        parts.append(f"{m.group(1)}={m.group(2)}" if m else p)
    kv, pos = _kv(parts)

    label = _first(kv, "label")
    url = _clean_url(_first(kv, "url"))
    emoji = _clean_emoji(_first(kv, "emoji"))
    dv = _first(kv, "disabled")
    disabled = any(p.lower() == "disabled" for p in pos) or (dv is not None and (dv == "" or _bool(dv)))

    style_name = _BUTTON_STYLES.get((_first(kv, "style", default="secondary") or "").lower(), "secondary")
    style = getattr(discord.ButtonStyle, style_name)

    if not label and not emoji:
        label = "Button"
    if label:
        label = label[:80]

    common: Dict[str, Any] = {"label": label, "emoji": emoji, "disabled": disabled}

    # `row=` only makes sense in classic views; V2 buttons live inside ActionRows
    raw_row = _first(kv, "row")
    if classic and raw_row is not None:
        try:
            if 0 <= int(raw_row) <= 4:
                common["row"] = int(raw_row)
        except ValueError:
            pass

    # Link button
    if url:
        return discord.ui.Button(style=discord.ButtonStyle.link, url=url, **common)
    if style == discord.ButtonStyle.link:  # link style without url can't work
        style = discord.ButtonStyle.secondary

    custom_id = _first(kv, "id", "custom_id")
    if custom_id:
        base, cid, n = custom_id[:90], custom_id[:90], 1
        while cid in used_ids:  # Discord rejects duplicate custom_ids in one message
            n += 1
            cid = f"{base}_{n}"
        used_ids.add(cid)
        common["custom_id"] = cid

    button = discord.ui.Button(style=style, **common)

    eph_raw = _first(kv, "ephemeral")
    ephemeral = True if eph_raw is None else _bool(eph_raw)
    button.callback = _make_callback(_first(kv, "action"), ephemeral)
    return button


def _build_accessory(value: str, used_ids: set):
    groups = _groups(value)
    if not groups and value.startswith(("http://", "https://")):
        return discord.ui.Thumbnail(media=value)
    for g in groups:
        name, args = _tag(g)
        if name in ("image", "thumbnail"):
            media = _parse_media(args)
            if media:
                return discord.ui.Thumbnail(**media[0])
        elif name == "button":
            return _build_button(args, used_ids)
    return None


def _build_section(args: str, used_ids: set) -> list:
    kv, _ = _kv(_split(args))
    texts = [_text(t) for key in ("content", "text") for t in kv.get(key, []) if t]
    if not texts:
        texts = ["\u200b"]

    accessory = _build_accessory(kv["accessory"][0], used_ids) if kv.get("accessory") else None
    if accessory is None:  # sections require an accessory, degrade to plain text
        return [discord.ui.TextDisplay(t) for t in texts]
    return [discord.ui.Section(*texts[:3], accessory=accessory)]


def _build_separator(args: str) -> discord.ui.Separator:
    kv, pos = _kv(_split(args))
    spacing = (_first(kv, "spacing", "size", default=pos[0] if pos else "small") or "small").lower()
    vis = _first(kv, "visible", "divider")
    visible = True if vis is None else _bool(vis)
    if any(p.lower() in ("invisible", "hidden") for p in pos):
        visible = False
    return discord.ui.Separator(
        visible=visible,
        spacing=discord.SeparatorSpacing.large if spacing in ("large", "l", "big") else discord.SeparatorSpacing.small,
    )


def _button_rows(buttons: list) -> list:
    """Buttons must live in an ActionRow (max 5 per row)."""
    return [discord.ui.ActionRow(*buttons[i:i + 5]) for i in range(0, len(buttons), 5)]


def _build_children(groups: List[str], used_ids: set) -> list:
    items: list = []
    buttons: list = []

    for g in groups:
        name, args = _tag(g)

        if name == "button":  # consecutive buttons share one ActionRow
            buttons.append(_build_button(args, used_ids))
            continue

        if buttons:
            items.extend(_button_rows(buttons))
            buttons = []

        if name == "text":
            if args:
                items.append(discord.ui.TextDisplay(_text(args)))
        elif name in ("gallery", "image"):
            media = _parse_media(args)
            if media:
                gallery = discord.ui.MediaGallery()
                for m in media[:10]:
                    gallery.add_item(**m)
                items.append(gallery)
        elif name == "section":
            items.extend(_build_section(args, used_ids))
        elif name == "separator":
            items.append(_build_separator(args))
        elif name == "row":
            row_buttons = [
                _build_button(a, used_ids) for n, a in map(_tag, _groups(args)) if n == "button"
            ]
            items.extend(_button_rows(row_buttons))

    if buttons:
        items.extend(_button_rows(buttons))
    return items


def _build_container(args: str, used_ids: set) -> Optional[discord.ui.Container]:
    color: Optional[int] = None
    spoiler = False
    child_groups: List[str] = []

    for chunk in _split(args):
        if chunk.startswith("{"):
            child_groups.extend(_groups(chunk))
            continue

        bare = _parse_color(chunk)
        if bare is not None:
            color = bare
            continue

        kv, _ = _kv([chunk])
        if "color" in kv or "colour" in kv:
            parsed = _parse_color(_first(kv, "color", "colour") or "")
            if parsed is not None:
                color = parsed
        elif "spoiler" in kv:
            spoiler = _bool(kv["spoiler"][0])
        elif chunk.lower() == "spoiler":
            spoiler = True

    children = _build_children(child_groups, used_ids)
    if not children:
        return None

    kwargs: Dict[str, Any] = {"spoiler": spoiler}
    if color is not None:
        kwargs["accent_colour"] = discord.Colour(color)
    return discord.ui.Container(*children, **kwargs)


# ──────────────────────────────────────────────────────────────────────────────


class EmbedBuilder:
    @staticmethod
    def ordinal(num: int) -> str:
        """Convert from number to ordinal (10 - 10th)"""
        numb = str(num)
        if numb.startswith("0"):
            numb = numb.strip("0") or "0"
        if numb in ["11", "12", "13"]:
            return numb + "th"
        if numb.endswith("1"):
            return numb + "st"
        elif numb.endswith("2"):
            return numb + "nd"
        elif numb.endswith("3"):
            return numb + "rd"
        else:
            return numb + "th"

    @staticmethod
    def get_parts(params: str) -> list[str]:
        if not params:
            return []
        params = params.replace("{embed}", "").replace("{EMBED}", "").strip()

        # New layout: {content: x}{title: y}{field: a && b}...  (brace-balanced, no $v)
        if "$v" not in params:
            groups = _groups(params)
            if groups:
                return [g.strip() for g in groups if g.strip()]

        # Classic layout: title: x$vdescription: y
        parts = []
        for p in params.split("$v"):
            p = p.strip()
            if not p:
                continue
            if p.startswith("{") and p.endswith("}"):
                p = p[1:-1].strip()
            if p:
                parts.append(p)
        return parts

    @staticmethod
    def embed_replacement(user: Union[discord.Member, discord.User], params: str = None) -> Optional[str]:
        if params is None:
            return None

        guild = getattr(user, "guild", None)

        if "{user}" in params:
            params = params.replace(
                "{user}", str(user.name) + "#" + str(getattr(user, "discriminator", "0"))
            )
        if "{user.mention}" in params:
            params = params.replace("{user.mention}", user.mention)
        if "{user.name}" in params:
            params = params.replace("{user.name}", user.name)
        if "{user.avatar}" in params:
            params = params.replace("{user.avatar}", str(user.display_avatar.url))
        if "{user.joined_at}" in params:
            joined_at = getattr(user, "joined_at", None)
            params = params.replace(
                "{user.joined_at}",
                discord.utils.format_dt(joined_at, style="R") if joined_at else "N/A",
            )
        if "{user.created_at}" in params:
            params = params.replace(
                "{user.created_at}",
                discord.utils.format_dt(user.created_at, style="R") if user.created_at else "N/A",
            )
        if "{user.discriminator}" in params:
            params = params.replace("{user.discriminator}", str(getattr(user, "discriminator", "0")))

        if guild:
            if "{guild.name}" in params:
                params = params.replace("{guild.name}", guild.name)
            if "{guild.count}" in params:
                params = params.replace("{guild.count}", str(guild.member_count or len(guild.members)))
            if "{guild.count.format}" in params:
                params = params.replace(
                    "{guild.count.format}",
                    EmbedBuilder.ordinal(guild.member_count or len(guild.members)),
                )
            if "{guild.id}" in params:
                params = params.replace("{guild.id}", str(guild.id))
            if "{guild.created_at}" in params:
                params = params.replace(
                    "{guild.created_at}",
                    discord.utils.format_dt(guild.created_at, style="R") if guild.created_at else "N/A",
                )
            if "{guild.boost_count}" in params:
                params = params.replace(
                    "{guild.boost_count}", str(guild.premium_subscription_count or 0)
                )
            if "{guild.booster_count}" in params:
                params = params.replace(
                    "{guild.booster_count}", str(len(getattr(guild, "premium_subscribers", [])))
                )
            if "{guild.boost_count.format}" in params:
                params = params.replace(
                    "{guild.boost_count.format}",
                    EmbedBuilder.ordinal(guild.premium_subscription_count or 0),
                )
            if "{guild.booster_count.format}" in params:
                params = params.replace(
                    "{guild.booster_count.format}",
                    EmbedBuilder.ordinal(len(getattr(guild, "premium_subscribers", []))),
                )
            if "{guild.boost_tier}" in params:
                params = params.replace("{guild.boost_tier}", str(guild.premium_tier))
            if "{guild.vanity}" in params:
                vanity = getattr(guild, "vanity_url_code", None)
                params = params.replace(
                    "{guild.vanity}",
                    f"/{vanity}" if vanity else "none",
                )
            if "{guild.icon}" in params:
                if guild.icon:
                    params = params.replace("{guild.icon}", guild.icon.url)
                else:
                    params = params.replace("{guild.icon}", "https://none.none")
        else:
            for tag in [
                "{guild.name}", "{guild.count}", "{guild.count.format}",
                "{guild.id}", "{guild.created_at}", "{guild.boost_count}",
                "{guild.booster_count}", "{guild.boost_count.format}",
                "{guild.booster_count.format}", "{guild.boost_tier}",
                "{guild.vanity}", "{guild.icon}"
            ]:
                params = params.replace(tag, "N/A")

        if "{invisible}" in params:
            params = params.replace("{invisible}", "2B2D31")
        if "{botcolor}" in params:
            params = params.replace("{botcolor}", "7d7ead")

        return params

    # ── Components V2 ─────────────────────────────────────────────────────────

    @staticmethod
    def is_components_v2(params: str) -> bool:
        """True if the script uses V2 tags ({container}, {text}, {gallery}, {section}, {separator}, {row})."""
        if not params or "{" not in params:
            return False
        return any(_tag(g)[0] in V2_TAGS for g in _groups(params))

    @staticmethod
    def to_layout(params: str) -> Optional["discord.ui.LayoutView"]:
        """
        Build a Components V2 LayoutView from a script. Returns None if nothing was built.

        Syntax:
            {container: #hex && <children>}
            {text: markdown text}
            {gallery: url url2 && description=alt && spoiler}
            {section: content=text && accessory={image: url}}        (or accessory={button: ...})
            {separator}   {separator: spacing=large && visible=false}
            {button: label=x && style=red && emoji=😀 && disabled=true && id=custom_id
                     && action={{content: hello}} && ephemeral=false}
            {button: label=Site && url=https://example.com}
            {row: {button: ...} {button: ...}}                       (explicit ActionRow)
        Consecutive {button}s are grouped into ActionRows automatically (5 per row).
        A button's `action` is a normal embed script, or a V2 script: action={{container: ...}}.
        """
        if not HAS_CV2:
            raise RuntimeError("Components V2 requires discord.py 2.6 or newer.")

        params = params.replace("{embed}", "").replace("{EMBED}", "").strip()
        used_ids: set = set()
        items: list = []
        pending: List[str] = []

        for g in _groups(params):
            name, args = _tag(g)
            if name == "container":
                if pending:
                    items.extend(_build_children(pending, used_ids))
                    pending = []
                container = _build_container(args, used_ids)
                if container:
                    items.append(container)
            else:
                pending.append(g)
        if pending:
            items.extend(_build_children(pending, used_ids))

        if not items:
            return None

        view = discord.ui.LayoutView(timeout=VIEW_TIMEOUT)
        for item in items:
            view.add_item(item)
        return view

    @staticmethod
    async def respond(interaction: discord.Interaction, script: str, ephemeral: bool = True):
        """Reply to a component interaction with a classic-embed or V2 script."""
        processed = EmbedBuilder.embed_replacement(interaction.user, script) or script

        if EmbedBuilder.is_components_v2(processed):
            layout = EmbedBuilder.to_layout(processed)
            if layout:
                return await interaction.response.send_message(view=layout, ephemeral=ephemeral)

        content, embed, view = await EmbedBuilder.to_object(processed)
        kwargs: Dict[str, Any] = {}
        if content:
            kwargs["content"] = content
        if embed:
            kwargs["embed"] = embed
        if view and len(view.children) > 0:
            kwargs["view"] = view
        if not kwargs:
            kwargs["content"] = processed
        await interaction.response.send_message(ephemeral=ephemeral, **kwargs)

    # ── Classic embeds ────────────────────────────────────────────────────────

    @staticmethod
    async def to_object(params: str) -> Tuple[Optional[str], Optional[Embed], discord.ui.View]:
        """
        Classic embed script -> (content, embed, view). Both layouts work:
            {content: hi}{embed}{title: x}{author: name: A && icon: url}{field: name: N && value: V && inline: true}
            title: x$vauthor: A && url$vfield: N && V && true
        """
        x: Dict[str, Any] = {}
        fields: List[Dict[str, Any]] = []
        content = None
        view = discord.ui.View(timeout=VIEW_TIMEOUT)
        used_ids: set = set()

        for part in EmbedBuilder.get_parts(params):
            key, _, val = part.partition(":")
            key, val = key.strip().lower(), val.strip()

            if key == "content":
                if val:
                    content = val

            elif key == "title":
                if val:
                    x["title"] = val[:256]

            elif key == "description":
                if val:
                    x["description"] = val[:4096]

            elif key in ("color", "colour"):
                raw_color = val.replace("#", "").replace("0x", "")
                try:
                    x["color"] = int(raw_color, 16)
                except Exception:
                    x["color"] = 0x2F3136

            elif key == "image":
                url = _clean_url(val)
                if url:
                    x["image"] = {"url": url}

            elif key == "thumbnail":
                url = _clean_url(val)
                if url:
                    x["thumbnail"] = {"url": url}

            elif key == "author":
                a = _named_args(val, ("name", "icon_url", "url"), {"icon": "icon_url"})
                name = a.get("name") or None
                icon_url = _clean_url(a.get("icon_url"))
                url = _clean_url(a.get("url"))

                if name or icon_url or url:
                    author_dict: Dict[str, Any] = {"name": (name or "\u200b")[:256]}
                    if icon_url:
                        author_dict["icon_url"] = icon_url
                    if url:
                        author_dict["url"] = url
                    x["author"] = author_dict

            elif key == "field":
                a = _named_args(val, ("name", "value", "inline"))
                name = (a.get("name") or "\u200b")[:256]
                value = (a.get("value") or "\u200b")[:1024]
                inline = a.get("inline", "true").strip().lower() not in ("false", "no", "0")
                if len(fields) < 25:
                    fields.append({"name": name, "value": value, "inline": inline})

            elif key == "footer":
                a = _named_args(val, ("text", "icon_url"), {"icon": "icon_url"})
                text = a.get("text") or None
                icon_url = _clean_url(a.get("icon_url"))

                if text or icon_url:
                    footer_dict: Dict[str, Any] = {"text": (text or "\u200b")[:2048]}
                    if icon_url:
                        footer_dict["icon_url"] = icon_url
                    x["footer"] = footer_dict

            elif key == "button":
                try:
                    view.add_item(_build_button(val, used_ids, classic=True))
                except ValueError:  # row full / more than 25 buttons
                    pass

        if fields:
            x["fields"] = fields

        embed = Embed.from_dict(x) if x else None
        return content, embed, view


class EmbedScript(commands.Converter):
    async def convert(self, ctx: commands.Context, argument: str):
        processed = EmbedBuilder.embed_replacement(ctx.author, argument) or argument

        # Components V2 -> {"view": LayoutView}. Note: V2 messages can't have content/embeds.
        if EmbedBuilder.is_components_v2(processed):
            try:
                layout = EmbedBuilder.to_layout(processed)
            except RuntimeError as e:
                raise commands.BadArgument(str(e))
            if layout:
                return {"view": layout}
            return {"content": processed}

        content, embed, view = await EmbedBuilder.to_object(processed)

        res = {}
        if content:
            res["content"] = content
        if embed:
            res["embed"] = embed
        if view and len(view.children) > 0:
            res["view"] = view

        if res:
            return res
        return {"content": processed}


async def send_embed(destination, message, member):
    processed_message = EmbedBuilder.embed_replacement(member, message) or message

    if EmbedBuilder.is_components_v2(processed_message):
        layout = EmbedBuilder.to_layout(processed_message)
        if layout:
            return await destination.send(view=layout)
        return await destination.send(content=processed_message)

    content, embed, view = await EmbedBuilder.to_object(processed_message)
    await destination.send(
        content=content or (processed_message if not embed else None),
        embed=embed,
        view=view if (view and len(view.children) > 0) else None,
    )
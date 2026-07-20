import asyncio
import html
import ipaddress
import logging
import os
import re
import socket
import time
from collections import defaultdict
from typing import Optional 

import aiohttp
from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import BotCommand, Message


# ============================================================
# CONFIGURACIÓN
# ============================================================

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

RATE_LIMIT = int(os.getenv("RATE_LIMIT", "10"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "12"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("universal-search-bot")

router = Router()

DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}$)"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z]{2,63}$",
    re.IGNORECASE,
)

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{2,64}$")

request_history: dict[int, list[float]] = defaultdict(list)


# ============================================================
# UTILIDADES
# ============================================================

def get_command_argument(message: Message) -> str:
    text = message.text or ""
    parts = text.split(maxsplit=1)

    if len(parts) != 2 or not parts[1].strip():
        raise ValueError("Falta el objetivo después del comando.")

    return parts[1].strip()


def check_rate_limit(user_id: int) -> bool:
    now = time.monotonic()

    recent_requests = [
        timestamp
        for timestamp in request_history[user_id]
        if now - timestamp < 60
    ]

    request_history[user_id] = recent_requests

    if len(recent_requests) >= RATE_LIMIT:
        return False

    recent_requests.append(now)
    return True


def classify_target(raw_value: str) -> tuple[str, str]:
    value = raw_value.strip().removeprefix("@")

    if not value:
        raise ValueError("El objetivo está vacío.")

    try:
        address = ipaddress.ip_address(value)
        return "ip", str(address)
    except ValueError:
        pass

    if DOMAIN_PATTERN.fullmatch(value):
        return "domain", value.lower()

    if USERNAME_PATTERN.fullmatch(value):
        return "username", value

    raise ValueError(
        "Objetivo inválido. Usa un username, dominio o dirección IP."
    )


def validate_forced_target(target_type: str, value: str) -> str:
    value = value.strip().removeprefix("@")

    if target_type == "username":
        if not USERNAME_PATTERN.fullmatch(value):
            raise ValueError("El valor no parece un username válido.")
        return value

    if target_type == "domain":
        if not DOMAIN_PATTERN.fullmatch(value):
            raise ValueError("El valor no parece un dominio válido.")
        return value.lower()

    if target_type == "ip":
        try:
            return str(ipaddress.ip_address(value))
        except ValueError as error:
            raise ValueError(
                "El valor no parece una dirección IP válida."
            ) from error

    raise ValueError("Tipo de búsqueda desconocido.")


def safe_text(value: object) -> str:
    return html.escape(str(value))


async def fetch_json(
    session: aiohttp.ClientSession,
    url: str,
    headers: Optional[dict[str, str]] = None,
) -> Optional[dict]:
    try:
        async with session.get(
            url,
            headers=headers,
            allow_redirects=True,
        ) as response:
            if response.status != 200:
                logger.info("HTTP %s para %s", response.status, url)
                return None

            data = await response.json(content_type=None)

            if isinstance(data, dict):
                return data

    except asyncio.TimeoutError:
        logger.warning("Timeout consultando %s", url)

    except aiohttp.ClientError as error:
        logger.warning("Error HTTP consultando %s: %s", url, error)

    except ValueError:
        logger.warning("Respuesta JSON inválida desde %s", url)

    return None


# ============================================================
# BÚSQUEDA DE USERNAMES
# ============================================================

USERNAME_SOURCES = {
    # --- REDES SOCIALES GENERALES ---
    "Facebook": "https://www.facebook.com/{username}",
    "Instagram": "https://www.instagram.com/{username}",
    "Twitter": "https://twitter.com/{username}",
    "X": "https://x.com/{username}",
    "TikTok": "https://www.tiktok.com/@{username}",
    "Snapchat": "https://www.snapchat.com/add/{username}",
    "Pinterest": "https://www.pinterest.com/{username}",
    "LinkedIn": "https://www.linkedin.com/in/{username}",
    "Threads": "https://www.threads.net/@{username}",
    "Bluesky": "https://bsky.app/profile/{username}",
    "Mastodon": "https://mastodon.social/@{username}",
    "Tumblr": "https://{username}.tumblr.com",
    "Flickr": "https://www.flickr.com/people/{username}",
    "Patreon": "https://www.patreon.com/{username}",
    "OnlyFans": "https://onlyfans.com/{username}",
    "Discord": "https://discord.com/users/{username}",
    "Telegram": "https://t.me/{username}",
    "WhatsApp": "https://wa.me/{username}",
    "Signal": "https://signal.me/#p/{username}",
    "Viber": "https://chats.viber.com/{username}",
    "Line": "https://line.me/ti/p/{username}",
    "WeChat": "https://weixin.qq.com/{username}",
    "KakaoTalk": "https://story.kakao.com/{username}",
    "QQ": "https://user.qzone.qq.com/{username}",
    "VK": "https://vk.com/{username}",
    "Odnoklassniki": "https://ok.ru/{username}",
    "Myspace": "https://myspace.com/{username}",
    "Badoo": "https://badoo.com/en/{username}",
    "Bumble": "https://bumble.com/{username}",
    "Tinder": "https://tinder.com/@{username}",
    "Grindr": "https://grindr.com/profile/{username}",
    "FetLife": "https://fetlife.com/users/{username}",
    "Meetup": "https://www.meetup.com/members/{username}",
    "Nextdoor": "https://nextdoor.com/profile/{username}",
    "Parler": "https://parler.com/profile/{username}",
    "Gab": "https://gab.com/{username}",
    "TruthSocial": "https://truthsocial.com/@{username}",
    "Gettr": "https://gettr.com/user/{username}",
    "Rumble": "https://rumble.com/user/{username}",
    "Odysee": "https://odysee.com/@{username}",
    "DLive": "https://dlive.tv/{username}",
    "Periscope": "https://periscope.tv/{username}",
    "YouNow": "https://www.younow.com/{username}",
    "BigoLive": "https://bigo.tv/{username}",
    "Likee": "https://likee.com/@{username}",
    "Triller": "https://triller.co/@{username}",
    "Dubsmash": "https://dubsmash.com/{username}",
    "Funimate": "https://funimate.com/user/{username}",
    "Lomotif": "https://lomotif.com/@{username}",
    "Zynn": "https://zynn.tv/@{username}",
    "Chingari": "https://chingari.io/user/{username}",
    "Moj": "https://mojapp.com/@{username}",
    "Josh": "https://joshapp.com/@{username}",
    "ShareChat": "https://sharechat.com/profile/{username}",
    "Dailyhunt": "https://dailyhunt.in/profile/{username}",
    "SinaWeibo": "https://weibo.com/u/{username}",
    "TencentWeibo": "https://t.qq.com/{username}",
    "Douyin": "https://douyin.com/user/{username}",
    "Kuaishou": "https://kuaishou.com/profile/{username}",
    "Xiaohongshu": "https://xiaohongshu.com/user/profile/{username}",
    "Zhihu": "https://zhihu.com/people/{username}",
    "Bilibili": "https://space.bilibili.com/{username}",
    "AcFun": "https://acfun.cn/u/{username}",
    "Tieba": "https://tieba.baidu.com/home/main?un={username}",
    "Douban": "https://douban.com/people/{username}",
    
    # --- BLOGS Y PUBLICACIONES ---
    "Medium": "https://medium.com/@{username}",
    "Substack": "https://substack.com/@{username}",
    "WordPress": "https://{username}.wordpress.com",
    "Blogger": "https://{username}.blogspot.com",
    "Ghost": "https://{username}.ghost.io",
    "Hashnode": "https://hashnode.com/@{username}",
    "DevTo": "https://dev.to/{username}",
    "Hackernoon": "https://hackernoon.com/@{username}",
    "Telegraph": "https://telegra.ph/{username}",
    "Notion": "https://notion.so/{username}",
    "ObsidianPublish": "https://publish.obsidian.md/{username}",
    "BearBlog": "https://bearblog.dev/{username}",
    "WriteAs": "https://write.as/{username}",
    "Posthaven": "https://posthaven.com/{username}",
    "Svbtle": "https://svbtle.com/{username}",
    "TumblrBlog": "https://{username}.tumblr.com",
    "Typepad": "https://{username}.typepad.com",
    "LiveJournal": "https://{username}.livejournal.com",
    "Dreamwidth": "https://{username}.dreamwidth.org",
    "Xanga": "https://xanga.com/{username}",
    "PenZU": "https://penzu.com/public/{username}",
    "Wattpad": "https://wattpad.com/user/{username}",
    "Quotev": "https://quotev.com/{username}",
    "Booksie": "https://booksie.com/users/{username}",
    "Scribd": "https://scribd.com/{username}",
    "Issuu": "https://issuu.com/{username}",
    "Calameo": "https://calameo.com/accounts/{username}",
    "Flipboard": "https://flipboard.com/@{username}",
    "ScoopIt": "https://scoop.it/u/{username}",
    "PaperLi": "https://paper.li/@{username}",
    "Storify": "https://storify.com/{username}",
    "RebelMouse": "https://rebelmouse.com/{username}",
    
    # --- CÓDIGO Y DESARROLLO ---
    "GitHub": "https://github.com/{username}",
    "GitLab": "https://gitlab.com/{username}",
    "Bitbucket": "https://bitbucket.org/{username}",
    "Codeberg": "https://codeberg.org/{username}",
    "Gitea": "https://gitea.com/{username}",
    "Gitee": "https://gitee.com/{username}",
    "GitFlic": "https://gitflic.ru/user/{username}",
    "SourceForge": "https://sourceforge.net/u/{username}",
    "Launchpad": "https://launchpad.net/~{username}",
    "Savannah": "https://savannah.gnu.org/users/{username}",
    "CodePen": "https://codepen.io/{username}",
    "JSFiddle": "https://jsfiddle.net/{username}",
    "CodeSandbox": "https://codesandbox.io/u/{username}",
    "Replit": "https://replit.com/@{username}",
    "Glitch": "https://glitch.com/@{username}",
    "StackBlitz": "https://stackblitz.com/@{username}",
    "Gitpod": "https://gitpod.io/@{username}",
    "Vercel": "https://vercel.com/{username}",
    "Netlify": "https://netlify.com/{username}",
    "Render": "https://render.com/u/{username}",
    "Heroku": "https://heroku.com/{username}",
    "PythonAnywhere": "https://pythonanywhere.com/user/{username}",
    "Railway": "https://railway.app/u/{username}",
    "Koyeb": "https://koyeb.com/users/{username}",
    "FlyIo": "https://fly.io/user/{username}",
    "DenoDeploy": "https://deno.dev/@{username}",
    "CloudflarePages": "https://{username}.pages.dev",
    "GitHubPages": "https://{username}.github.io",
    "GitLabPages": "https://{username}.gitlab.io",
    
    # --- FOROS Y COMUNIDADES ---
    "Reddit": "https://www.reddit.com/user/{username}",
    "StackOverflow": "https://stackoverflow.com/users/{username}",
    "StackExchange": "https://stackexchange.com/users/{username}",
    "Quora": "https://quora.com/profile/{username}",
    "AskFM": "https://ask.fm/{username}",
    "CuriousCat": "https://curiouscat.me/{username}",
    "SaidIt": "https://saidit.net/u/{username}",
    "Voat": "https://voat.co/user/{username}",
    "Hubski": "https://hubski.com/user/{username}",
    "HackerNews": "https://news.ycombinator.com/user?id={username}",
    "Lobsters": "https://lobste.rs/u/{username}",
    "Tildes": "https://tildes.net/user/{username}",
    "Lemmy": "https://lemmy.ml/u/{username}",
    "Kbin": "https://kbin.social/u/{username}",
    "Raddle": "https://raddle.me/u/{username}",
    "Snapzu": "https://snapzu.com/u/{username}",
    "SoylentNews": "https://soylentnews.org/u/{username}",
    "Metafilter": "https://metafilter.com/user/{username}",
    "SomethingAwful": "https://forums.somethingawful.com/member.php?userid={username}",
    "Fark": "https://fark.com/users/{username}",
    "Digg": "https://digg.com/u/{username}",
    "Mix": "https://mix.com/{username}",
    "Pearltrees": "https://pearltrees.com/{username}",
    "Diigo": "https://diigo.com/user/{username}",
    "Pinboard": "https://pinboard.in/u:{username}",
    "Bookmarks": "https://bookmarks.com/{username}",
    "Raindrop": "https://raindrop.io/{username}",
    "Minds": "https://minds.com/{username}",
    "MeWe": "https://mewe.com/i/{username}",
    "RumbleTalk": "https://rumbletalk.com/{username}",
    
    # --- PORTAFOLIOS Y CREATIVOS ---
    "Behance": "https://behance.net/{username}",
    "Dribbble": "https://dribbble.com/{username}",
    "ArtStation": "https://artstation.com/{username}",
    "DeviantArt": "https://deviantart.com/{username}",
    "CGSociety": "https://cgsociety.org/user/{username}",
    "Pixiv": "https://pixiv.net/users/{username}",
    "Newgrounds": "https://newgrounds.com/user/{username}",
    "Sketchfab": "https://sketchfab.com/{username}",
    "Turbosquid": "https://turbosquid.com/Search/Artists/{username}",
    "Cults3D": "https://cults3d.com/es/usuarios/{username}",
    "Thingiverse": "https://thingiverse.com/{username}",
    "Printables": "https://printables.com/@{username}",
    "MyMiniFactory": "https://myminifactory.com/users/{username}",
    "Shapeways": "https://shapeways.com/designer/{username}",
    "Etsy": "https://etsy.com/shop/{username}",
    "Society6": "https://society6.com/{username}",
    "Redbubble": "https://redbubble.com/people/{username}",
    "Threadless": "https://threadless.com/artist/{username}",
    "Zazzle": "https://zazzle.com/member/{username}",
    "Cafepress": "https://cafepress.com/profile/{username}",
    "CreativeMarket": "https://creativemarket.com/{username}",
    "EnvatoMarket": "https://envato.com/user/{username}",
    "ThemeForest": "https://themeforest.net/user/{username}",
    "CodeCanyon": "https://codecanyon.net/user/{username}",
    "GraphicRiver": "https://graphicriver.net/user/{username}",
    "AudioJungle": "https://audiojungle.net/user/{username}",
    "VideoHive": "https://videohive.net/user/{username}",
    "PhotoDune": "https://photodune.net/user/{username}",
    "3DOcean": "https://3docean.net/user/{username}",
    
    # --- MÚSICA Y AUDIO ---
    "SoundCloud": "https://soundcloud.com/{username}",
    "Spotify": "https://open.spotify.com/user/{username}",
    "AppleMusic": "https://music.apple.com/profile/{username}",
    "Deezer": "https://deezer.com/profile/{username}",
    "Tidal": "https://tidal.com/user/{username}",
    "AmazonMusic": "https://music.amazon.com/profile/{username}",
    "Bandcamp": "https://bandcamp.com/{username}",
    "Mixcloud": "https://mixcloud.com/{username}",
    "Audiomack": "https://audiomack.com/{username}",
    "HearThisAt": "https://hearthis.at/{username}",
    "ReverbNation": "https://reverbnation.com/{username}",
    "Splice": "https://splice.com/{username}",
    "BeatStars": "https://beatstars.com/{username}",
    "Airbit": "https://airbit.com/{username}",
    "SoundBetter": "https://soundbetter.com/profiles/{username}",
    "SpotifyArtists": "https://artists.spotify.com/@{username}",
    "DistroKid": "https://distrokid.com/@{username}",
    "TuneCore": "https://tunecore.com/profile/{username}",
    "CDBaby": "https://cdbaby.com/member/{username}",
    "Amuse": "https://amuse.io/@{username}",
    "RecordUnion": "https://recordunion.com/{username}",
    "NPRMusic": "https://npr.org/artists/{username}",
    "Pandora": "https://pandora.com/artist/{username}",
    "iHeartRadio": "https://iheart.com/artist/{username}",
    "LastFM": "https://last.fm/user/{username}",
    "Genius": "https://genius.com/{username}",
    "Musixmatch": "https://musixmatch.com/artist/{username}",
    "Shazam": "https://shazam.com/artist/{username}",
    "WhoSampled": "https://whosampled.com/{username}",
    "RateYourMusic": "https://rateyourmusic.com/artist/{username}",
    
    # --- VIDEO Y STREAMING ---
    "YouTube": "https://youtube.com/@{username}",
    "YouTubeChannel": "https://youtube.com/channel/{username}",
    "Twitch": "https://twitch.tv/{username}",
    "Kick": "https://kick.com/{username}",
    "Trovo": "https://trovo.live/{username}",
    "FacebookGaming": "https://facebook.com/gaming/{username}",
    "Dlive": "https://dlive.tv/{username}",
    "Caffeine": "https://caffeine.tv/{username}",
    "Vimeo": "https://vimeo.com/{username}",
    "Dailymotion": "https://dailymotion.com/{username}",
    "Vevo": "https://vevo.com/artist/{username}",
    "Bitchute": "https://bitchute.com/channel/{username}",
    "OdyseeChannel": "https://odysee.com/@{username}",
    "RumbleVideo": "https://rumble.com/c/{username}",
    "PeerTube": "https://peertube.tv/@{username}",
    "Floatplane": "https://floatplane.com/channel/{username}",
    "Nebula": "https://nebula.tv/{username}",
    "CuriosityStream": "https://curiositystream.com/profile/{username}",
    "TwitCasting": "https://twitcasting.tv/{username}",
    "Showroom": "https://showroom-live.com/{username}",
    "Pococha": "https://pococha.com/{username}",
    "17Live": "https://17live.com/profile/{username}",
    "Uplive": "https://uplive.com/{username}",
    "MogoLive": "https://mogo.live/{username}",
    "VKVideo": "https://vk.com/video/@{username}",
    "Rutube": "https://rutube.ru/user/{username}",
    "VKPlay": "https://vkplay.live/{username}",
    "GoodGame": "https://goodgame.ru/{username}",
    "WASD": "https://wasd.tv/{username}",
    
    # --- FOTOGRAFÍA Y ARTE ---
    "InstagramPhoto": "https://instagram.com/{username}",
    "500px": "https://500px.com/{username}",
    "FlickrPhotos": "https://flickr.com/photos/{username}",
    "SmugMug": "https://{username}.smugmug.com",
    "PhotoBlog": "https://{username}.photoblog.com",
    "ViewBug": "https://viewbug.com/member/{username}",
    "GuruShots": "https://gurushots.com/{username}",
    "YouPic": "https://youpic.com/photographer/{username}",
    "Picfair": "https://picfair.com/users/{username}",
    "EyeEm": "https://eyeem.com/u/{username}",
    "Unsplash": "https://unsplash.com/@{username}",
    "Pexels": "https://pexels.com/@{username}",
    "Pixabay": "https://pixabay.com/users/{username}",
    "Stocksy": "https://stocksy.com/artist/{username}",
    "Shutterstock": "https://shutterstock.com/g/{username}",
    "AdobeStock": "https://stock.adobe.com/contributor/{username}",
    "GettyImages": "https://gettyimages.com/contributor/{username}",
    "Alamy": "https://alamy.com/contributor/{username}",
    "Dreamstime": "https://dreamstime.com/{username}",
    "Depositphotos": "https://depositphotos.com/portfolio/{username}",
    "Canva": "https://canva.com/@{username}",
    "Figma": "https://figma.com/@{username}",
    "Sketch": "https://sketch.com/u/{username}",
    "AdobeBehance": "https://behance.net/{username}",
    
    # --- JUEGOS ---
    "Steam": "https://steamcommunity.com/id/{username}",
    "SteamProfile": "https://steamcommunity.com/profiles/{username}",
    "EpicGames": "https://epicgames.com/id/{username}",
    "XboxLive": "https://xbox.com/play/profile/{username}",
    "PlayStation": "https://playstation.com/profile/{username}",
    "Nintendo": "https://nintendo.com/users/{username}",
    "BattleNet": "https://battle.net/{username}",
    "RiotGames": "https://riotgames.com/{username}",
    "EA": "https://ea.com/profile/{username}",
    "Ubisoft": "https://ubisoft.com/profile/{username}",
    "Rockstar": "https://socialclub.rockstargames.com/member/{username}",
    "TwitchGamer": "https://twitch.tv/{username}",
    "DiscordGame": "https://discord.com/users/{username}",
    "GOG": "https://gog.com/u/{username}",
    "HumbleBundle": "https://humblebundle.com/user/{username}",
    "Fanatical": "https://fanatical.com/profile/{username}",
    "GreenManGaming": "https://greenmangaming.com/user/{username}",
    "Kongregate": "https://kongregate.com/accounts/{username}",
    "NewgroundsGame": "https://newgrounds.com/user/{username}",
    "ItchIo": "https://itch.io/profile/{username}",
    "GameJolt": "https://gamejolt.com/@{username}",
    "IndieDB": "https://indiedb.com/members/{username}",
    "ModDB": "https://moddb.com/members/{username}",
    "NexusMods": "https://nexusmods.com/users/{username}",
    "CurseForge": "https://curseforge.com/members/{username}",
    "PlanetMinecraft": "https://planetminecraft.com/member/{username}",
    "MinecraftName": "https://namemc.com/profile/{username}",
    "Hypixel": "https://hypixel.net/members/{username}",
    "Roblox": "https://roblox.com/user.aspx?username={username}",
    "RobloxProfile": "https://roblox.com/users/{username}/profile",
    "FortniteTracker": "https://fortnitetracker.com/profile/all/{username}",
    "ApexTracker": "https://apextracker.gg/profile/{username}",
    "CODWarzone": "https://cod.tracker.gg/warzone/profile/{username}",
    "ValorantTracker": "https://valorant.tracker.gg/profile/{username}",
    "CSGOStats": "https://csgostats.gg/player/{username}",
    "Dotabuff": "https://dotabuff.com/players/{username}",
    "OpGG": "https://op.gg/summoners/all/{username}",
    "LeagueOfGraphs": "https://leagueofgraphs.com/summoner/all/{username}",
    "GenshinImpact": "https://genshin.mihoyo.com/profile/{username}",
    
    # --- PROFESIONAL Y NEGOCIOS ---
    "LinkedInBusiness": "https://linkedin.com/company/{username}",
    "Indeed": "https://indeed.com/profile/{username}",
    "Glassdoor": "https://glassdoor.com/profile/{username}",
    "AngelList": "https://angel.co/u/{username}",
    "Wellfound": "https://wellfound.com/u/{username}",
    "XING": "https://xing.com/profile/{username}",
    "Viadeo": "https://viadeo.com/p/{username}",
    "AboutMe": "https://about.me/{username}",
    "Keybase": "https://keybase.io/{username}",
    "MastodonWork": "https://mastodon.work/@{username}",
    "Crunchbase": "https://crunchbase.com/person/{username}",
    "Bloomberg": "https://bloomberg.com/profile/person/{username}",
    "Forbes": "https://forbes.com/profile/{username}",
    "Inc": "https://inc.com/profile/{username}",
    "Entrepreneur": "https://entrepreneur.com/profile/{username}",
    "BusinessInsider": "https://businessinsider.com/profile/{username}",
    "TechCrunch": "https://techcrunch.com/author/{username}",
    "VentureBeat": "https://venturebeat.com/author/{username}",
    "TheVerge": "https://theverge.com/author/{username}",
    "Wired": "https://wired.com/author/{username}",
    "FastCompany": "https://fastcompany.com/user/{username}",
    "HarvardBusiness": "https://hbr.org/user/{username}",
    "MITTechnology": "https://technologyreview.com/profile/{username}",
    "IEEE": "https://ieee.org/profile/{username}",
    "ACM": "https://acm.org/profile/{username}",
    "ResearchGate": "https://researchgate.net/profile/{username}",
    "GoogleScholar": "https://scholar.google.com/citations?user={username}",
    "ORCID": "https://orcid.org/{username}",
    "Academia": "https://academia.edu/{username}",
    "SSRN": "https://ssrn.com/author={username}",
    
    # --- CIENCIA Y EDUCACIÓN ---
    "GitHubScience": "https://github.com/{username}",
    "Kaggle": "https://kaggle.com/{username}",
    "Coursera": "https://coursera.org/user/{username}",
    "edX": "https://edx.org/u/{username}",
    "Udemy": "https://udemy.com/user/{username}",
    "Skillshare": "https://skillshare.com/user/{username}",
    "Pluralsight": "https://pluralsight.com/profile/{username}",
    "LinkedInLearning": "https://linkedin.com/learning/instructor/{username}",
    "Kattis": "https://datacamp.com/profile/{username}",
    "Codecademy": "https://codecademy.com/profiles/{username}",
    "FreeCodeCamp": "https://freecodecamp.org/{username}",
    "TheOdinProject": "https://theodinproject.com/users/{username}",
    "Exercism": "https://exercism.org/profiles/{username}",
    "Codewars": "https://codewars.com/users/{username}",
    "LeetCode": "https://leetcode.com/{username}",
    "HackerRank": "https://hackerrank.com/{username}",
    "Codeforces": "https://codeforces.com/profile/{username}",
    "Topcoder": "https://topcoder.com/members/{username}",
    "AtCoder": "https://atcoder.jp/users/{username}",
    "Kattis": "https://open.kattis.com/users/{username}",
    "SPOJ": "https://spoj.com/users/{username}",
    "CodeChef": "https://codechef.com/users/{username}",
    "GeeksForGeeks": "https://geeksforgeeks.org/user/{username}",
    "CodingNinjas": "https://codingninjas.com/profile/{username}",
    "InterviewBit": "https://interviewbit.com/profile/{username}",
    "AlgoExpert": "https://algoexpert.io/{username}",
    "NeetCode": "https://neetcode.io/{username}",
    "Brilliant": "https://brilliant.org/profile/{username}",
    
    # --- CROWDFUNDING Y FINANZAS ---
    "Kickstarter": "https://kickstarter.com/profile/{username}",
    "Indiegogo": "https://indiegogo.com/individuals/{username}",
    "GoFundMe": "https://gofundme.com/campaign/{username}",
    "PatreonCreator": "https://patreon.com/{username}",
    "BuyMeACoffee": "https://buymeacoffee.com/{username}",
    "KoFi": "https://ko-fi.com/{username}",
    "Liberapay": "https://liberapay.com/{username}",
    "OpenCollective": "https://opencollective.com/{username}",
    "GitHubSponsors": "https://github.com/sponsors/{username}",
    "PayPalMe": "https://paypal.me/{username}",
    "Venmo": "https://venmo.com/{username}",
    "CashApp": "https://cash.app/${username}",
    "Stripe": "https://stripe.com/@{username}",
    "Coinbase": "https://coinbase.com/{username}",
    "Binance": "https://binance.com/en/profile/{username}",
    "Kraken": "https://kraken.com/profiles/{username}",
    "CryptoWallet": "https://etherscan.io/address/{username}",
    "Opensea": "https://opensea.io/{username}",
    "Rarible": "https://rarible.com/user/{username}",
    "Foundation": "https://foundation.app/@{username}",
    "SuperRare": "https://superrare.com/{username}",
    "KnownOrigin": "https://knownorigin.io/artist/{username}",
    "AsyncArt": "https://async.art/artist/{username}",
    "NFTShowroom": "https://nftshowroom.com/{username}",
    
    # --- BOTONES ADICIONALES DE PLATAFORMAS ---
    "Pleroma": "https://pleroma.site/@{username}",
    "Friendica": "https://friendica.com/profile/{username}",
    "Diaspora": "https://diasporafoundation.org/u/{username}",
    "Hubzilla": "https://hubzilla.org/channel/{username}",
    "Streams": "https://streams.social/@{username}",
    "GNU social": "https://gnusocial.net/{username}",
    "Misskey": "https://misskey.io/@{username}",
    "Akkoma": "https://akkoma.social/@{username}",
    "Firefish": "https://firefish.social/@{username}",
    "Kitsu": "https://kitsu.io/users/{username}",
    "Anilist": "https://anilist.co/user/{username}",
    "MyAnimeList": "https://myanimelist.net/profile/{username}",
    "Goodreads": "https://goodreads.com/user/show/{username}",
    "LibraryThing": "https://librarything.com/profile/{username}",
    "Letterboxd": "https://letterboxd.com/{username}",
    "FilmAffinity": "https://filmaffinity.com/es/user/{username}",
    "IMDb": "https://imdb.com/user/{username}",
    "RottenTomatoes": "https://rottentomatoes.com/user/id/{username}",
    "TVTime": "https://tvtime.com/{username}",
    "Trakt": "https://trakt.tv/users/{username}",
    "Serializd": "https://serializd.com/user/{username}",
    "Cinephile": "https://cinephile.net/{username}",
    "Mubi": "https://mubi.com/users/{username}",
    "Criticker": "https://criticker.com/profile/{username}",
    "RateBeer": "https://ratebeer.com/user/{username}",
    "Untappd": "https://untappd.com/user/{username}",
    "Vivino": "https://vivino.com/users/{username}",
    "Decanter": "https://decanter.com/profile/{username}",
    "WhiskyBase": "https://whiskybase.com/profile/{username}",
    "CigarAficionado": "https://cigaraficionado.com/profile/{username}",
    
    # --- DEPORTES Y FITNESS ---
    "Strava": "https://strava.com/athletes/{username}",
    "TrainingPeaks": "https://trainingpeaks.com/profile/{username}",
    "MyFitnessPal": "https://myfitnesspal.com/profile/{username}",
    "Fitbit": "https://fitbit.com/user/{username}",
    "Garmin": "https://connect.garmin.com/profile/{username}",
    "Polar": "https://polar.com/profile/{username}",
    "Suunto": "https://suunto.com/profile/{username}",
    "Zwift": "https://zwift.com/athlete/{username}",
    "Peloton": "https://peloton.com/member/{username}",
    "NikeRunClub": "https://nike.com/run/profile/{username}",
    "Runkeeper": "https://runkeeper.com/user/{username}",
    "Runtastic": "https://runtastic.com/user/{username}",
    "Endomondo": "https://endomondo.com/user/{username}",
    "MapMyRun": "https://mapmyrun.com/profile/{username}",
    "AllTrails": "https://alltrails.com/members/{username}",
    "Komoot": "https://komoot.com/user/{username}",
    "Trailforks": "https://trailforks.com/profile/{username}",
    "MTBProject": "https://mtbproject.com/profile/{username}",
    "Surfline": "https://surfline.com/surfers/{username}",
    "MagicSeaweed": "https://magicseaweed.com/profile/{username}",
    "Windsurf": "https://windsurf.com/profile/{username}",
    "Snowbird": "https://snowbird.com/profile/{username}",
    "OpenSnow": "https://opensnow.com/profile/{username}",
    
    # --- VIAJES ---
    "TripAdvisor": "https://tripadvisor.com/members/{username}",
    "Booking": "https://booking.com/profile/{username}",
    "Airbnb": "https://airbnb.com/users/show/{username}",
    "Couchsurfing": "https://couchsurfing.com/people/{username}",
    "Hostelworld": "https://hostelworld.com/profile/{username}",
    "LonelyPlanet": "https://lonelyplanet.com/profile/{username}",
    "NomadList": "https://nomadlist.com/@{username}",
    "Workaway": "https://workaway.info/profile/{username}",
    "HelpX": "https://helpx.net/profile/{username}",
    "WWOOF": "https://wwoof.net/profile/{username}",
    "TrustedHousesitters": "https://trustedhousesitters.com/profile/{username}",
    "HouseSitMatch": "https://housesitmatch.com/profile/{username}",
    "MindMyHouse": "https://mindmyhouse.com/profile/{username}",
    "Roam": "https://roam.com/@{username}",
    "BeWelcome": "https://bewelcome.org/profile/{username}",
}
   


async def check_public_profile(
    session: aiohttp.ClientSession,
    source: str,
    url: str,
) -> Optional[str]:
    try:
        async with session.get(
            url,
            allow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 UniversalSearchBot/1.0 "
                    "(public OSINT research)"
                )
            },
        ) as response:
            if response.status == 200:
                escaped_source = safe_text(source)
                escaped_url = html.escape(url, quote=True)

                return (
                    f'• <b>{escaped_source}</b>: '
                    f'<a href="{escaped_url}">perfil público encontrado</a>'
                )

    except asyncio.TimeoutError:
        logger.info("Timeout comprobando %s", source)

    except aiohttp.ClientError as error:
        logger.info("Error comprobando %s: %s", source, error)

    return None


async def search_username(
    username: str,
    session: aiohttp.ClientSession,
) -> list[str]:
    checks = [
        check_public_profile(
            session,
            source,
            template.format(username=username),
        )
        for source, template in USERNAME_SOURCES.items()
    ]

    results = await asyncio.gather(*checks)

    return [result for result in results if result is not None]


# ============================================================
# BÚSQUEDA DE DOMINIOS
# ============================================================

async def search_domain(
    domain: str,
    session: aiohttp.ClientSession,
) -> list[str]:
    results: list[str] = []

    dns_url = (
        "https://cloudflare-dns.com/dns-query"
        f"?name={domain}&type=A"
    )

    dns_data = await fetch_json(
        session,
        dns_url,
        headers={
            "Accept": "application/dns-json",
            "User-Agent": "UniversalSearchBot/1.0",
        },
    )

    if dns_data:
        addresses = sorted(
            {
                answer.get("data", "")
                for answer in dns_data.get("Answer", [])
                if answer.get("type") == 1 and answer.get("data")
            }
        )

        for address in addresses[:10]:
            results.append(
                f"• <b>DNS A</b>: <code>{safe_text(address)}</code>"
            )

    rdap_data = await fetch_json(
        session,
        f"https://rdap.org/domain/{domain}",
        headers={"User-Agent": "UniversalSearchBot/1.0"},
    )

    if rdap_data:
        domain_name = rdap_data.get("ldhName", domain)
        statuses = ", ".join(rdap_data.get("status", [])) or "sin datos"
        event_dates: dict[str, str] = {}

        for event in rdap_data.get("events", []):
            action = event.get("eventAction")
            date = event.get("eventDate")

            if action and date:
                event_dates[action] = date[:10]

        registration = event_dates.get("registration", "desconocido")
        expiration = event_dates.get("expiration", "desconocida")

        results.append(
            "• <b>RDAP</b>: "
            + safe_text(domain_name)
            + "\n  Estado: "
            + safe_text(statuses)
            + "\n  Registro: "
            + safe_text(registration)
            + "\n  Expiración: "
            + safe_text(expiration)
        )

    return results
# ============================================================
# BÚSQUEDA DE DIRECCIONES IP
# ============================================================

async def search_ip(
    raw_address: str,
    session: aiohttp.ClientSession,
) -> list[str]:
    address = ipaddress.ip_address(raw_address)

    if not address.is_global:
        return [
            "• <b>Dirección bloqueada</b>: "
            "no es una IP pública global."
        ]

    results: list[str] = []
    event_loop = asyncio.get_running_loop()

    try:
        hostname, aliases, _ = await event_loop.run_in_executor(
            None,
            socket.gethostbyaddr,
            raw_address,
        )

        results.append(
            "• <b>DNS inverso</b>: <code>"
            + safe_text(hostname)
            + "</code>"
        )

        for alias in aliases[:5]:
            results.append(
                "• <b>Alias DNS</b>: <code>"
                + safe_text(alias)
                + "</code>"
            )

    except (socket.herror, socket.gaierror, OSError):
        logger.info("Sin DNS inverso para %s", raw_address)

    rdap_data = await fetch_json(
        session,
        "https://rdap.org/ip/" + raw_address,
        headers={"User-Agent": "UniversalSearchBot/1.0"},
    )

    if rdap_data:
        network_name = rdap_data.get("name", "sin nombre")
        country = rdap_data.get("country", "desconocido")
        start_address = rdap_data.get("startAddress", "?")
        end_address = rdap_data.get("endAddress", "?")
        network_type = rdap_data.get("type", "desconocido")

        results.append(
            "• <b>Asignación RDAP</b>"
            + "\n  Red: "
            + safe_text(network_name)
            + "\n  País: "
            + safe_text(country)
            + "\n  Tipo: "
            + safe_text(network_type)
            + "\n  Rango: <code>"
            + safe_text(start_address)
            + " - "
            + safe_text(end_address)
            + "</code>"
        )

    return results

# ============================================================
# EJECUCIÓN DE BÚSQUEDAS
# ============================================================

async def execute_search(
    message: Message,
    forced_type: Optional[str] = None,
) -> None:
    if not message.from_user:
        return

    if not check_rate_limit(message.from_user.id):
        await message.answer(
            "Has alcanzado el límite temporal. "
            "Espera un minuto antes de volver a buscar."
        )
        return

    status_message: Optional[Message] = None

    try:
        raw_target = get_command_argument(message)

        if forced_type:
            target_type = forced_type
            target = validate_forced_target(forced_type, raw_target)
        else:
            target_type, target = classify_target(raw_target)

        status_message = await message.answer(
            "Buscando en fuentes públicas…"
        )

        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)

        async with aiohttp.ClientSession(
            timeout=timeout,
            headers={"User-Agent": "UniversalSearchBot/1.0"},
        ) as session:
            if target_type == "username":
                results = await search_username(target, session)

            elif target_type == "domain":
                results = await search_domain(target, session)

            elif target_type == "ip":
                results = await search_ip(target, session)

            else:
                raise ValueError("Tipo de búsqueda no compatible.")

        response_lines = [
            f"<b>Universal Search: {safe_text(target_type)}</b>",
            f"<code>{safe_text(target)}</code>",
            "",
        ]

        if results:
            response_lines.extend(results)
        else:
            response_lines.append(
                "No se encontraron hallazgos públicos verificables."
            )

        response_lines.extend(
            [
                "",
                "<i>Un match no confirma identidad. "
                "Verifica los hallazgos manualmente.</i>",
            ]
        )

        response_text = "\n".join(response_lines)

        if len(response_text) > 4096:
            response_text = response_text[:3900]
            response_text += "\n\n<i>Resultado recortado por Telegram.</i>"

        await status_message.edit_text(
            response_text,
            disable_web_page_preview=True,
        )

    except ValueError as error:
        error_text = safe_text(error)

        if status_message:
            await status_message.edit_text(error_text)
        else:
            await message.answer(error_text)

    except asyncio.TimeoutError:
        logger.exception("Timeout general durante una búsqueda")

        error_text = (
            "La búsqueda tardó demasiado. "
            "Prueba nuevamente dentro de unos segundos."
        )

        if status_message:
            await status_message.edit_text(error_text)
        else:
            await message.answer(error_text)

    except Exception:
        logger.exception("Error inesperado durante una búsqueda")

        error_text = (
            "Ocurrió un error inesperado. "
            "Revisa los logs del contenedor."
        )

        if status_message:
            await status_message.edit_text(error_text)
        else:
            await message.answer(error_text)


# ============================================================
# COMANDOS DE TELEGRAM
# ============================================================

@router.message(Command("start", "help"))
async def start_command(message: Message) -> None:
    await message.answer(
        "<b>Universal Search OSINT</b>\n\n"
        "Consulta información disponible públicamente:\n\n"
        "<code>/search objetivo</code>\n"
        "<code>/username nombre</code>\n"
        "<code>/domain example.com</code>\n"
        "<code>/ip 1.1.1.1</code>\n"
        "<code>/privacy</code>\n\n"
        "Ejemplos:\n"
        "<code>/search torvalds</code>\n"
        "<code>/domain example.com</code>\n"
        "<code>/ip 1.1.1.1</code>\n\n"
        "Úsalo únicamente con fines legítimos y autorizados."
    )


@router.message(Command("search"))
async def search_command(message: Message) -> None:
    await execute_search(message)


@router.message(Command("username"))
async def username_command(message: Message) -> None:
    await execute_search(message, forced_type="username")


@router.message(Command("domain"))
async def domain_command(message: Message) -> None:
    await execute_search(message, forced_type="domain")


@router.message(Command("ip"))
async def ip_command(message: Message) -> None:
    await execute_search(message, forced_type="ip")


@router.message(Command("privacy"))
async def privacy_command(message: Message) -> None:
    await message.answer(
        "<b>Privacidad</b>\n\n"
        "El bot no guarda objetivos ni resultados en una base de datos.\n"
        "Solo mantiene temporalmente en memoria el número de consultas "
        "necesario para aplicar el límite de uso."
    )


# ============================================================
# WEBHOOK PARA RENDER
# ============================================================

from aiohttp import web
from aiogram.webhook.aiohttp_server import (
    SimpleRequestHandler,
    setup_application,
)

PORT = int(os.getenv("PORT", "10000"))
WEBHOOK_PATH = "/telegram/webhook"
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]

WEBHOOK_BASE_URL = (
    os.getenv("WEBHOOK_BASE_URL")
    or os.getenv("RENDER_EXTERNAL_URL")
)

if not WEBHOOK_BASE_URL:
    raise RuntimeError(
        "Configura WEBHOOK_BASE_URL con la URL pública de Render."
    )

WEBHOOK_BASE_URL = WEBHOOK_BASE_URL.rstrip("/")
WEBHOOK_URL = f"{WEBHOOK_BASE_URL}{WEBHOOK_PATH}"


async def health_handler(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "status": "ok",
            "service": "universal-search-bot",
            "webhook": WEBHOOK_PATH,
        }
    )


async def root_handler(request: web.Request) -> web.Response:
    return web.Response(
        text="Universal Search OSINT Bot funcionando.",
        content_type="text/plain",
    )


async def on_startup(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(
                command="search",
                description="Detectar y buscar un objetivo",
            ),
            BotCommand(
                command="username",
                description="Buscar un username público",
            ),
            BotCommand(
                command="domain",
                description="Consultar un dominio",
            ),
            BotCommand(
                command="ip",
                description="Consultar una IP pública",
            ),
            BotCommand(
                command="privacy",
                description="Información de privacidad",
            ),
            BotCommand(
                command="help",
                description="Mostrar ayuda",
            ),
        ]
    )

    await bot.set_webhook(
        url=WEBHOOK_URL,
        secret_token=WEBHOOK_SECRET,
        allowed_updates=dispatcher.resolve_used_update_types(),
        drop_pending_updates=False,
    )

    webhook_info = await bot.get_webhook_info()

    logger.info("Webhook configurado: %s", webhook_info.url)
    logger.info("Servidor escuchando en 0.0.0.0:%s", PORT)


async def on_shutdown(bot: Bot) -> None:
    logger.info("Cerrando sesión del bot")
    await bot.session.close()


bot = Bot(
    token=TELEGRAM_BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML,
    ),
)

dispatcher = Dispatcher()
dispatcher.include_router(router)
dispatcher.startup.register(on_startup)
dispatcher.shutdown.register(on_shutdown)


def create_application() -> web.Application:
    application = web.Application()

    application.router.add_get("/", root_handler)
    application.router.add_get("/health", health_handler)

    webhook_handler = SimpleRequestHandler(
        dispatcher=dispatcher,
        bot=bot,
        secret_token=WEBHOOK_SECRET,
    )

    webhook_handler.register(
        application,
        path=WEBHOOK_PATH,
    )

    setup_application(
        application,
        dispatcher,
        bot=bot,
    )

    return application


if __name__ == "__main__":
    web.run_app(
        create_application(),
        host="0.0.0.0",
        port=PORT,
        access_log=logger,
    )

# link_preview.py - Inline URL preview (Open Graph / <title> extraction)

import asyncio
import re
import ssl
import traceback
from urllib.parse import urlparse

from PySide6.QtWidgets import QTextEdit
from PySide6.QtGui import QTextCursor, QTextCharFormat, QTextFrameFormat, QColor, QFont, QBrush, QImage
from PySide6.QtCore import QObject, Signal, QTimer, QUrl, Qt

import state
from irc_client import _URL_RE


def _dbg(*args):
    state.dbg(state.LOG_DEBUG, '[link_preview]', *args)


# ---------------------------------------------------------------------------
# HTML meta tag parsing (minimal — only parses <head>)
# ---------------------------------------------------------------------------

_OG_RE = re.compile(
    r'<meta\s[^>]*?property\s*=\s*["\']og:(\w+)["\'][^>]*?content\s*=\s*["\']([^"\']*)["\']'
    r'|<meta\s[^>]*?content\s*=\s*["\']([^"\']*)["\'][^>]*?property\s*=\s*["\']og:(\w+)["\']',
    re.IGNORECASE | re.DOTALL)

_TITLE_RE = re.compile(r'<title[^>]*>(.*?)</title>', re.IGNORECASE | re.DOTALL)

_HTML_ENT = re.compile(r'&(#?[a-zA-Z0-9]+);')


def _decode_entities(s):
    """Decode common HTML entities."""
    import html
    try:
        return html.unescape(s)
    except Exception:
        return s


def _parse_head(html_bytes, encoding='utf-8'):
    """Extract Open Graph tags and <title> from HTML <head>.

    Returns dict with keys: title, description, image (all optional).
    """
    try:
        text = html_bytes.decode(encoding, errors='replace')
    except Exception:
        text = html_bytes.decode('utf-8', errors='replace')

    result = {}

    # Open Graph tags
    for m in _OG_RE.finditer(text):
        if m.group(1):
            key, val = m.group(1).lower(), m.group(2)
        else:
            key, val = m.group(4).lower(), m.group(3)
        val = _decode_entities(val.strip())
        if key == 'title' and 'title' not in result:
            result['title'] = val
        elif key == 'description' and 'description' not in result:
            result['description'] = val
        elif key == 'image' and 'image' not in result:
            result['image'] = val

    # Fallback to <title> tag
    if 'title' not in result:
        m = _TITLE_RE.search(text)
        if m:
            result['title'] = _decode_entities(m.group(1).strip())

    return result


# ---------------------------------------------------------------------------
# Async URL fetching
# ---------------------------------------------------------------------------

_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

# Cookies to bypass consent walls (YouTube, etc.)
_COOKIES = 'CONSENT=YES+cb; SOCS=CAESEwgDEgk0ODE3Nzk3MjQaAmVuIAEaBgiA_LyaBg'


async def _fetch_head(url, max_size=65536, timeout=10.0, proxy=''):
    """Fetch the <head> section of a URL.

    Returns bytes (up to max_size), or None on failure.
    Only follows up to 3 redirects.
    """
    import aiohttp
    try:
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout),
                                   max_redirects=3,
                                   proxy=proxy or None,
                                   headers={'User-Agent': _UA, 'Cookie': _COOKIES}) as resp:
                if resp.status != 200:
                    _dbg('fetch_head %s: HTTP %d' % (url, resp.status))
                    return None
                ct = resp.headers.get('content-type', '')
                if 'text/html' not in ct and 'application/xhtml' not in ct:
                    _dbg('fetch_head %s: bad content-type %r' % (url, ct))
                    return None
                # Read up to max_size bytes
                data = await resp.content.read(max_size)
                _dbg('fetch_head %s: got %d bytes' % (url, len(data)))
                return data
    except Exception as e:
        _dbg('fetch_head %s: %s' % (url, e))
        return None


# Try stdlib fallback if aiohttp not available
_USE_AIOHTTP = True
try:
    import aiohttp
except ImportError:
    _USE_AIOHTTP = False


async def _fetch_head_stdlib(url, max_size=65536, timeout=10.0, proxy=''):
    """Fallback fetcher using urllib (runs in thread pool)."""
    import urllib.request
    import urllib.error

    def _do_fetch():
        try:
            req = urllib.request.Request(url, headers={'User-Agent': _UA, 'Cookie': _COOKIES})
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            if proxy:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({
                        'http': proxy, 'https': proxy}))
                with opener.open(req, timeout=timeout) as resp:
                    ct = resp.headers.get('content-type', '')
                    if 'text/html' not in ct and 'application/xhtml' not in ct:
                        _dbg('fetch_head_stdlib %s: bad content-type %r' % (url, ct))
                        return None
                    return resp.read(max_size)
            else:
                with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                    ct = resp.headers.get('content-type', '')
                    if 'text/html' not in ct and 'application/xhtml' not in ct:
                        _dbg('fetch_head_stdlib %s: bad content-type %r' % (url, ct))
                        return None
                    return resp.read(max_size)
        except Exception as e:
            _dbg('fetch_head_stdlib %s: %s' % (url, e))
            return None

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _do_fetch)


async def _fetch_image(url, max_size=262144, timeout=10.0, proxy=''):
    """Fetch image data from a URL. Returns bytes or None.
    Skips images larger than max_size to avoid truncated/corrupt downloads."""
    if _USE_AIOHTTP:
        import aiohttp
        try:
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
            connector = aiohttp.TCPConnector(ssl=ssl_ctx)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout),
                                       max_redirects=3,
                                       proxy=proxy or None,
                                       headers={'User-Agent': _UA, 'Cookie': _COOKIES}) as resp:
                    if resp.status != 200:
                        _dbg('fetch_image %s: HTTP %d' % (url, resp.status))
                        return None
                    ct = resp.headers.get('content-type', '')
                    if 'image/' not in ct:
                        _dbg('fetch_image %s: bad content-type %r' % (url, ct))
                        return None
                    # Check Content-Length — skip if too large
                    cl = resp.headers.get('content-length')
                    if cl and int(cl) > max_size:
                        _dbg('fetch_image %s: too large (%s bytes)' % (url, cl))
                        return None
                    data = await resp.content.read(max_size)
                    # Verify we got the full image (not truncated)
                    if cl and len(data) < int(cl):
                        _dbg('fetch_image %s: truncated (%d/%s bytes)' % (url, len(data), cl))
                        return None
                    _dbg('fetch_image %s: got %d bytes' % (url, len(data)))
                    return data
        except Exception as e:
            _dbg('fetch_image %s: %s' % (url, e))
            return None
    else:
        import urllib.request
        def _do():
            try:
                req = urllib.request.Request(url, headers={'User-Agent': _UA, 'Cookie': _COOKIES})
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                if proxy:
                    opener = urllib.request.build_opener(
                        urllib.request.ProxyHandler({'http': proxy, 'https': proxy}))
                    resp = opener.open(req, timeout=timeout)
                else:
                    resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
                with resp:
                    ct = resp.headers.get('content-type', '')
                    if 'image/' not in ct:
                        _dbg('fetch_image_stdlib %s: bad content-type %r' % (url, ct))
                        return None
                    cl = resp.headers.get('content-length')
                    if cl and int(cl) > max_size:
                        _dbg('fetch_image_stdlib %s: too large (%s bytes)' % (url, cl))
                        return None
                    data = resp.read(max_size)
                    if cl and len(data) < int(cl):
                        _dbg('fetch_image_stdlib %s: truncated' % url)
                        return None
                    return data
            except Exception as e:
                _dbg('fetch_image_stdlib %s: %s' % (url, e))
                return None
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _do)


# ---------------------------------------------------------------------------
# oEmbed providers — sites where scraping <head> is unreliable
# ---------------------------------------------------------------------------

_OEMBED_PROVIDERS = [
    # (url regex, oembed endpoint template)
    # YouTube
    (re.compile(r'https?://(?:www\.|m\.)?youtube\.com/watch\?'),
     'https://www.youtube.com/oembed?url={url}&format=json'),
    (re.compile(r'https?://youtu\.be/'),
     'https://www.youtube.com/oembed?url={url}&format=json'),
    (re.compile(r'https?://(?:www\.|m\.)?youtube\.com/shorts/'),
     'https://www.youtube.com/oembed?url={url}&format=json'),
    # Vimeo
    (re.compile(r'https?://(?:www\.)?vimeo\.com/\d+'),
     'https://vimeo.com/api/oembed.json?url={url}'),
    # Reddit
    (re.compile(r'https?://(?:www\.|old\.|new\.)?reddit\.com/r/\w+/comments/'),
     'https://www.reddit.com/oembed?url={url}'),
    # Spotify
    (re.compile(r'https?://open\.spotify\.com/(?:track|album|playlist|episode)/'),
     'https://open.spotify.com/oembed?url={url}'),
    # SoundCloud
    (re.compile(r'https?://soundcloud\.com/.+/.+'),
     'https://soundcloud.com/oembed?url={url}&format=json'),
    # TikTok
    (re.compile(r'https?://(?:www\.|vm\.)?tiktok\.com/'),
     'https://www.tiktok.com/oembed?url={url}'),
    # DailyMotion
    (re.compile(r'https?://(?:www\.)?dailymotion\.com/video/'),
     'https://www.dailymotion.com/services/oembed?url={url}&format=json'),
]

# Site-specific handlers (for sites that need custom logic)
_RE_TWITTER = re.compile(
    r'https?://(?:www\.)?(?:twitter\.com|x\.com)/\w+/status/(\d+)')
_RE_WIKIPEDIA = re.compile(
    r'https?://([a-z]{2,3})\.wikipedia\.org/wiki/(.+)')


async def _fetch_json(url, timeout=10.0, proxy=''):
    """Fetch a URL and parse as JSON. Returns dict or None."""
    import json
    if _USE_AIOHTTP:
        import aiohttp
        try:
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
            connector = aiohttp.TCPConnector(ssl=ssl_ctx)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout),
                                       proxy=proxy or None,
                                       headers={'User-Agent': _UA}) as resp:
                    if resp.status != 200:
                        _dbg('fetch_json %s: HTTP %d' % (url, resp.status))
                        return None
                    return await resp.json(content_type=None)
        except Exception as e:
            _dbg('fetch_json %s: %s' % (url, e))
            return None
    else:
        import urllib.request
        def _do():
            try:
                req = urllib.request.Request(url, headers={'User-Agent': _UA})
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                    return json.loads(resp.read(65536))
            except Exception as e:
                _dbg('fetch_json_stdlib %s: %s' % (url, e))
                return None
        return await asyncio.get_event_loop().run_in_executor(None, _do)


async def _try_twitter(url, timeout=10.0, proxy=''):
    """Fetch Twitter/X preview via fxtwitter API."""
    m = _RE_TWITTER.match(url)
    if not m:
        return None
    tweet_id = m.group(1)
    api_url = 'https://api.fxtwitter.com/status/%s' % tweet_id
    _dbg('trying fxtwitter: %s' % api_url)
    data = await _fetch_json(api_url, timeout=timeout, proxy=proxy)
    if not data or not data.get('tweet'):
        _dbg('fxtwitter: no tweet data')
        return None
    tweet = data['tweet']
    author = tweet.get('author', {})
    name = author.get('name', '')
    handle = '@%s' % author.get('screen_name', '') if author.get('screen_name') else ''
    title = '%s (%s)' % (name, handle) if name and handle else name or handle or 'Tweet'
    info = {'title': title}
    text = tweet.get('text', '')
    if text:
        info['description'] = text[:300]
    # Try to get a media thumbnail
    media = tweet.get('media', {})
    photos = media.get('photos', [])
    if photos:
        info['image'] = photos[0].get('url', '')
    elif media.get('videos'):
        thumb = media['videos'][0].get('thumbnail_url', '')
        if thumb:
            info['image'] = thumb
    _dbg('fxtwitter result:', {k: v[:80] + '...' if isinstance(v, str) and len(v) > 80 else v
                               for k, v in info.items()})
    return info


async def _try_wikipedia(url, timeout=10.0, proxy=''):
    """Fetch Wikipedia preview via REST API."""
    m = _RE_WIKIPEDIA.match(url)
    if not m:
        return None
    lang, article = m.group(1), m.group(2)
    # Remove fragment/anchor
    article = article.split('#')[0]
    from urllib.parse import unquote
    api_url = 'https://%s.wikipedia.org/api/rest_v1/page/summary/%s' % (lang, article)
    _dbg('trying Wikipedia API: %s' % api_url)
    data = await _fetch_json(api_url, timeout=timeout, proxy=proxy)
    if not data or not data.get('title'):
        _dbg('Wikipedia API: no data')
        return None
    info = {'title': data['title']}
    if data.get('extract'):
        info['description'] = data['extract'][:300]
    thumb = data.get('thumbnail', {})
    if thumb.get('source'):
        info['image'] = thumb['source']
    _dbg('Wikipedia result:', {k: v[:80] + '...' if isinstance(v, str) and len(v) > 80 else v
                               for k, v in info.items()})
    return info


async def _try_oembed(url, timeout=10.0, proxy=''):
    """Try oEmbed providers for the URL. Returns info dict or None."""
    from urllib.parse import quote
    for pattern, endpoint_tmpl in _OEMBED_PROVIDERS:
        if pattern.search(url):
            endpoint = endpoint_tmpl.replace('{url}', quote(url, safe=''))
            _dbg('trying oEmbed: %s' % endpoint)
            data = await _fetch_json(endpoint, timeout=timeout, proxy=proxy)
            if data and data.get('title'):
                info = {'title': data['title']}
                if data.get('author_name'):
                    info['description'] = data['author_name']
                if data.get('thumbnail_url'):
                    info['image'] = data['thumbnail_url']
                _dbg('oEmbed result:', info)
                return info
            elif data:
                _dbg('oEmbed returned data but no title:', data)
            break
    return None


async def _try_special_handlers(url, timeout=10.0, proxy=''):
    """Try site-specific handlers, then oEmbed. Returns info dict or None."""
    # Twitter/X
    if _RE_TWITTER.match(url):
        return await _try_twitter(url, timeout=timeout, proxy=proxy)
    # Wikipedia
    if _RE_WIKIPEDIA.match(url):
        return await _try_wikipedia(url, timeout=timeout, proxy=proxy)
    # oEmbed providers
    return await _try_oembed(url, timeout=timeout, proxy=proxy)


async def fetch_preview(url, max_size=65536, timeout=10.0, proxy=''):
    """Fetch and parse a URL preview.

    Returns dict with title, description, image (all optional), or None.
    Tries site-specific handlers (Twitter, Wikipedia) and oEmbed
    (YouTube, Vimeo, Reddit, Spotify, etc.) first, then falls back to
    scraping OG tags from <head>.
    """
    _dbg('fetch_preview %s (max_size=%d, timeout=%.1f, aiohttp=%s)'
         % (url, max_size, timeout, _USE_AIOHTTP))

    # Try site-specific handlers and oEmbed first
    info = await _try_special_handlers(url, timeout=timeout, proxy=proxy)
    if info:
        info['url'] = url
    else:
        # Fall back to scraping OG tags
        if _USE_AIOHTTP:
            data = await _fetch_head(url, max_size, timeout, proxy)
        else:
            data = await _fetch_head_stdlib(url, max_size, timeout, proxy)
        if not data:
            _dbg('fetch_preview %s: no data returned' % url)
            return None
        _dbg('fetch_preview %s: got %d bytes, first 500: %s'
             % (url, len(data), data[:500].decode('utf-8', errors='replace')))
        info = _parse_head(data)
        _dbg('fetch_preview %s: parsed ->' % url,
             {k: (v[:80] + '...' if isinstance(v, str) and len(v) > 80 else v)
              for k, v in info.items() if k != 'image_data'})
        if not info.get('title'):
            _dbg('fetch_preview %s: no title found' % url)
            return None
        info['url'] = url

    # Fetch thumbnail image if available
    img_url = info.get('image')
    if img_url:
        # Resolve relative URLs
        if img_url.startswith('//'):
            img_url = 'https:' + img_url
        elif img_url.startswith('/'):
            parsed = urlparse(url)
            img_url = '%s://%s%s' % (parsed.scheme, parsed.netloc, img_url)
        try:
            img_data = await _fetch_image(img_url, max_size=262144,
                                          timeout=timeout, proxy=proxy)
            if img_data:
                info['image_data'] = img_data
        except Exception:
            pass
    return info


# ---------------------------------------------------------------------------
# Preview rendering into QTextEdit
# ---------------------------------------------------------------------------

_preview_counter = 0

def _insert_preview(window, info, marker_name=None):
    """Insert a link preview block into the chat output.

    *marker_name* is an optional anchor name to find the insertion point.
    If None, inserts at the end.
    """
    global _preview_counter
    if not window._widget_alive():
        return
    cfg = state.config
    doc = window.output.document()

    # Find the marker anchor position
    insert_cur = None
    if marker_name:
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid() and frag.charFormat().anchorHref() == marker_name:
                    insert_cur = QTextCursor(doc)
                    insert_cur.setPosition(frag.position() + frag.length())
                    break
                it += 1
            if insert_cur:
                break
            block = block.next()

    cur = insert_cur or QTextCursor(doc)
    if not insert_cur:
        cur.movePosition(QTextCursor.MoveOperation.End)
    cur.insertText('\n')

    # Preview box styling
    width = cfg.link_preview_width
    height = cfg.link_preview_height
    border_color = '#cccccc'
    bg_color = '#f8f8f8' if cfg.bgcolor.lightness() > 128 else '#2a2a2a'
    text_color = '#333333' if cfg.bgcolor.lightness() > 128 else '#cccccc'
    link_color = cfg.color_link.name()

    title = info.get('title', '')
    desc = info.get('description', '')
    url = info.get('url', '')
    image_data = info.get('image_data')

    # Truncate description
    if len(desc) > 200:
        desc = desc[:197] + '...'

    # Register thumbnail image with the document if available
    img_name = ''
    if image_data:
        img = QImage()
        if img.loadFromData(image_data):
            # Scale to fit preview height
            thumb_h = max(40, height - 16)
            thumb_w = int(img.width() * thumb_h / max(img.height(), 1))
            if thumb_w > width // 2:
                thumb_w = width // 2
                thumb_h = int(img.height() * thumb_w / max(img.width(), 1))
            img = img.scaled(thumb_w, thumb_h,
                             aspectMode=Qt.AspectRatioMode.KeepAspectRatio,
                             mode=Qt.TransformationMode.SmoothTransformation)
            _preview_counter += 1
            img_name = 'preview_%d' % _preview_counter
            window.output.document().addResource(
                3,  # QTextDocument.ResourceType.ImageResource
                QUrl(img_name), img)

    # Build as HTML table for compact layout
    html = (
        '<table cellpadding="4" cellspacing="0" '
        'style="border: 1px solid %s; background-color: %s; '
        'max-width: %dpx; margin: 2px 0 2px 20px;">'
        '<tr>'
        % (border_color, bg_color, width)
    )
    # Thumbnail cell
    if img_name:
        html += '<td style="vertical-align: top; padding-right: 6px;"><img src="%s"></td>' % img_name
    # Text cell
    html += (
        '<td style="vertical-align: top;">'
        '<a href="%s" style="color: %s; font-weight: bold; '
        'text-decoration: none; font-size: 9pt;">%s</a>'
        % (url, link_color, _escape_html(title))
    )
    if desc:
        html += (
            '<br><span style="color: %s; font-size: 8pt;">%s</span>'
            % (text_color, _escape_html(desc))
        )
    html += (
        '<br><span style="color: %s; font-size: 7pt;">%s</span>'
        % (link_color, _escape_html(_truncate_url(url)))
    )
    html += '</td></tr></table>'

    cur.insertHtml(html)
    # Restore the main cursor to end of document
    window.cur.movePosition(QTextCursor.MoveOperation.End)
    window._updateBottomAlign()


def _escape_html(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def _truncate_url(url, max_len=60):
    if len(url) <= max_len:
        return url
    return url[:max_len - 3] + '...'


# ---------------------------------------------------------------------------
# Integration: called after a message is rendered
# ---------------------------------------------------------------------------

_pending = set()   # URLs currently being fetched (avoid duplicates)
_previewed = set()  # URLs that have already been previewed (avoid re-preview)


def check_and_preview(window, text):
    """Check text for URLs and fetch previews asynchronously.

    Called from message handlers after the message is displayed.
    """
    if not state.config.link_preview_enabled:
        return

    # Extract URLs (reuse the IRC client regex)
    urls = []
    for m in _URL_RE.finditer(text):
        url = m.group(0)
        while url and url[-1] in '.,;:!?\'"':
            url = url[:-1]
        while url.endswith(')') and url.count(')') > url.count('('):
            url = url[:-1]
        if url and url not in _pending and url not in _previewed:
            urls.append(url)

    # Insert a marker anchor at end of document so we can find it later
    _preview_marker_id = id(window.output.document()) + window.output.document().characterCount()
    marker_name = '_lp_%d' % _preview_marker_id
    cur = window.cur
    fmt = QTextCharFormat()
    fmt.setAnchor(True)
    fmt.setAnchorHref(marker_name)
    fmt.setFontPointSize(1)
    fmt.setForeground(QColor(Qt.GlobalColor.transparent))
    cur.insertText('\u200b', fmt)  # zero-width space as anchor
    cur.movePosition(QTextCursor.MoveOperation.End)

    for url in urls:
        _pending.add(url)
        asyncio.ensure_future(_fetch_and_insert(window, url, marker_name))


async def _fetch_and_insert(window, url, marker_name=None):
    """Fetch a preview and insert it into the window."""
    try:
        info = await fetch_preview(
            url,
            max_size=state.config.link_preview_max_size,
            timeout=state.config.link_preview_timeout,
            proxy=state.config.link_preview_proxy)
        if info:
            _dbg('inserting preview for %s: %s' % (url, info.get('title', '')))
            _insert_preview(window, info, marker_name)
            _previewed.add(url)
            if len(_previewed) > 500:
                _previewed.clear()
        else:
            _dbg('no preview for %s' % url)
    except Exception as e:
        _dbg('_fetch_and_insert %s: %s' % (url, e))
    finally:
        _pending.discard(url)

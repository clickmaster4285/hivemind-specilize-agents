"""A self-hosted image CAPTCHA (BACKLOG L16 S2b, ISSUES_LOG O39; approved by the user 2026-11-11).

The API draws a few characters with Pillow (its own font: no files, nothing leaves the LAN), each turned
and shifted, over noise lines and dots. The answer stays in memory (the API is one process) for
CAPTCHA_LIFETIME and can be checked once — right or wrong, it is gone. At most MAX_OPEN wait at a time
(the oldest are dropped), so asking for images cannot fill memory.

Asked on every sign-up, and on a login once the account or the address has LOGIN_CAPTCHA_AFTER failures
(src/core/abuse.py).
"""

from __future__ import annotations

import base64
import io
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable

from PIL import Image, ImageDraw, ImageFont

CAPTCHA_LENGTH = 6
# no look-alikes: 0/O, 1/I/l, 2/Z, 5/S, 8/B are left out; the answer is compared in upper case
ALPHABET = "ACDEFGHJKMNPQRTUVWXY34679"
CAPTCHA_LIFETIME_SECONDS = 5 * 60
# a few times more than people signing in at once on a LAN; each entry is a few bytes
MAX_OPEN = 2000
ID_BYTES = 16

# ── the picture ───────────────────────────────────────────────────────────────────────────────────────
WIDTH, HEIGHT = 240, 80
FONT_SIZE = 42
CHAR_STEP = 36                 # horizontal distance between characters
MARGIN_X = 16
MAX_TURN_DEGREES = 25
MAX_SHIFT_Y = 10
NOISE_LINES = 6
NOISE_DOTS = 350
BACKGROUND = (246, 243, 236)
INK_DARKEST, INK_LIGHTEST = 20, 110      # character colours: dark enough to read, varied enough to confuse
NOISE_DARKEST, NOISE_LIGHTEST = 90, 200

_random = secrets.SystemRandom()


def _ink(darkest: int, lightest: int) -> tuple[int, int, int]:
    return tuple(_random.randint(darkest, lightest) for _ in range(3))


def draw(text: str) -> bytes:
    """A PNG of `text`, made hard for a program to read."""
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    font = ImageFont.load_default(size=FONT_SIZE)
    for n, char in enumerate(text):
        glyph = Image.new("RGBA", (FONT_SIZE + MAX_SHIFT_Y * 2,) * 2, (0, 0, 0, 0))
        ImageDraw.Draw(glyph).text((MAX_SHIFT_Y, 0), char, font=font, fill=_ink(INK_DARKEST, INK_LIGHTEST))
        glyph = glyph.rotate(_random.uniform(-MAX_TURN_DEGREES, MAX_TURN_DEGREES), expand=True)
        x = MARGIN_X + n * CHAR_STEP + _random.randint(-MAX_SHIFT_Y // 2, MAX_SHIFT_Y // 2)
        y = (HEIGHT - glyph.height) // 2 + _random.randint(-MAX_SHIFT_Y, MAX_SHIFT_Y)
        image.paste(glyph, (x, y), glyph)
    pen = ImageDraw.Draw(image)
    for _ in range(NOISE_LINES):
        start = (_random.randint(0, WIDTH), _random.randint(0, HEIGHT))
        end = (_random.randint(0, WIDTH), _random.randint(0, HEIGHT))
        pen.line([start, end], fill=_ink(NOISE_DARKEST, NOISE_LIGHTEST), width=_random.randint(1, 2))
    for _ in range(NOISE_DOTS):
        pen.point((_random.randrange(WIDTH), _random.randrange(HEIGHT)), fill=_ink(NOISE_DARKEST, NOISE_LIGHTEST))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


class CaptchaStore:
    """The open CAPTCHAs: id → (answer, when it runs out)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self._open: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._lock = threading.Lock()          # sync routes run in a thread pool

    def new(self) -> dict[str, str]:
        """A new CAPTCHA for the page: its id and the image as a data URL."""
        answer = "".join(_random.choice(ALPHABET) for _ in range(CAPTCHA_LENGTH))
        captcha_id = secrets.token_urlsafe(ID_BYTES)
        with self._lock:
            self._drop_expired()
            self._open[captcha_id] = (answer, self.clock() + CAPTCHA_LIFETIME_SECONDS)
            while len(self._open) > MAX_OPEN:
                self._open.popitem(last=False)
        png = base64.b64encode(draw(answer)).decode()
        return {"id": captcha_id, "image": f"data:image/png;base64,{png}"}

    def check(self, captcha_id: str | None, answer: str | None) -> bool:
        """Whether `answer` is right; the CAPTCHA is used up either way."""
        if not captcha_id or answer is None:
            return False
        with self._lock:
            entry = self._open.pop(captcha_id, None)
        if entry is None or entry[1] <= self.clock():
            return False
        return secrets.compare_digest(entry[0].encode(), answer.strip().upper().encode())

    def _drop_expired(self) -> None:
        """Entries are kept oldest first and all live equally long: the expired ones are at the front."""
        now = self.clock()
        while self._open and next(iter(self._open.values()))[1] <= now:
            self._open.popitem(last=False)

    def reset(self) -> None:
        with self._lock:
            self._open.clear()


store = CaptchaStore()

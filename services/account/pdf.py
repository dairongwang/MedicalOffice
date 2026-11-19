"""A small PDF writer — standard library only (like the rest of this service: no SDK to pin per OS).

Enough for the office's documents (prescriptions.py, referrals.py, visit_summaries.py): Letter pages,
text in Helvetica / Helvetica-Bold (the PDF standard fonts — every viewer has them, nothing is embedded),
word-wrapped paragraphs, lines, filled boxes, PNG images (a drawn signature — ``image``) and new pages
when a page is full. Text is encoded as WinAnsi (cp1252): "é", "—", "•",
"°" print as such; anything else becomes "?". The output is a plain PDF 1.4 (page content uncompressed,
images Flate-compressed) with a correct cross-reference table (``qpdf --check`` clean).

    doc = PdfDocument(title="Prescription RX-000001")
    doc.text(72, 720, "Hello", size=14, bold=True)
    y = doc.paragraph(72, 700, "A long text …", width=468)
    doc.line(72, y, 540, y)
    data = doc.output()          # bytes
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone

PAGE_WIDTH, PAGE_HEIGHT = 612, 792  # US Letter, points

# Advance widths of Helvetica (Adobe AFM, 1/1000 em) for the printable ASCII range 32–126.
_HELVETICA = [
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,  # space … /
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,  # 0 … ?
    1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,  # @ … O
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,  # P … _
    333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,  # ` … o
    556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584,  # p … ~
]  # fmt: skip
# Helvetica-Bold for the same range.
_HELVETICA_BOLD = [
    278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 333, 333, 584, 584, 584, 611,
    975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778,
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556,
    333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611,
    611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584,
]  # fmt: skip


def text_width(text: str, size: float, bold: bool = False) -> float:
    """Width of ``text`` in points at ``size`` (characters outside ASCII count as a digit's width)."""
    table = _HELVETICA_BOLD if bold else _HELVETICA
    units = sum(table[ord(ch) - 32] if 32 <= ord(ch) <= 126 else 556 for ch in text)
    return units * size / 1000


def wrap(text: str, width: float, size: float, bold: bool = False) -> list[str]:
    """Word-wrap ``text`` to lines no wider than ``width`` (a word longer than a line is cut)."""
    lines: list[str] = []
    for raw in (text or "").splitlines() or [""]:
        line = ""
        for word in raw.split(" "):
            candidate = f"{line} {word}" if line else word
            if text_width(candidate, size, bold) <= width:
                line = candidate
                continue
            if line:
                lines.append(line)
            while text_width(word, size, bold) > width and len(word) > 1:  # a very long word
                cut = len(word)
                while cut > 1 and text_width(word[:cut], size, bold) > width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            line = word
        lines.append(line)
    return lines


def _literal(text: str) -> bytes:
    data = text.encode("cp1252", errors="replace")
    return b"(" + data.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


def _info_literal(text: str) -> bytes:
    """A document-information string (title, author): UTF-16BE with its byte-order mark."""
    data = b"\xfe\xff" + text.encode("utf-16-be")
    return b"(" + data.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


def _num(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


# ---------------------------------------------------------------------------- PNG images


@dataclass
class Image:
    """A decoded image, ready for the PDF: 8-bit samples, transparency already flattened onto white."""

    width: int
    height: int
    color_space: str  # DeviceGray | DeviceRGB
    samples: bytes  # row by row, no filter bytes

    @property
    def components(self) -> int:
        return 1 if self.color_space == "DeviceGray" else 3


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _unfilter(raw: bytes, width: int, height: int, bpp: int) -> bytearray:
    """Undo the PNG scanline filters (None, Sub, Up, Average, Paeth)."""
    stride = width * bpp
    out = bytearray(stride * height)
    prev = bytearray(stride)
    pos = 0
    for row in range(height):
        kind = raw[pos]
        line = bytearray(raw[pos + 1 : pos + 1 + stride])
        pos += 1 + stride
        if kind == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif kind == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif kind == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif kind == 4:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                up_left = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(left, prev[i], up_left)) & 0xFF
        elif kind != 0:
            raise ValueError(f"unknown PNG filter {kind}")
        out[row * stride : (row + 1) * stride] = line
        prev = line
    return out


def decode_png(data: bytes) -> Image:
    """PNG bytes → ``Image``: 8-bit gray, gray + alpha, RGB or RGBA (what browsers' ``canvas.toDataURL``
    and most tools write), not interlaced. Transparency is flattened onto white. ``ValueError`` otherwise."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, header = 8, [], None
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        kind = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
    if header is None or not idat:
        raise ValueError("incomplete PNG")
    width, height, depth, color, _compression, _filter, interlace = header
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color)
    if depth != 8 or interlace != 0 or channels is None or not (0 < width <= 4000 and 0 < height <= 4000):
        raise ValueError("unsupported PNG (8-bit gray / RGB with optional alpha, not interlaced)")
    pixels = _unfilter(zlib.decompress(b"".join(idat)), width, height, channels)
    if color in (0, 2):
        return Image(width, height, "DeviceGray" if color == 0 else "DeviceRGB", bytes(pixels))
    # With alpha: composite onto white — out = value·α + 255·(1 − α).
    color_channels = channels - 1
    out = bytearray(width * height * color_channels)
    o = 0
    for i in range(0, len(pixels), channels):
        alpha = pixels[i + color_channels]
        for c in range(color_channels):
            out[o] = (pixels[i + c] * alpha + 255 * (255 - alpha)) // 255
            o += 1
    return Image(width, height, "DeviceGray" if color == 4 else "DeviceRGB", bytes(out))


def encode_png(width: int, height: int, rgb: bytes) -> bytes:
    """8-bit RGB samples → a PNG file (no filters) — used for generated images such as the dummy
    signatures of the seed data."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    stride = width * 3
    raw = b"".join(b"\x00" + rgb[y * stride : (y + 1) * stride] for y in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


class PdfDocument:
    """Pages of drawing operations → one PDF file. Coordinates in points, origin bottom-left."""

    def __init__(self, title: str = "", author: str = "", margin: float = 54) -> None:
        self.title, self.author, self.margin = title, author, margin
        self.pages: list[list[bytes]] = [[]]
        self._page = 0  # the page being drawn on
        self.images: list[Image] = []  # /Im1, /Im2, … (every page may use any of them)

    @property
    def _ops(self) -> list[bytes]:
        return self.pages[self._page]

    def new_page(self) -> None:
        self.pages.append([])
        self._page = len(self.pages) - 1

    def on_each_page(self, draw) -> None:
        """``draw(page_number, page_count)`` on every page — a footer or a banner, once the content is done."""
        keep = self._page
        for index in range(len(self.pages)):
            self._page = index
            draw(index + 1, len(self.pages))
        self._page = keep

    def columns(
        self,
        y: float,
        left: tuple[float, float, list[tuple[str, bool]]],
        right: tuple[float, float, list[tuple[str, bool]]],
        *,
        size: float = 9.5,
        bold_size: float = 11,
        leading: float = 14,
    ) -> float:
        """Two blocks side by side — each ``(x, width, [(text, bold), …])``; a text too wide for its column
        is wrapped, empty texts are skipped. Returns the y below the longer block."""

        def lines(width: float, items: list[tuple[str, bool]]) -> list[tuple[str, bool, float]]:
            out = []
            for text, bold in items:
                if text:
                    size_ = bold_size if bold else size
                    out += [(line, bold, size_) for line in wrap(text, width, size_, bold)]
            return out

        a, b = lines(left[1], left[2]), lines(right[1], right[2])
        for i in range(max(len(a), len(b))):
            if i < len(a):
                self.text(left[0], y, a[i][0], size=a[i][2], bold=a[i][1])
            if i < len(b):
                self.text(right[0], y, b[i][0], size=b[i][2], bold=b[i][1])
            y -= leading
        return y

    def text(self, x: float, y: float, text: str, *, size: float = 10, bold: bool = False, gray: float = 0) -> None:
        font = b"/F2" if bold else b"/F1"
        self._ops.append(
            b"BT %s %s Tf %s g %s %s Td %s Tj ET"
            % (font, _num(size).encode(), _num(gray).encode(), _num(x).encode(), _num(y).encode(), _literal(text))
        )

    def text_right(self, x_right: float, y: float, text: str, *, size: float = 10, bold: bool = False) -> None:
        self.text(x_right - text_width(text, size, bold), y, text, size=size, bold=bold)

    def line(self, x1: float, y1: float, x2: float, y2: float, *, width: float = 0.75, gray: float = 0) -> None:
        self._ops.append(
            b"%s G %s w %s %s m %s %s l S" % tuple(_num(v).encode() for v in (gray, width, x1, y1, x2, y2))
        )

    def box(self, x: float, y: float, w: float, h: float, *, gray: float = 0.93) -> None:
        """A filled rectangle (lower-left corner at x, y)."""
        self._ops.append(b"%s g %s %s %s %s re f" % tuple(_num(v).encode() for v in (gray, x, y, w, h)))

    def image(self, x: float, y: float, width: float, height: float, png: bytes | Image) -> None:
        """Draw a PNG (bytes or a decoded ``Image``) into the box whose lower-left corner is (x, y); the
        aspect ratio is kept (centred in the box). ``ValueError`` for an image ``decode_png`` cannot read."""
        img = png if isinstance(png, Image) else decode_png(png)
        self.images.append(img)
        scale = min(width / img.width, height / img.height)
        w, h = img.width * scale, img.height * scale
        x0, y0 = x + (width - w) / 2, y + (height - h) / 2
        self._ops.append(
            b"q %s 0 0 %s %s %s cm /Im%d Do Q"
            % (_num(w).encode(), _num(h).encode(), _num(x0).encode(), _num(y0).encode(), len(self.images))
        )

    def paragraph(
        self,
        x: float,
        y: float,
        text: str,
        *,
        width: float,
        size: float = 10,
        bold: bool = False,
        leading: float | None = None,
        on_new_page=None,
    ) -> float:
        """Word-wrapped text from (x, y) downwards; continues on a new page when the page is full
        (``on_new_page()`` may draw a header and returns the y to continue at). Returns the next y."""
        leading = leading or size * 1.35
        for line in wrap(text, width, size, bold):
            if y < self.margin:
                self.new_page()
                y = on_new_page() if on_new_page else PAGE_HEIGHT - self.margin
            self.text(x, y, line, size=size, bold=bold)
            y -= leading
        return y

    def output(self) -> bytes:
        """The PDF file."""
        objects: list[bytes] = []  # object n is objects[n - 1]

        def add(body: bytes) -> int:
            objects.append(body)
            return len(objects)

        catalog = add(b"")  # filled in below (needs the pages object number)
        pages_obj = add(b"")
        font1 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        font2 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        xobjects = []
        for img in self.images:
            stream = zlib.compress(img.samples)
            xobjects.append(
                add(
                    b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /%s /BitsPerComponent 8 "
                    b"/Filter /FlateDecode /Length %d >>\nstream\n%s\nendstream"
                    % (img.width, img.height, img.color_space.encode(), len(stream), stream)
                )
            )
        xobject_dict = (
            b" /XObject << %s >>" % b" ".join(b"/Im%d %d 0 R" % (i + 1, n) for i, n in enumerate(xobjects))
            if xobjects
            else b""
        )
        kids = []
        for ops in self.pages:
            stream = b"\n".join(ops)
            content = add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
            kids.append(
                add(
                    b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %d %d] /Contents %d 0 R "
                    b"/Resources << /Font << /F1 %d 0 R /F2 %d 0 R >>%s >> >>"
                    % (pages_obj, PAGE_WIDTH, PAGE_HEIGHT, content, font1, font2, xobject_dict)
                )
            )
        objects[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_obj
        objects[pages_obj - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
            b" ".join(b"%d 0 R" % k for k in kids),
            len(kids),
        )
        stamp = datetime.now(timezone.utc).strftime("D:%Y%m%d%H%M%SZ").encode()
        info = add(
            b"<< /Title %s /Author %s /Producer (Medic Office) /CreationDate (%s) >>"
            % (_info_literal(self.title), _info_literal(self.author), stamp)
        )
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for number, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
        for offset in offsets:
            out += b"%010d 00000 n \n" % offset
        out += b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
            len(objects) + 1,
            catalog,
            info,
            xref,
        )
        return bytes(out)

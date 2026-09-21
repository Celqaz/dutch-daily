"""Build an EPUB 3 out of the same sections the HTML preview uses.

Why EPUB: Amazon's "Send to Kindle" converts whatever you send (HTML, DOCX or
EPUB) into its own KFX/AZW3 format, which KOReader cannot render. A real EPUB
placed on the device - or pulled over OPDS, see :mod:`app.opds` - is what
KOReader reads best, and its TOC gives a proper "contents" menu built from the
same Dutch/Japanese anchors as the HTML document.

Written with the standard library only (zipfile + string templates), so the
Docker image keeps its single runtime dependency.
"""
from __future__ import annotations

import html
import re
import uuid
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from .config import Config
from .render import (
    Section,
    document_title,
    languages_of,
    render_body,
    render_styles,
    toc_entries,
)

MIMETYPE = "application/epub+zip"
CONTAINER_PATH = "META-INF/container.xml"
OPF_PATH = "OEBPS/content.opf"
_OPF_DIR = "OEBPS"

_CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="{opf}" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

# Named entities such as &mdash; are not defined in XML, so they are turned into
# characters. The five predefined entities and numeric references are kept.
_NAMED_ENTITY = re.compile(
    r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#[xX][0-9a-fA-F]+);)[a-zA-Z][a-zA-Z0-9]*;"
)


def xml_safe(markup: str) -> str:
    """Make HTML safe to embed in an XHTML document."""
    return _NAMED_ENTITY.sub(lambda match: html.unescape(match.group(0)), markup)


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _package_id(title: str) -> str:
    """Stable identifier, so re-rendering the same day keeps the same book id."""
    return f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, f'language-daily:{title}')}"


def _title_template(title: str) -> str:
    return """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{lang}" lang="{lang}">
<head>
  <meta charset="utf-8"/>
  <title>{title}</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
{body}
</body>
</html>
"""


def _nav_xhtml(title: str, sections: list[Section], lang: str) -> str:
    items: list[str] = []
    for section in sections:
        code = section.profile.code
        label = _esc(section.heading)
        entries = toc_entries(section)
        if entries:
            children = "\n".join(
                f'          <li><a href="text.xhtml#{anchor}">{_esc(text)}</a></li>'
                for text, anchor in entries
            )
            items.append(
                f'      <li><a href="text.xhtml#{code}">{label}</a>\n'
                f"        <ol>\n{children}\n        </ol>\n      </li>"
            )
        else:
            items.append(f'      <li><a href="text.xhtml#{code}">{label}</a></li>')
    body = "\n".join(items)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{lang}" lang="{lang}">
<head>
  <meta charset="utf-8"/>
  <title>Contents</title>
</head>
<body>
  <nav epub:type="toc" id="toc">
    <h1>Contents</h1>
    <ol>
{body}
    </ol>
  </nav>
  <nav epub:type="landmarks" hidden="hidden">
    <ol>
      <li><a epub:type="bodymatter" href="text.xhtml">Start reading</a></li>
    </ol>
  </nav>
</body>
</html>
"""


def _ncx(title: str, sections: list[Section], identifier: str, lang: str) -> str:
    """EPUB 2 style NCX; older readers (and some KOReader builds) prefer it."""
    order = 0
    points: list[str] = []
    for index, section in enumerate(sections, 1):
        code = section.profile.code
        order += 1
        children: list[str] = []
        for child_index, (text, anchor) in enumerate(toc_entries(section), 1):
            order += 1
            children.append(
                f'      <navPoint id="nav-{index}-{child_index}" playOrder="{order}">\n'
                f"        <navLabel><text>{_esc(text)}</text></navLabel>\n"
                f'        <content src="text.xhtml#{anchor}"/>\n'
                f"      </navPoint>"
            )
        inner = "\n" + "\n".join(children) + "\n    " if children else ""
        points.append(
            f'    <navPoint id="nav-{index}" playOrder="{order - len(children)}">\n'
            f"      <navLabel><text>{_esc(section.heading)}</text></navLabel>\n"
            f'      <content src="text.xhtml#{code}"/>{inner}\n'
            f"    </navPoint>"
        )
    nav_map = "\n".join(points)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="{lang}">
  <head>
    <meta name="dtb:uid" content="{_esc(identifier)}"/>
    <meta name="dtb:depth" content="2"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>{_esc(title)}</text></docTitle>
  <navMap>
{nav_map}
  </navMap>
</ncx>
"""


def _content_opf(title: str, identifier: str, modified: str, languages: list[str]) -> str:
    lang_meta = "\n    ".join(f"<dc:language>{_esc(code)}</dc:language>" for code in languages)
    if not lang_meta:
        lang_meta = "<dc:language>en</dc:language>"
    return f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0"
         unique-identifier="pub-id" xml:lang="{_esc(languages[0] if languages else 'en')}">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="pub-id">{_esc(identifier)}</dc:identifier>
    <dc:title>{_esc(title)}</dc:title>
    {lang_meta}
    <dc:creator>Language Daily</dc:creator>
    <dc:description>Daily beginner lesson (key vocabulary, grammar, word building
    and paragraph-by-paragraph translation) written automatically from a real
    article. AI-generated - check important details before relying on them.</dc:description>
    <meta property="dcterms:modified">{_esc(modified)}</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="text" href="text.xhtml" media-type="application/xhtml+xml"/>
    <item id="css" href="style.css" media-type="text/css"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="text"/>
  </spine>
</package>
"""


def build_epub(sections: list[Section], cfg: Config) -> bytes:
    """Render the sections into an EPUB 3 file and return its bytes."""
    title = document_title(cfg, sections)
    languages = languages_of(sections)
    lang = languages[0] if languages else "en"
    identifier = _package_id(title)
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    content = _title_template(title).format(
        lang=_esc(lang), title=_esc(title), body=xml_safe(render_body(sections, cfg))
    )

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        # The mimetype entry must come first, uncompressed and without extra
        # fields, otherwise readers reject the file.
        info = zipfile.ZipInfo("mimetype", date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_STORED
        archive.writestr(info, MIMETYPE)
        archive.writestr(CONTAINER_PATH, _CONTAINER_XML.format(opf=OPF_PATH))
        archive.writestr(
            f"{_OPF_DIR}/content.opf",
            _content_opf(title, identifier, modified, languages),
        )
        archive.writestr(f"{_OPF_DIR}/nav.xhtml", _nav_xhtml(title, sections, lang))
        archive.writestr(f"{_OPF_DIR}/toc.ncx", _ncx(title, sections, identifier, lang))
        archive.writestr(f"{_OPF_DIR}/style.css", render_styles())
        archive.writestr(f"{_OPF_DIR}/text.xhtml", content)
    return buffer.getvalue()


def write_epub(path: Path, sections: list[Section], cfg: Config) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_epub(sections, cfg))
    return path

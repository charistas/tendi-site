#!/usr/bin/env python3
"""Fail-closed static checks for the Tendi site."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Iterator
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "site.config.json").read_text(encoding="utf-8"))

REQUIRED_CONFIG_KEYS = frozenset({
    "claimFamilies", "claimScanExtraPaths", "claimScanCoverage", "claimExemptions",
    "promiseContract", "comparativeClaim", "prohibitedPageText", "requiredPageText",
    "metadataParity", "metadataCrossPageParity", "llmsAgreement", "socialCard",
})
EXPECTED_CLAIM_FAMILIES = frozenset({
    "P", "D1", "D2", "C", "E", "A1", "A2", "A3", "F", "V", "G", "I", "H", "M",
})
EXPECTED_SCAN_EXTRA_PATHS = [
    "llms.txt", "social-card.svg", "favicon.svg", ".well-known/security.txt",
    "robots.txt", "sitemap.xml", "README.md", "AGENTS.md",
    ".github/copilot-instructions.md", ".cursor/rules/static-site.mdc",
]
EXPECTED_PROHIBITED_PAGE_TEXT = {
    "index.html": [
        "Mood tracking without streaks",
        "Your journal will still be there",
        "smart insights",
        "Tendi is free.",
    ],
    "support.html": ["Tendi is free,"],
}
EXPECTED_CANONICAL_METADATA = {
    "index.html": {
        "title": "Tendi: Mood Journal - Check in with one mood.",
        "description": "Check in with one mood. Your entries build a record, and Tendi is honest about what that record can actually show. No streaks. No account required.",
        "canonical": "https://tendijournal.app/",
    },
    "privacy.html": {
        "title": "Privacy Policy - Tendi",
        "description": "How Tendi stores journal content on your device, what can sync through iCloud, and what never goes to Tendi servers.",
        "canonical": "https://tendijournal.app/privacy.html",
    },
    "support.html": {
        "title": "Support - Tendi",
        "description": "Get help with Tendi, find answers to common questions, report a problem, or contact the Tendi support team.",
        "canonical": "https://tendijournal.app/support.html",
    },
}
HOMEPAGE_DESCRIPTION_CLAUSES = (
    "Check in with one mood",
    "Your entries build a record",
    "Tendi is honest about what that record can actually show",
)
EXPECTED_SUPPORT_CONTRACTS = [
    {
        "name": "record-support",
        "selector": "#record-support",
        "mustContain": [
            "A single mood is enough",
            "Save stays explicit",
            "Days without entries stay just that, not failures",
            "Coming back after a break is just coming back",
        ],
    },
    {
        "name": "daily-faq-answer",
        "selector": "#daily-faq-answer",
        "mustContain": [
            "No.",
            "There are no streaks",
            "no catching up",
            "no deficit counters",
            "Days without entries stay just that, not failures",
        ],
    },
    {
        "name": "export-faq-answer",
        "selector": "#export-faq-answer",
        "mustContain": [
            "A full Tendi backup is designed for restoring your journal into Tendi",
            "JSON and CSV carry entries for use elsewhere",
            "PDF is a readable report rather than a backup",
            "Export and restore need no payment and no account",
        ],
    },
]
LEGACY_GUIDANCE_BANNED_TERMS = frozenset({
    "bloomery", "mind garden", "classic garden", "past gardens", "terrarium",
    "mossbear", "procedural garden",
})
CONTRIBUTOR_GUIDANCE_PATHS = frozenset({
    "README.md", "AGENTS.md", ".github/copilot-instructions.md", ".cursor/rules/static-site.mdc",
})
EXPECTED_DEFERRED = frozenset({
    "check_metadata_parity",
    "check_metadata_cross_page_parity",
    "check_social_card_reference_parity",
    "check_llms_agreement",
    "check_social_card (socialCard assertions)",
})
SOCIAL_CARD_INPUT_PATHS = (
    "social-card.svg",
    "assets/app-icon.png",
    "assets/screenshot-home.png",
    "assets/screenshot-insights.png",
    "assets/fonts/Fraunces-opsz-wght-latin.woff2",
    "tools/render_social_card.mjs",
)
LEDGER_KINDS = frozenset({
    "paragraph", "heading", "list_item", "table_cell", "link", "button", "form_text",
    "block_residual", "inline_orphan", "meta", "a11y_attr", "svg_text",
})

INLINE_TAGS = frozenset({
    "span", "strong", "b", "em", "i", "a", "code", "small", "sup", "sub",
    "time", "abbr", "u", "s", "mark", "br",
})
PROSE_TAG_KINDS = {
    **{tag: "heading" for tag in ("h1", "h2", "h3", "h4", "h5", "h6")},
    **{tag: "paragraph" for tag in ("p", "blockquote", "figcaption", "dd", "dt", "summary")},
    "li": "list_item",
    "th": "table_cell", "td": "table_cell", "caption": "table_cell",
    "label": "form_text", "legend": "form_text", "option": "form_text", "optgroup": "form_text",
}
INTERACTIVE_TAGS = frozenset({"a", "button"})
VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
})
NON_CONTENT_TAGS = frozenset({"html", "head", "body", "script", "style", "template", "noscript"})
A11Y_ATTRS = frozenset({
    "alt", "aria-label", "aria-description", "aria-placeholder", "title", "placeholder",
})

ACTIVE_PATHS: frozenset[str] | None = None
ALLOW_DEFERRED = False
WHOLE_STRICT_RUN = True
DEFERRED: list[str] = []


@dataclass(eq=False)
class Node:
    tag: str
    attrs: dict[str, str]
    line: int
    order: int
    parent: Node | None = None
    children: list[Node | TextRun] = field(default_factory=list)


@dataclass(eq=False)
class TextRun:
    text: str
    line: int
    order: int
    parent: Node


@dataclass(frozen=True)
class Unit:
    path: str
    kind: str
    text: str
    start_line: int


class DOMParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("__root__", {}, 1, 0)
        self.stack = [self.root]
        self.counter = 0

    def _next_order(self) -> int:
        self.counter += 1
        return self.counter

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        node = Node(
            tag,
            {key.lower(): value or "" for key, value in attrs},
            self.getpos()[0],
            self._next_order(),
            self.stack[-1],
        )
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data:
            self.stack[-1].children.append(
                TextRun(data, self.getpos()[0], self._next_order(), self.stack[-1])
            )


def fail(message: str) -> None:
    raise AssertionError(message)


def normalize(value: str) -> str:
    return " ".join(value.split())


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_dom_text(source: str) -> Node:
    parser = DOMParser()
    parser.feed(source)
    parser.close()
    return parser.root


def parse_dom(path: str) -> Node:
    return parse_dom_text(read(path))


def iter_nodes(node: Node) -> Iterator[Node]:
    for child in node.children:
        if isinstance(child, Node):
            yield child
            yield from iter_nodes(child)


def iter_text_runs(node: Node) -> Iterator[TextRun]:
    for child in node.children:
        if isinstance(child, TextRun):
            yield child
        else:
            yield from iter_text_runs(child)


def node_text(node: Node) -> str:
    return normalize("".join(run.text for run in iter_text_runs(node)))


def ancestors(node: Node | None) -> Iterator[Node]:
    while node and node.tag != "__root__":
        yield node
        node = node.parent


def is_block(node: Node) -> bool:
    return (
        node.tag not in INLINE_TAGS
        and node.tag not in INTERACTIVE_TAGS
        and node.tag not in NON_CONTENT_TAGS
        and node.tag not in VOID_TAGS
    )


def block_has_own_prose(block: Node) -> bool:
    fragments: list[str] = []

    def walk(node: Node) -> None:
        for child in node.children:
            if isinstance(child, TextRun):
                fragments.append(child.text)
            elif child.tag in INTERACTIVE_TAGS:
                continue
            elif child is not block and is_block(child):
                continue
            else:
                walk(child)

    walk(block)
    return bool(normalize("".join(fragments)))


def standalone_interactive(node: Node) -> bool:
    block = next((candidate for candidate in ancestors(node.parent) if is_block(candidate)), None)
    return block is None or not block_has_own_prose(block)


def owner_for(run: TextRun) -> tuple[Node | None, str]:
    lineage = list(ancestors(run.parent))
    interactive = next((node for node in lineage if node.tag in INTERACTIVE_TAGS), None)
    if interactive and standalone_interactive(interactive):
        return interactive, "link" if interactive.tag == "a" else "button"
    prose = next((node for node in lineage if node.tag in PROSE_TAG_KINDS), None)
    if prose:
        return prose, PROSE_TAG_KINDS[prose.tag]
    block = next((node for node in lineage if is_block(node)), None)
    if block:
        return block, "block_residual"
    return run.parent, "inline_orphan"


def extract_html_units(path: str, source: str | None = None) -> list[Unit]:
    source = read(path) if source is None else source
    root = parse_dom_text(source)
    grouped: dict[tuple[Node, str], list[TextRun]] = {}
    for run in iter_text_runs(root):
        lineage_tags = {node.tag for node in ancestors(run.parent)}
        if lineage_tags & {"script", "style", "template", "noscript", "head"}:
            continue
        owner, kind = owner_for(run)
        if owner:
            grouped.setdefault((owner, kind), []).append(run)
    units = [
        Unit(path, kind, normalize("".join(run.text for run in runs)), owner.line)
        for (owner, kind), runs in grouped.items()
        if normalize("".join(run.text for run in runs))
    ]
    for node in iter_nodes(root):
        if node.tag == "title" and node_text(node):
            units.append(Unit(path, "meta", node_text(node), node.line))
        if node.tag == "meta" and node.attrs.get("content") and (node.attrs.get("name") or node.attrs.get("property")):
            units.append(Unit(path, "meta", normalize(node.attrs["content"]), node.line))
        for attr in A11Y_ATTRS:
            if normalize(node.attrs.get(attr, "")):
                units.append(Unit(path, "a11y_attr", normalize(node.attrs[attr]), node.line))
        if node.tag == "input" and node.attrs.get("type", "").casefold() == "submit" and node.attrs.get("value"):
            units.append(Unit(path, "button", normalize(node.attrs["value"]), node.line))
    return sorted(units, key=lambda unit: (unit.start_line, unit.kind, unit.text))


def extract_svg_units(path: str, source: str | None = None) -> list[Unit]:
    source = read(path) if source is None else source
    root = ET.fromstring(source)
    units: list[Unit] = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == "text":
            text = normalize("".join(element.itertext()))
            if text:
                offset = source.find(text.split()[0])
                line = source[:max(offset, 0)].count("\n") + 1
                units.append(Unit(path, "svg_text", text, line))
    return units


def extract_markdown_units(path: str, source: str | None = None) -> list[Unit]:
    source = read(path) if source is None else source
    lines = source.splitlines()
    units: list[Unit] = []
    paragraph: list[str] = []
    paragraph_line = 0
    paragraph_kind = "paragraph"
    index = 0

    def flush() -> None:
        nonlocal paragraph_kind, paragraph_line
        if paragraph:
            units.append(Unit(path, paragraph_kind, normalize(" ".join(paragraph)), paragraph_line))
            paragraph.clear()
            paragraph_line = 0
            paragraph_kind = "paragraph"

    while index < len(lines):
        stripped = lines[index].strip()
        if stripped.startswith("```"):
            flush()
            fence = stripped[:len(stripped) - len(stripped.lstrip("`"))]
            start = index + 1
            body: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith(fence):
                body.append(lines[index])
                index += 1
            if index >= len(lines):
                fail(f"{path}:{start}: unterminated Markdown fence")
            units.append(Unit(path, "fenced_block", normalize("\n".join(body)), start))
        elif not stripped:
            flush()
        elif stripped.startswith("|"):
            flush()
            units.append(Unit(path, "table_cell", normalize(stripped), index + 1))
        elif re.match(r"^(?:[-*+] |\d+[.)] )", stripped):
            flush()
            paragraph_line = index + 1
            paragraph_kind = "list_item"
            paragraph.append(stripped)
        elif stripped.startswith("#"):
            flush()
            units.append(Unit(path, "heading", normalize(stripped), index + 1))
        else:
            if not paragraph:
                paragraph_line = index + 1
            paragraph.append(stripped)
        index += 1
    flush()
    return units


def extract_plain_units(path: str, source: str | None = None) -> list[Unit]:
    source = read(path) if source is None else source
    units: list[Unit] = []
    for match in re.finditer(r"(?:^|\n\s*\n)(.*?)(?=\n\s*\n|\Z)", source, re.DOTALL):
        text = normalize(match.group(1))
        if text:
            units.append(Unit(path, "paragraph", text, source[:match.start(1)].count("\n") + 1))
    return units


def logical_units(path: str, source: str | None = None) -> list[Unit]:
    if path.endswith(".html"):
        return extract_html_units(path, source)
    if path.endswith(".svg"):
        return extract_svg_units(path, source)
    if path.endswith((".md", ".mdc")) or Path(path).name in {"README.md", "AGENTS.md", "CLAUDE.md"}:
        return extract_markdown_units(path, source)
    if path == "llms.txt":
        return extract_markdown_units(path, source)
    if path.endswith(".txt"):
        return extract_plain_units(path, source)
    return []


def page_url(page: str) -> str:
    suffix = "/" if page == "index.html" else f"/{page}"
    return f"{CONFIG['domain']}{suffix}"


def normalize_local_url(value: str, source_page: str) -> str | None:
    if not value or value.startswith(("#", "mailto:", "tel:", "data:", "javascript:")):
        return None
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"}:
        if f"{parsed.scheme}://{parsed.netloc}" != CONFIG["domain"]:
            return None
        value = parsed.path or "/"
    elif parsed.netloc:
        return None
    value = value.split("#", 1)[0].split("?", 1)[0]
    if value in {"", "/"}:
        return "index.html"
    local = value.lstrip("/") if value.startswith("/") else str(Path(source_page).parent.joinpath(value))
    return str(Path(local))


def png_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        fail(f"{path} is not a PNG")
    return struct.unpack(">II", data[16:24])


def find_nodes(root: Node, tag: str | None = None, node_id: str | None = None, css_class: str | None = None) -> list[Node]:
    found: list[Node] = []
    for node in iter_nodes(root):
        if tag and node.tag != tag:
            continue
        if node_id and node.attrs.get("id") != node_id:
            continue
        if css_class and css_class not in node.attrs.get("class", "").split():
            continue
        found.append(node)
    return found


def select_nodes(root: Node, selector: str) -> list[Node]:
    if selector.startswith("#"):
        return find_nodes(root, node_id=selector[1:])
    if selector.startswith(".") and " " not in selector:
        return find_nodes(root, css_class=selector[1:])
    if selector == ".feature-flow article.feature-row:nth-of-type(3) .feature-copy p:not(.flow-step)":
        flows = find_nodes(root, css_class="feature-flow")
        if len(flows) != 1:
            return []
        articles = [node for node in iter_nodes(flows[0]) if node.tag == "article" and "feature-row" in node.attrs.get("class", "").split()]
        if len(articles) < 3:
            return []
        copies = find_nodes(articles[2], css_class="feature-copy")
        if len(copies) != 1:
            return []
        return [node for node in iter_nodes(copies[0]) if node.tag == "p" and "flow-step" not in node.attrs.get("class", "").split()]
    fail(f"Unsupported configured selector: {selector}")


def get_meta(root: Node, key: str, value: str) -> str | None:
    for node in find_nodes(root, tag="meta"):
        if node.attrs.get(key) == value:
            return node.attrs.get("content")
    return None


def get_link(root: Node, rel: str) -> str | None:
    for node in find_nodes(root, tag="link"):
        if rel in node.attrs.get("rel", "").split():
            return node.attrs.get("href")
    return None


def get_title(root: Node) -> str | None:
    titles = find_nodes(root, tag="title")
    return node_text(titles[0]) if len(titles) == 1 else None


def all_attr_values(root: Node, tag_names: Iterable[str], attr_names: Iterable[str]) -> list[str]:
    tags = set(tag_names)
    attrs = set(attr_names)
    values: list[str] = []
    for node in iter_nodes(root):
        if node.tag in tags:
            values.extend(node.attrs[attr] for attr in attrs if node.attrs.get(attr))
    return values


def derived_scan_universe(config: dict | None = None) -> list[str]:
    config = CONFIG if config is None else config
    paths = [*config["pages"], *config["claimScanExtraPaths"]]
    if len(paths) != len(set(paths)):
        fail("Derived claim scan universe contains duplicate paths")
    for path in paths:
        if not (ROOT / path).is_file():
            fail(f"Claim scan path does not exist: {path}")
    return paths


def scoped_paths(candidates: Iterable[str]) -> list[str]:
    values = list(candidates)
    if ACTIVE_PATHS is None:
        return values
    return [path for path in values if path in ACTIVE_PATHS]


def exact_keys(value: dict, expected: set[str], name: str) -> None:
    if set(value) != expected:
        fail(f"{name} fields must be exactly {sorted(expected)}, got {sorted(value)}")


def require_nonempty_string_list(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
        fail(f"{name} must be a non-empty array of non-empty strings")
    return value


def validate_prohibited_page_text(value: object) -> None:
    if not isinstance(value, dict):
        fail("prohibitedPageText must be an object")
    if set(value) != set(EXPECTED_PROHIBITED_PAGE_TEXT):
        fail("prohibitedPageText pages must exactly match the code-owned contract")
    for path, expected in EXPECTED_PROHIBITED_PAGE_TEXT.items():
        actual = require_nonempty_string_list(value[path], f"prohibitedPageText.{path}")
        if actual != expected:
            fail(f"prohibitedPageText.{path} must exactly match the code-owned literal contract")


def validate_metadata_parity(value: object) -> None:
    if not isinstance(value, dict):
        fail("metadataParity must be an object")
    expected_fields = {
        "titleKeys", "descriptionKeys", "imageKeys", "imageAltKeys",
        "sharedImageAltAcrossPages", "canonicalByPage",
    }
    exact_keys(value, expected_fields, "metadataParity")
    expected_key_lists = {
        "titleKeys": ["og:title", "twitter:title"],
        "descriptionKeys": ["og:description", "twitter:description"],
        "imageKeys": ["og:image", "twitter:image"],
        "imageAltKeys": ["og:image:alt", "twitter:image:alt"],
    }
    for key, expected in expected_key_lists.items():
        actual = require_nonempty_string_list(value[key], f"metadataParity.{key}")
        if actual != expected:
            fail(f"metadataParity.{key} must exactly match the code-owned parity keys")
    if value["sharedImageAltAcrossPages"] is not True:
        fail("metadataParity.sharedImageAltAcrossPages must be true")
    canonical = value["canonicalByPage"]
    if not isinstance(canonical, dict) or set(canonical) != set(EXPECTED_CANONICAL_METADATA):
        fail("metadataParity.canonicalByPage must contain exactly the three published pages")
    for page, expected in EXPECTED_CANONICAL_METADATA.items():
        actual = canonical[page]
        if not isinstance(actual, dict):
            fail(f"metadataParity.canonicalByPage.{page} must be an object")
        exact_keys(actual, {"title", "description", "canonical"}, f"metadataParity.canonicalByPage.{page}")
        if any(not isinstance(item, str) or not item for item in actual.values()):
            fail(f"metadataParity.canonicalByPage.{page} values must be non-empty strings")
        if actual != expected:
            fail(f"metadataParity.canonicalByPage.{page} must match the code-owned canonical metadata")
    homepage_description = canonical["index.html"]["description"]
    if len(homepage_description) != 147:
        fail("homepage canonical description must remain exactly 147 characters")
    positions = [homepage_description.find(clause) for clause in HOMEPAGE_DESCRIPTION_CLAUSES]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        fail("homepage canonical description must preserve capture, record, interpretation order")


def validate_metadata_cross_page_parity(value: object) -> None:
    if not isinstance(value, dict):
        fail("metadataCrossPageParity must be an object")
    exact_keys(value, {"altKeys", "pages"}, "metadataCrossPageParity")
    require_nonempty_string_list(value["altKeys"], "metadataCrossPageParity.altKeys")
    if value["pages"] != CONFIG["pages"]:
        fail("metadataCrossPageParity.pages must equal CONFIG.pages exactly")


def validate_llms_agreement(value: object) -> None:
    if not isinstance(value, dict):
        fail("llmsAgreement must be an object")
    exact_keys(value, {"mustContainVerbatim", "urlsMustAppearInSitemap", "sitemapPath"}, "llmsAgreement")
    require_nonempty_string_list(value["mustContainVerbatim"], "llmsAgreement.mustContainVerbatim")
    if value["urlsMustAppearInSitemap"] is not True:
        fail("llmsAgreement.urlsMustAppearInSitemap must be true")
    if not isinstance(value["sitemapPath"], str) or not (ROOT / value["sitemapPath"]).is_file():
        fail("llmsAgreement.sitemapPath must name an existing file")


def validate_social_card(value: object) -> None:
    if not isinstance(value, dict):
        fail("socialCard must be an object")
    expected = {"width", "height", "version", "pngSha256", "chromiumRevision", "browserVersion", "expectedSvgText", "inputSha256"}
    exact_keys(value, expected, "socialCard")
    if (
        isinstance(value["width"], bool)
        or isinstance(value["height"], bool)
        or not isinstance(value["width"], int)
        or not isinstance(value["height"], int)
        or value["width"] < 1
        or value["height"] < 1
    ):
        fail("socialCard width and height must be positive integers")
    for field_name in ("version", "chromiumRevision", "browserVersion"):
        if not isinstance(value[field_name], str) or not value[field_name]:
            fail(f"socialCard.{field_name} must be a non-empty string")
    if not isinstance(value["pngSha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["pngSha256"]):
        fail("socialCard.pngSha256 must be 64 lowercase hexadecimal characters")
    require_nonempty_string_list(value["expectedSvgText"], "socialCard.expectedSvgText")
    if not isinstance(value["inputSha256"], dict) or set(value["inputSha256"]) != set(SOCIAL_CARD_INPUT_PATHS):
        fail(f"socialCard.inputSha256 must contain exactly {list(SOCIAL_CARD_INPUT_PATHS)}")
    for path, digest in value["inputSha256"].items():
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            fail(f"socialCard.inputSha256[{path!r}] must be 64 lowercase hexadecimal characters")


def validate_config_schema(config: dict, *, strict_presence: bool) -> None:
    manifest = config.get("requiredConfigKeys")
    if not isinstance(manifest, list):
        fail("requiredConfigKeys must be present as an array")
    if any(not isinstance(item, str) for item in manifest):
        fail("requiredConfigKeys entries must be strings")
    if len(manifest) != len(set(manifest)):
        fail("requiredConfigKeys contains duplicate names")
    if set(manifest) != REQUIRED_CONFIG_KEYS:
        missing = sorted(REQUIRED_CONFIG_KEYS - set(manifest))
        extra = sorted(set(manifest) - REQUIRED_CONFIG_KEYS)
        fail(f"requiredConfigKeys must exactly match code authority; missing={missing}, extra={extra}")
    if strict_presence:
        missing = sorted(REQUIRED_CONFIG_KEYS - set(config))
        if missing:
            fail(f"Required configuration keys missing: {missing}")
    if "claimFamilies" in config:
        families = config["claimFamilies"]
        if not isinstance(families, dict) or set(families) != EXPECTED_CLAIM_FAMILIES:
            fail(f"claimFamilies must contain exactly {sorted(EXPECTED_CLAIM_FAMILIES)}")
        for family, patterns in families.items():
            require_nonempty_string_list(patterns, f"claimFamilies.{family}")
            for pattern in patterns:
                re.compile(pattern, re.IGNORECASE)
    if "claimScanExtraPaths" in config and config["claimScanExtraPaths"] != EXPECTED_SCAN_EXTRA_PATHS:
        fail("claimScanExtraPaths must equal the fixed complete non-HTML scan list")
    if "claimScanCoverage" in config:
        coverage = config["claimScanCoverage"]
        if not isinstance(coverage, dict):
            fail("claimScanCoverage must be an object")
        exact_keys(coverage, {"publishedGlobs", "exempt"}, "claimScanCoverage")
        if coverage["publishedGlobs"] != ["**/*.html", "**/*.txt", "**/*.svg", "**/*.md", "**/*.mdc"]:
            fail("claimScanCoverage.publishedGlobs must be the five recursive prose globs")
        if not isinstance(coverage["exempt"], list) or len(coverage["exempt"]) != 1:
            fail("claimScanCoverage.exempt must contain exactly the reviewed OFL entry")
        for exemption in coverage["exempt"]:
            if not isinstance(exemption, dict):
                fail("claimScanCoverage exemption must be an object")
            exact_keys(exemption, {"path", "sha256", "why"}, "claimScanCoverage exemption")
            if exemption["path"] != "assets/fonts/OFL.txt":
                fail("claimScanCoverage exemption path must be assets/fonts/OFL.txt")
            if not isinstance(exemption["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", exemption["sha256"]):
                fail(f"Coverage exemption {exemption.get('path')} sha256 must be 64 lowercase hexadecimal characters")
            if not isinstance(exemption["why"], str) or not exemption["why"]:
                fail(f"Coverage exemption {exemption.get('path')} must include why")
    if "claimExemptions" in config:
        exemptions = config["claimExemptions"]
        if not isinstance(exemptions, list) or len(exemptions) > 2:
            fail("claimExemptions must be an array with no more than the two reviewed entries")
        if strict_presence and len(exemptions) != 2:
            fail("strict verification requires exactly the two reviewed claim exemptions")
        identities: set[tuple[str, str, str, str]] = set()
        for exemption in exemptions:
            if not isinstance(exemption, dict):
                fail("claimExemptions entry must be an object")
            exact_keys(exemption, {"path", "kind", "unit_text", "expected_count", "family", "justification"}, "claimExemptions entry")
            for field_name in ("path", "kind", "unit_text", "family", "justification"):
                if not isinstance(exemption[field_name], str) or not exemption[field_name]:
                    fail(f"claim exemption {field_name} must be a non-empty string")
            if exemption["kind"] not in LEDGER_KINDS:
                fail(f"claim exemption kind must be one of {sorted(LEDGER_KINDS)}")
            if exemption["family"] not in EXPECTED_CLAIM_FAMILIES | {"U"}:
                fail("claim exemption family must name a configured family or U")
            if isinstance(exemption["expected_count"], bool) or not isinstance(exemption["expected_count"], int) or exemption["expected_count"] < 1:
                fail("claim exemption expected_count must be a positive integer")
            identity = (exemption["path"], exemption["kind"], normalize(exemption["unit_text"]), exemption["family"])
            if identity in identities:
                fail(f"Duplicate claim exemption: {identity}")
            identities.add(identity)
    if "promiseContract" in config:
        if not isinstance(config["promiseContract"], dict) or set(config["promiseContract"]) != {"index.html"}:
            fail("promiseContract must contain index.html only")
        promise = config["promiseContract"]["index.html"]
        exact_keys(promise, {"captureSelector", "capture", "ledeSelector", "record", "interpretation", "promiseVerbatim", "supportContracts"}, "promiseContract.index.html")
        for field_name in ("captureSelector", "capture", "ledeSelector", "record", "interpretation", "promiseVerbatim"):
            value = promise[field_name]
            if not isinstance(value, str) or not value:
                fail(f"promiseContract.{field_name} must be a non-empty string")
        if promise["supportContracts"] != EXPECTED_SUPPORT_CONTRACTS:
            fail("promiseContract.supportContracts must exactly match the code-owned selector and meaning contract")
    if "comparativeClaim" in config:
        if not isinstance(config["comparativeClaim"], dict) or set(config["comparativeClaim"]) != {"index.html"}:
            fail("comparativeClaim must contain index.html only")
        exact_keys(config["comparativeClaim"]["index.html"], {"selector", "mustContain", "mustNotContain", "exclusivePatterns"}, "comparativeClaim.index.html")
        require_nonempty_string_list(config["comparativeClaim"]["index.html"]["mustContain"], "comparativeClaim.mustContain")
        require_nonempty_string_list(config["comparativeClaim"]["index.html"]["mustNotContain"], "comparativeClaim.mustNotContain")
        for pattern in require_nonempty_string_list(config["comparativeClaim"]["index.html"]["exclusivePatterns"], "comparativeClaim.exclusivePatterns"):
            re.compile(pattern, re.IGNORECASE)
    if "prohibitedPageText" in config:
        validate_prohibited_page_text(config["prohibitedPageText"])
    if "requiredPageText" in config:
        if not isinstance(config["requiredPageText"], dict):
            fail("requiredPageText must be an object")
        for path, strings in config["requiredPageText"].items():
            require_nonempty_string_list(strings, f"requiredPageText.{path}")
    if "metadataParity" in config:
        validate_metadata_parity(config["metadataParity"])
    if "metadataCrossPageParity" in config:
        validate_metadata_cross_page_parity(config["metadataCrossPageParity"])
    if "llmsAgreement" in config:
        validate_llms_agreement(config["llmsAgreement"])
    if "socialCard" in config:
        validate_social_card(config["socialCard"])


def defer_or_require(key: str, check_name: str) -> bool:
    if key in CONFIG:
        return True
    if not ALLOW_DEFERRED:
        fail(f"{check_name} requires missing configuration key: {key}")
    if check_name not in EXPECTED_DEFERRED:
        fail(f"Unexpected staging deferral: {check_name} ({key} not yet configured)")
    DEFERRED.append(check_name)
    print(f"deferred: {check_name} ({key} not yet configured)")
    return False


def check_required_files() -> None:
    missing = [path for path in CONFIG["requiredFiles"] if not (ROOT / path).exists()]
    if missing:
        fail(f"Required files missing: {missing}")


def check_pages_publish_config() -> None:
    if (ROOT / ".nojekyll").exists():
        fail(".nojekyll disables _config.yml excludes; remove it before publishing from the repository root")
    config_text = read("_config.yml")
    pages_config = CONFIG.get("pagesConfig", {})
    for item in pages_config.get("requiredIncludes", []):
        if f'- "{item}"' not in config_text and f"- {item}" not in config_text:
            fail(f"_config.yml must include {item}")
    for item in pages_config.get("requiredExcludes", []):
        if f'- "{item}"' not in config_text and f"- {item}" not in config_text:
            fail(f"_config.yml must exclude {item}")


def check_html_pages() -> None:
    if not defer_or_require("requiredPageText", "check_html_pages"):
        return
    external_seen: set[str] = set()
    pages = scoped_paths(CONFIG["pages"])
    failures: list[str] = []
    for page in pages:
        root = parse_dom(page)
        expected = page_url(page)
        if not get_title(root):
            failures.append(f"{page} is missing <title>")
        if get_link(root, "canonical") != expected:
            failures.append(f"{page} canonical URL must be {expected}")
        if get_meta(root, "property", "og:url") != expected:
            failures.append(f"{page} og:url must be {expected}")
        if not get_meta(root, "name", "description"):
            failures.append(f"{page} is missing meta description")
        if not get_meta(root, "property", "og:image") or not get_meta(root, "name", "twitter:image"):
            failures.append(f"{page} is missing social image metadata")
        for node in find_nodes(root, tag="img"):
            if "alt" not in node.attrs:
                failures.append(f"{page} has image without alt text: {node.attrs.get('src', '<missing src>')}")
        for value in all_attr_values(root, {"a", "link", "script", "img", "source", "form"}, {"href", "src", "action"}):
            parsed = urlparse(value)
            if parsed.scheme in {"http", "https"} and f"{parsed.scheme}://{parsed.netloc}" != CONFIG["domain"]:
                external_seen.add(value)
            local = normalize_local_url(value, page)
            if local and not (ROOT / local).exists():
                failures.append(f"{page} references missing local asset/page: {value} -> {local}")
        page_text = " ".join(unit.text for unit in extract_html_units(page)).casefold()
        for required in CONFIG["requiredPageText"].get(page, []):
            if required.casefold() not in page_text:
                failures.append(f"{page} is missing required text: {required}")
        if page == "index.html" and "Do I have to check in every day?" in CONFIG["requiredPageText"][page]:
            if len(find_nodes(root, tag="details")) != 7:
                failures.append("index.html must contain exactly seven FAQ details entries")
    if ACTIVE_PATHS is None:
        for expected in CONFIG["expectedExternalLinks"]:
            if expected not in external_seen:
                failures.append(f"Expected external link/form action not found: {expected}")
    if failures:
        fail("; ".join(failures))


def check_forms() -> None:
    allowed = set(CONFIG.get("allowedFormActions", []))
    for page in CONFIG["pages"]:
        for node in find_nodes(parse_dom(page), tag="form"):
            if node.attrs.get("action") not in allowed:
                fail(f"{page} has unexpected form action: {node.attrs.get('action')}")


def check_mailto_placeholders() -> None:
    expected = set(CONFIG.get("requiredMailtoLinks", []))
    found: set[str] = set()
    per_page: dict[str, set[str]] = {}
    for page in CONFIG["pages"]:
        page_found: set[str] = set()
        for node in iter_nodes(parse_dom(page)):
            if node.tag in {"span", "a"} and node.attrs.get("data-email-user") and node.attrs.get("data-email-domain"):
                page_found.add(f"{node.attrs['data-email-user']}@{node.attrs['data-email-domain']}")
            if node.tag == "a" and node.attrs.get("href", "").startswith("mailto:"):
                page_found.add(node.attrs["href"].removeprefix("mailto:"))
        found |= page_found
        per_page[page] = page_found
    if expected - found:
        fail(f"Missing expected mailto/data-email links: {sorted(expected - found)}")
    for page, page_expected in CONFIG.get("requiredPageMailtoLinks", {}).items():
        missing = set(page_expected) - per_page[page]
        if missing:
            fail(f"{page} is missing expected mailto/data-email links: {sorted(missing)}")


def check_sitemap_and_robots() -> None:
    expected_sitemap = f"Sitemap: {CONFIG['domain']}/sitemap.xml"
    if expected_sitemap not in read("robots.txt"):
        fail(f"robots.txt must contain {expected_sitemap}")
    tree = ET.parse(ROOT / "sitemap.xml")
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locs = [node.text for node in tree.findall(".//sm:loc", ns)]
    expected = [f"{CONFIG['domain']}{path}" for path in CONFIG["sitemapPaths"]]
    if locs != expected:
        fail(f"sitemap.xml locs differ. Expected {expected}, got {locs}")


def check_security_txt() -> None:
    text = read(".well-known/security.txt")
    for item in ["Contact: mailto:hello@twodesks.app", f"Canonical: {CONFIG['domain']}/.well-known/security.txt", "Expires:"]:
        if item not in text:
            fail(f"security.txt missing {item}")


def check_disallowed_script_markers() -> None:
    markers = [marker.casefold() for marker in CONFIG.get("disallowedScriptMarkers", [])]
    for path in [*CONFIG["pages"], "assets/site.js"]:
        haystack = read(path).casefold()
        for marker in markers:
            if marker in haystack:
                fail(f"{path} contains disallowed script marker: {marker}")


def check_banned_public_terms() -> None:
    paths = derived_scan_universe()
    terms = [term.casefold() for term in CONFIG.get("bannedPublicTerms", [])]
    failures = banned_public_term_failures({path: read(path) for path in paths}, terms)
    if failures:
        fail("; ".join(failures))


def banned_public_term_failures(contents: dict[str, str], terms: list[str]) -> list[str]:
    failures: list[str] = []
    for path, contents_for_path in contents.items():
        haystack = contents_for_path.casefold()
        for term in terms:
            if path in CONTRIBUTOR_GUIDANCE_PATHS and term in LEGACY_GUIDANCE_BANNED_TERMS:
                continue
            if term in haystack:
                failures.append(f"{path} contains banned public term: {term}")
    return failures


def family_matches(family: str, text: str) -> list[str]:
    if family == "U":
        matches: list[str] = []
        for pattern in [
            r"check.ins? with and without", r"with[- ]and[- ]without",
            r"(?:every pattern|each pattern).{0,60}(?:cohort|comparison group|with and without)",
            r"compares? (?:every|each|all) pattern", r"every pattern is a comparison",
        ]:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                matches.append(match.group(0))
        if re.search(r"\bpattern", text, re.IGNORECASE) and re.search(r"(?:\bA comparison is\b|\bcomparisons are\b)", text, re.IGNORECASE) and not re.search(r"\b(?:when|where|if)\b", text, re.IGNORECASE):
            matches.append("unconditional comparison assertion")
        return matches
    matches = []
    for pattern in CONFIG["claimFamilies"][family]:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            matches.append(match.group(0))
    return matches


def violations_for(unit: Unit) -> list[tuple[str, str]]:
    violations: list[tuple[str, str]] = []
    for family in [*sorted(CONFIG["claimFamilies"]), "U"]:
        violations.extend((family, match) for match in family_matches(family, unit.text))
    return violations


def exemption_matches(exemption: dict, unit: Unit, family: str) -> bool:
    return (
        exemption["path"] == unit.path
        and exemption["kind"] == unit.kind
        and normalize(exemption["unit_text"]) == unit.text
        and exemption["family"] == family
    )


def exemption_count_failure(index: int, exemption: dict, observed: int) -> str | None:
    if observed == exemption["expected_count"]:
        return None
    return (
        f"claim exemption {index + 1} expected_count={exemption['expected_count']} observed={observed}: "
        f"{exemption['path']} [{exemption['kind']}] family {exemption['family']}"
    )


def check_prohibited_claims() -> None:
    for key in ("claimFamilies", "claimScanExtraPaths", "claimExemptions"):
        if not defer_or_require(key, "check_prohibited_claims"):
            return
    paths = scoped_paths(derived_scan_universe())
    units = [unit for path in paths for unit in logical_units(path)]
    exemptions = CONFIG["claimExemptions"]
    if WHOLE_STRICT_RUN and len(exemptions) != 2:
        fail(f"Strict whole verifier requires exactly two reviewed claim exemptions, got {len(exemptions)}")
    exemption_usage = [0 for _ in exemptions]
    failures: list[str] = []
    family_units: dict[str, set[tuple[str, str, str, int]]] = {}
    for unit in units:
        for family, match in violations_for(unit):
            matching = [
                index for index, exemption in enumerate(exemptions)
                if exemption_matches(exemption, unit, family)
            ]
            if matching:
                for index in matching:
                    exemption_usage[index] += 1
                continue
            family_units.setdefault(family, set()).add((unit.path, unit.kind, unit.text, unit.start_line))
            failures.append(f"family {family}: {unit.path}:{unit.start_line} [{unit.kind}] {match!r} in {unit.text!r}")
    if ACTIVE_PATHS is None:
        for index, exemption in enumerate(exemptions):
            observed = exemption_usage[index]
            count_failure = exemption_count_failure(index, exemption, observed)
            if count_failure:
                failures.append(count_failure)
    if failures:
        summary = ", ".join(f"{family}={len(values)}" for family, values in sorted(family_units.items()))
        fail(f"Prohibited claim units ({summary}): " + " | ".join(failures))


def check_promise_contract() -> None:
    if not defer_or_require("promiseContract", "check_promise_contract"):
        return
    contract = CONFIG["promiseContract"]["index.html"]
    root = parse_dom("index.html")
    capture = select_nodes(root, contract["captureSelector"])
    lede = select_nodes(root, contract["ledeSelector"])
    failures: list[str] = []
    if len(capture) != 1 or node_text(capture[0]) != contract["capture"]:
        failures.append(f"capture text must be {contract['capture']!r}")
    headings = [node for node in iter_nodes(root) if node.tag in {"h1", "h2", "h3", "h4", "h5", "h6"}]
    h1s = [node for node in headings if node.tag == "h1"]
    if len(h1s) != 1 or not headings or headings[0] is not h1s[0]:
        failures.append("capture must be the first heading and the only h1")
    if len(lede) != 1:
        failures.append("lede selector must resolve exactly once")
    elif len(capture) == 1:
        lede_text = node_text(lede[0])
        record_index = lede_text.find(contract["record"])
        interpretation_index = lede_text.find(contract["interpretation"])
        if record_index < 0 or interpretation_index <= record_index:
            failures.append("lede must contain record before interpretation")
        if lede[0].order <= capture[0].order:
            failures.append("lede must follow capture in document order")
        combined = normalize(f"{node_text(capture[0])} {lede_text}")
        if combined != contract["promiseVerbatim"]:
            failures.append("capture plus lede must equal promiseVerbatim")
    failures.extend(support_contract_failures(root, contract["supportContracts"]))
    if failures:
        fail("; ".join(failures))


def support_contract_failures(root: Node, contracts: list[dict]) -> list[str]:
    failures: list[str] = []
    for contract in contracts:
        nodes = select_nodes(root, contract["selector"])
        if len(nodes) != 1:
            failures.append(f"{contract['name']} selector must resolve exactly once, got {len(nodes)}")
            continue
        text = node_text(nodes[0])
        for required in contract["mustContain"]:
            if required not in text:
                failures.append(f"{contract['name']} is missing {required!r}")
    return failures


def check_comparative_claim() -> None:
    if not defer_or_require("comparativeClaim", "check_comparative_claim"):
        return
    contract = CONFIG["comparativeClaim"]["index.html"]
    root = parse_dom("index.html")
    nodes = select_nodes(root, contract["selector"])
    if len(nodes) != 1:
        fail(f"comparativeClaim.selector must resolve exactly once, got {len(nodes)}")
    text = node_text(nodes[0])
    failures = [f"missing {item!r}" for item in contract["mustContain"] if item not in text]
    failures += [f"contains prohibited {item!r}" for item in contract["mustNotContain"] if item.casefold() in text.casefold()]
    hero = select_nodes(root, "#hero-title")[0]
    sections = find_nodes(root, tag="section")
    if nodes[0].order <= hero.order or (sections and nodes[0].order <= sections[0].order):
        failures.append("comparative claim must not be in the hero or first section")
    allowed_text = normalize(text)
    allowed_occurrences = 0
    exclusive_patterns = [re.compile(pattern, re.IGNORECASE) for pattern in contract["exclusivePatterns"]]
    for path in derived_scan_universe():
        for unit in logical_units(path):
            if path == "index.html" and normalize(unit.text) == allowed_text:
                allowed_occurrences += 1
                continue
            for pattern in exclusive_patterns:
                if pattern.search(unit.text):
                    failures.append(f"comparative-evidence explanation outside the configured Insights paragraph at {path}:{unit.start_line}")
                    break
    if allowed_occurrences != 1:
        failures.append(f"configured comparative paragraph must be the sole exact allowed unit, got {allowed_occurrences}")
    if failures:
        fail("; ".join(failures))


def metadata_value(root: Node, key: str) -> str | None:
    value = get_meta(root, "property", key)
    return value if value is not None else get_meta(root, "name", key)


def check_metadata_parity() -> None:
    if not defer_or_require("metadataParity", "check_metadata_parity"):
        return
    parity = CONFIG["metadataParity"]
    failures: list[str] = []
    for page in scoped_paths(CONFIG["pages"]):
        root = parse_dom(page)
        title = get_title(root)
        description = get_meta(root, "name", "description")
        image_values = [metadata_value(root, key) for key in parity["imageKeys"]]
        alt_values = [metadata_value(root, key) for key in parity["imageAltKeys"]]
        canonical_contract = parity["canonicalByPage"][page]
        if title != canonical_contract["title"]:
            failures.append(f"{page} title differs from canonical contract")
        if description != canonical_contract["description"]:
            failures.append(f"{page} description differs from canonical contract")
        if get_link(root, "canonical") != canonical_contract["canonical"]:
            failures.append(f"{page} canonical URL differs from canonical contract")
        if any(metadata_value(root, key) != title for key in parity["titleKeys"]):
            failures.append(f"{page} title metadata differs")
        if any(metadata_value(root, key) != description for key in parity["descriptionKeys"]):
            failures.append(f"{page} description metadata differs")
        if len(set(image_values)) != 1 or image_values[0] is None:
            failures.append(f"{page} image metadata differs")
        if len(set(alt_values)) != 1 or alt_values[0] is None:
            failures.append(f"{page} image-alt metadata differs")
        if image_values[0]:
            local = normalize_local_url(image_values[0], page)
            if not local or not (ROOT / local).exists():
                failures.append(f"{page} social image does not resolve locally")
        expected_url = page_url(page)
        if get_link(root, "canonical") != expected_url or get_meta(root, "property", "og:url") != expected_url:
            failures.append(f"{page} canonical/og:url mismatch")
    if failures:
        fail("; ".join(failures))


def check_metadata_cross_page_parity() -> None:
    if not defer_or_require("metadataCrossPageParity", "check_metadata_cross_page_parity"):
        return
    assert_metadata_cross_page_parity(CONFIG["metadataCrossPageParity"])


def assert_metadata_cross_page_parity(config: dict, parser=parse_dom) -> None:
    values: list[tuple[str, str, str | None]] = []
    for page in config["pages"]:
        root = parser(page)
        values.extend((page, key, metadata_value(root, key)) for key in config["altKeys"])
    unique = {value for _, _, value in values}
    if len(unique) != 1 or None in unique:
        fail(f"Social-card alt metadata must be identical across every page: {values}")


def check_social_card_reference_parity() -> None:
    if not defer_or_require("socialCard", "check_social_card_reference_parity"):
        return
    expected = CONFIG["socialCard"]["version"]
    failures: list[str] = []
    for page in CONFIG["pages"]:
        root = parse_dom(page)
        for key in ("og:image", "twitter:image"):
            value = metadata_value(root, key) or ""
            parsed = urlparse(value)
            if parsed.path != "/social-card.png":
                failures.append(f"{page} {key} path must be /social-card.png, got {parsed.path!r}")
            query = parsed.query
            if query != f"v={expected}":
                failures.append(f"{page} {key} query must be v={expected}, got {query!r}")
    if failures:
        fail("; ".join(failures))


def check_no_structured_data() -> None:
    failures: list[str] = []
    for page in scoped_paths(CONFIG["pages"]):
        scripts = find_nodes(parse_dom(page), tag="script")
        jsonld = [node for node in scripts if node.attrs.get("type", "").casefold() == "application/ld+json"]
        allowed = [node for node in scripts if node.attrs.get("src") == "assets/site.js" and "defer" in node.attrs]
        if jsonld:
            failures.append(f"{page} must contain zero application/ld+json blocks")
        if len(scripts) != 1 or len(allowed) != 1:
            failures.append(f"{page} must contain only the deferred assets/site.js script")
    if failures:
        fail("; ".join(failures))


def check_llms_agreement() -> None:
    if not defer_or_require("llmsAgreement", "check_llms_agreement"):
        return
    config = CONFIG["llmsAgreement"]
    failures = llms_agreement_failures(
        config,
        read("llms.txt"),
        read(config["sitemapPath"]),
        CONFIG["prohibitedPageText"].get("llms.txt", []),
    )
    if failures:
        fail("; ".join(failures))


def llms_agreement_failures(config: dict, text: str, sitemap: str, prohibited_text: list[str]) -> list[str]:
    failures = [f"llms.txt missing verbatim text: {item}" for item in config["mustContainVerbatim"] if item not in text]
    if config["urlsMustAppearInSitemap"]:
        urls = re.findall(r"https://tendijournal\.app/[^)\s:]*", text)
        for url in urls:
            cleaned = url.rstrip(".,")
            if cleaned not in sitemap:
                failures.append(f"llms.txt URL absent from sitemap: {cleaned}")
    for prohibited in prohibited_text:
        if prohibited.casefold() in text.casefold():
            failures.append(f"llms.txt contains prohibited text: {prohibited}")
    return failures


def check_social_card() -> None:
    image = ROOT / "social-card.png"
    if png_dimensions(image) != (1200, 630):
        fail("social-card.png must be 1200x630")
    if not defer_or_require("socialCard", "check_social_card (socialCard assertions)"):
        return
    card = CONFIG["socialCard"]
    failures: list[str] = []
    svg_root = ET.fromstring(read("social-card.svg"))
    if svg_root.attrib.get("width") != str(card["width"]) or svg_root.attrib.get("height") != str(card["height"]):
        failures.append("social-card.svg dimensions differ from socialCard config")
    svg_text = [unit.text for unit in extract_svg_units("social-card.svg")]
    if svg_text != card["expectedSvgText"]:
        failures.append(f"social-card.svg text differs: {svg_text}")
    if png_dimensions(image) != (card["width"], card["height"]):
        failures.append("social-card.png dimensions differ from socialCard config")
    if sha256(image) != card["pngSha256"]:
        failures.append("social-card.png digest differs; re-render and re-pin in the same commit, never edit the digest alone")
    for path, expected_digest in card["inputSha256"].items():
        actual_digest = sha256(ROOT / path)
        if actual_digest != expected_digest:
            failures.append(f"social-card input digest differs for {path}; re-render and re-pin all card inputs in the same commit")
    command = ["node", "-e", "process.stdout.write(require('playwright').chromium.executablePath())"]
    executable = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True).stdout
    match = re.search(r"chromium-(\d+)", executable)
    revision = match.group(1) if match else None
    if revision != card["chromiumRevision"]:
        failures.append(f"Chromium revision {revision!r} differs from pin {card['chromiumRevision']!r}; re-render and re-pin in the same commit")
    if failures:
        fail("; ".join(failures))


def check_prohibited_page_text() -> None:
    sources = {path: read(path) for path in scoped_paths(CONFIG["prohibitedPageText"])}
    failures = prohibited_page_text_failures(CONFIG["prohibitedPageText"], sources)
    disclaimer = "Tendi is a self-reflection and journaling tool. It is not intended to diagnose, treat, cure, or prevent any disease or mental health condition."
    if ACTIVE_PATHS is None or "index.html" in ACTIVE_PATHS:
        if disclaimer not in read("index.html"):
            failures.append("index.html canonical disclaimer is missing")
    if ACTIVE_PATHS is None or "llms.txt" in ACTIVE_PATHS:
        if disclaimer not in read("llms.txt"):
            failures.append("llms.txt canonical disclaimer is missing")
    if failures:
        fail("; ".join(failures))


def prohibited_page_text_failures(contract: dict[str, list[str]], sources: dict[str, str]) -> list[str]:
    failures: list[str] = []
    for path, source in sources.items():
        haystack = source.casefold()
        for prohibited in contract[path]:
            if prohibited.casefold() in haystack:
                failures.append(f"{path} contains prohibited text: {prohibited}")
    return failures


def yaml_publish_rules() -> tuple[list[str], list[str]]:
    includes: list[str] = []
    excludes: list[str] = []
    current: list[str] | None = None
    for line in read("_config.yml").splitlines():
        stripped = line.strip()
        if stripped == "include:":
            current = includes
        elif stripped == "exclude:":
            current = excludes
        elif stripped.startswith("-") and current is not None:
            current.append(stripped[1:].strip().strip('"'))
    return includes, excludes


def path_is_published(path: str, includes: list[str], excludes: list[str]) -> bool:
    parts = Path(path).parts
    hidden = any(part.startswith(".") for part in parts)
    included = any(path == item or path.startswith(f"{item}/") for item in includes)
    if hidden and not included:
        return False
    excluded = any(path == item or path.startswith(f"{item}/") for item in excludes)
    return included or not excluded


def coverage_membership_failure(
    relative: str,
    includes: list[str],
    excludes: list[str],
    universe: set[str],
    exempt: dict[str, dict],
) -> str | None:
    if not relative.endswith((".html", ".txt", ".svg", ".md", ".mdc")):
        return None
    if not path_is_published(relative, includes, excludes):
        return None
    if relative in universe or relative in exempt:
        return None
    return f"Published prose-capable file is outside claim scan universe: {relative}"


def coverage_digest_failure(relative: str, actual_digest: str, record: dict, *, detailed: bool) -> str | None:
    if actual_digest == record["sha256"]:
        return None
    suffix = " (claim content is not hidden)" if detailed else ""
    return f"Coverage exemption digest mismatch for {relative}; re-read and re-pin deliberately{suffix}"


def check_claim_scan_coverage() -> None:
    for key in ("claimScanExtraPaths", "claimScanCoverage"):
        if not defer_or_require(key, "check_claim_scan_coverage"):
            return
    includes, excludes = yaml_publish_rules()
    universe = set(derived_scan_universe())
    coverage = CONFIG["claimScanCoverage"]
    exempt = {item["path"]: item for item in coverage["exempt"]}
    failures: list[str] = []
    for relative in sorted(path.relative_to(ROOT).as_posix() for path in ROOT.rglob("*") if path.is_file()):
        membership_failure = coverage_membership_failure(relative, includes, excludes, universe, exempt)
        if membership_failure:
            failures.append(membership_failure)
            continue
        if relative in exempt and path_is_published(relative, includes, excludes):
            digest_failure = coverage_digest_failure(relative, sha256(ROOT / relative), exempt[relative], detailed=True)
            if digest_failure:
                failures.append(digest_failure)
    for path, record in exempt.items():
        if not (ROOT / path).is_file():
            failures.append(f"Coverage exemption path missing: {path}")
        else:
            digest_failure = coverage_digest_failure(path, sha256(ROOT / path), record, detailed=False)
            if digest_failure:
                failures.append(digest_failure)
    if failures:
        fail("; ".join(failures))


def expect_unit(markup: str, family: str, kind: str, *, count: int = 1) -> list[Unit]:
    units = [unit for unit in extract_html_units("fixture.html", markup) if unit.kind == kind and family_matches(family, unit.text)]
    if len(units) != count:
        fail(f"selftest expected {count} {kind}/{family} units, got {[(u.kind, u.text) for u in units]}")
    return units


def expect_failure(callback, label: str) -> None:
    try:
        callback()
    except (AssertionError, re.error):
        return
    fail(f"selftest expected failure: {label}")


def check_claim_contract_selftest() -> None:
    positives = {
        "P": "Tendi is free forever.", "D1": "Tendi keeps your journal safe.",
        "D2": "Your journal will still be there.", "C": "Mood tracking without streaks.",
        "E": "Export everything as CSV, photos included.", "A1": "science-backed insights",
        "A2": "Tendi helps treat anxiety.", "A3": "Walking improves your mood.",
        "F": "Our moat is non-coercion.", "V": "Shows them with high confidence.",
        "G": "Dates that show up in your patterns.", "I": "Tendi AI reads your entries.",
        "H": "Apple Health: Connected", "M": "Just talk and Tendi does the rest.",
    }
    near_misses = {
        "P": "Tendi 1.0 launches free, with no ads, no subscriptions, and no in-app purchases.",
        "D1": "Tendi is built to keep your journal private.",
        "D2": "Deleting the app removes its local copy but does not necessarily delete data already synced to iCloud.",
        "C": "Tendi is a mood journal for iPhone and Apple Watch.",
        "E": "A Tendi backup is the only export format capable of a complete restore.",
        "A1": "Tendi does not diagnose you or tell you what to do next.",
        "A2": "It is not intended to diagnose, treat, cure, or prevent any disease.",
        "A3": "When a pattern includes a comparison, that comparison is not a cause-and-effect claim.",
        "F": "No single capability is a differentiator.",
        "V": "Tendi shows the counts behind it and the exact dates it covers.",
        "G": "Important Days mark dates that matter to you.", "I": "No ads or journal-content AI training.",
        "H": "Apple Health context is optional.", "M": "A mood alone is enough, and Save is always explicit.",
    }
    for family in sorted(EXPECTED_CLAIM_FAMILIES):
        if not family_matches(family, positives[family]):
            fail(f"selftest family {family} is inert")
        if family != "A3" and family_matches(family, near_misses[family]):
            fail(f"selftest family {family} is over-broad for {near_misses[family]!r}")
    if not family_matches("P", "Tendi is free\u00adforever."):
        fail("selftest family P misses the soft-hyphen form")
    if not family_matches("U", "Tendi compares every pattern across your entries. A comparison is not a cause-and-effect claim."):
        fail("selftest family U is inert")
    if family_matches("U", near_misses["A3"]):
        fail("selftest family U rejects the conditional form")
    if not family_matches("A3", near_misses["A3"]):
        fail("selftest A3 must fire before the required non-causality exemption is applied")
    exemption_fixture = Unit("index.html", "paragraph", near_misses["A3"], 1)
    exemption_record = {
        "path": "index.html", "kind": "paragraph", "unit_text": near_misses["A3"],
        "expected_count": 1, "family": "A3", "justification": "Required non-causality statement",
    }
    if not exemption_matches(exemption_record, exemption_fixture, "A3"):
        fail("selftest A3 exemption identity did not clear the required conditional sentence")
    extended = "Herbarium preserves entries as pressed-specimen memories."
    if not family_matches("D1", extended):
        fail("selftest D1 surface-subject extension is inert")
    unextended = re.compile(r"(?:Tendi|we)\s+(?:(?:will|can|could|may|might|must|should|would)\s+)?(?:keeps?|holds?|preserves?|protects?|safeguards?)\b.{0,40}(?:journal|entries|entry|data|memories)", re.IGNORECASE)
    if unextended.search(extended):
        fail("selftest control: unextended D1 unexpectedly matched Herbarium")
    if family_matches("D1", "Journal keeps them easy to find."):
        fail("selftest D1 surface extension is over-broad for a pronoun object")

    expect_unit("<p>Tendi keeps your <strong>journal</strong> safe.</p>", "D1", "paragraph")
    expect_unit("<p>Your <span>journal <span>will still be there</span></span>.</p>", "D2", "paragraph")
    entity_units = expect_unit("<p>Your journal will still be there &rsquo;later&rsquo; &mdash; &#39;always&#39;.</p>", "D2", "paragraph")
    if "’later’ — 'always'" not in entity_units[0].text:
        fail("selftest HTML entities were not decoded before matching")
    expect_unit("<p>Tendi keeps<!-- comment --> your journal safe.</p>", "D1", "paragraph")
    expect_unit("<p>Tendi\n keeps   your journal\n safe.</p>", "D1", "paragraph")
    expect_unit("<p hidden aria-hidden='true'>Your journal will still be there.</p>", "D2", "paragraph")
    expect_unit("<img alt='Your journal will still be there.'>", "D2", "a11y_attr")
    expect_unit("<input placeholder='Your journal will still be there.'>", "D2", "a11y_attr")
    expect_unit("<span title='Your journal will still be there.'></span>", "D2", "a11y_attr")
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><text><tspan>Mood</tspan> <tspan>tracking</tspan></text></svg>'
    svg_units = extract_svg_units("fixture.svg", svg)
    if len(svg_units) != 1 or not family_matches("C", svg_units[0].text):
        fail("selftest SVG tspan concatenation failed")
    adjacent = extract_html_units("fixture.html", "<p>Tendi keeps your</p><p>journal safe.</p>")
    if len([unit for unit in adjacent if unit.kind == "paragraph"]) != 2 or any(family_matches("D1", unit.text) for unit in adjacent):
        fail("selftest adjacent paragraph boundary failed")
    expect_unit("<div><strong>Tendi keeps your journal safe.</strong></div>", "D1", "block_residual")
    expect_unit("<label>Your journal will still be there.</label>", "D2", "form_text")
    expect_unit("<div><span>Tendi keeps</span> <strong>your journal safe.</strong></div>", "D1", "block_residual")
    nested = expect_unit("<li><p>Tendi keeps your journal safe.</p></li>", "D1", "paragraph")
    if len([unit for unit in nested if unit.kind == "paragraph"]) != 1:
        fail("selftest nested owner emitted duplicate units")
    wrong_count = dict(exemption_record, path="fixture.html", unit_text=nested[0].text, family="D1", expected_count=2)
    if exemption_count_failure(0, wrong_count, observed=1) is None:
        fail("selftest deliberately wrong exemption expected_count did not fail")
    expect_unit("<p>Tendi keeps your <a href='/x'>journal</a> safe.</p>", "D1", "paragraph")
    if any(unit.kind == "link" for unit in extract_html_units("fixture.html", "<p>Tendi keeps your <a href='/x'>journal</a> safe.</p>")):
        fail("selftest nested link stole prose-owner text")
    expect_unit("<nav><a href='/x'>Mood tracking without streaks.</a></nav>", "C", "link")
    expect_unit("<div>Tendi keeps your <a href='/x'>journal</a> safe.</div>", "D1", "block_residual")
    expect_unit("<div>Tendi keeps your <button>journal</button> safe.</div>", "D1", "block_residual")
    expect_unit("<div class='reveal'><p>Unrelated.</p><a href='/x'>Mood tracking without streaks.</a></div>", "C", "link")

    kind_fixtures = {
        "heading": "<h1>Mood tracking</h1>", "paragraph": "<p>Mood tracking</p>",
        "list_item": "<li>Mood tracking</li>", "table_cell": "<td>Mood tracking</td>",
        "link": "<nav><a>Mood tracking</a></nav>", "button": "<div><button>Mood tracking</button></div>",
        "form_text": "<label>Mood tracking</label>", "block_residual": "<div>Mood tracking</div>",
        "inline_orphan": "<span>Mood tracking</span>", "meta": "<meta name='description' content='Mood tracking'>",
        "a11y_attr": "<img alt='Mood tracking'>",
    }
    for kind, markup in kind_fixtures.items():
        expect_unit(markup, "C", kind)
    if not any(unit.kind == "svg_text" and family_matches("C", unit.text) for unit in svg_units):
        fail("selftest did not exercise svg_text")
    markdown = extract_markdown_units("fixture.md", "# Mood tracking\n\n- Mood tracking\n\n| Mood tracking |\n\n```\nMood tracking\n```\n")
    if not {"heading", "list_item", "table_cell", "fenced_block"}.issubset({unit.kind for unit in markdown}):
        fail("selftest Markdown extractor kinds incomplete")
    wrapped_markdown = logical_units("fixture.mdc", "- Tendi keeps your\n  journal safe.\n")
    if len(wrapped_markdown) != 1 or wrapped_markdown[0].kind != "list_item" or not family_matches("D1", wrapped_markdown[0].text):
        fail("selftest wrapped Markdown list or .mdc dispatch split a prohibited claim")

    base = json.loads(json.dumps(CONFIG))
    validate_config_schema(base, strict_presence=True)
    deleted_manifest = json.loads(json.dumps(base)); deleted_manifest.pop("requiredConfigKeys")
    expect_failure(lambda: validate_config_schema(deleted_manifest, strict_presence=False), "deleted requiredConfigKeys")
    delete_both = json.loads(json.dumps(base)); delete_both.pop("claimFamilies"); delete_both["requiredConfigKeys"].remove("claimFamilies")
    expect_failure(lambda: validate_config_schema(delete_both, strict_presence=False), "protected key and manifest name deleted together")
    removed_name = json.loads(json.dumps(base)); removed_name["requiredConfigKeys"].remove("socialCard")
    expect_failure(lambda: validate_config_schema(removed_name, strict_presence=False), "manifest name removed while protected key remains")
    duplicate = json.loads(json.dumps(base)); duplicate["requiredConfigKeys"].append("socialCard")
    expect_failure(lambda: validate_config_schema(duplicate, strict_presence=False), "duplicate manifest entry")
    added = json.loads(json.dumps(base)); added["requiredConfigKeys"].append("unknown")
    expect_failure(lambda: validate_config_schema(added, strict_presence=False), "extra manifest entry")
    missing_default = json.loads(json.dumps(base)); missing_default.pop("metadataParity")
    expect_failure(lambda: validate_config_schema(missing_default, strict_presence=True), "default mode missing metadataParity")
    missing_exemption = json.loads(json.dumps(base)); missing_exemption["claimExemptions"].pop()
    expect_failure(lambda: validate_config_schema(missing_exemption, strict_presence=True), "strict mode missing one reviewed exemption")
    malformed_coverage = json.loads(json.dumps(base)); malformed_coverage["claimScanCoverage"]["exempt"][0].pop("sha256")
    expect_failure(lambda: validate_config_schema(malformed_coverage, strict_presence=False), "missing coverage sha256")
    malformed_coverage = json.loads(json.dumps(base)); malformed_coverage["claimScanCoverage"]["exempt"][0]["sha256"] = "nope"
    expect_failure(lambda: validate_config_schema(malformed_coverage, strict_presence=False), "malformed coverage sha256")

    for label, mutation in [
        ("empty prohibitedPageText", {}),
        ("missing prohibited page", {"index.html": EXPECTED_PROHIBITED_PAGE_TEXT["index.html"]}),
        ("wrong prohibited type", []),
        ("unknown prohibited page", {**EXPECTED_PROHIBITED_PAGE_TEXT, "privacy.html": ["unknown"]}),
    ]:
        malformed = json.loads(json.dumps(base)); malformed["prohibitedPageText"] = mutation
        expect_failure(lambda malformed=malformed: validate_config_schema(malformed, strict_presence=False), label)
    removed_literal = json.loads(json.dumps(base)); removed_literal["prohibitedPageText"]["index.html"].remove("Tendi is free.")
    expect_failure(lambda: validate_config_schema(removed_literal, strict_presence=False), "removed prohibited literal")
    renamed_literal = json.loads(json.dumps(base)); renamed_literal["prohibitedPageText"]["index.html"][-1] = "Tendi is free"
    expect_failure(lambda: validate_config_schema(renamed_literal, strict_presence=False), "renamed prohibited literal")
    extra_literal = json.loads(json.dumps(base)); extra_literal["prohibitedPageText"]["index.html"].append("unexpected")
    expect_failure(lambda: validate_config_schema(extra_literal, strict_presence=False), "unexpected prohibited literal")
    restored_failures = prohibited_page_text_failures(
        base["prohibitedPageText"],
        {"index.html": "The restored copy says: Tendi is free.", "support.html": ""},
    )
    if not any("Tendi is free." in failure for failure in restored_failures):
        fail("selftest prohibitedPageText did not catch a restored banned sentence")

    metadata_mutations: list[tuple[str, dict]] = []
    missing_canonical = json.loads(json.dumps(base)); missing_canonical["metadataParity"].pop("canonicalByPage")
    metadata_mutations.append(("missing canonical metadata", missing_canonical))
    malformed_canonical = json.loads(json.dumps(base)); malformed_canonical["metadataParity"]["canonicalByPage"] = []
    metadata_mutations.append(("malformed canonical metadata", malformed_canonical))
    incomplete_canonical = json.loads(json.dumps(base)); incomplete_canonical["metadataParity"]["canonicalByPage"].pop("support.html")
    metadata_mutations.append(("incomplete canonical metadata", incomplete_canonical))
    reordered_description = json.loads(json.dumps(base)); reordered_description["metadataParity"]["canonicalByPage"]["index.html"]["description"] = "Your entries build a record. Check in with one mood. Tendi is honest about what that record can actually show. No streaks. No account required."
    metadata_mutations.append(("reordered homepage description", reordered_description))
    unknown_metadata = json.loads(json.dumps(base)); unknown_metadata["metadataParity"]["canonicalByPage"]["index.html"]["unknown"] = True
    metadata_mutations.append(("unknown canonical metadata field", unknown_metadata))
    weakened_parity = json.loads(json.dumps(base)); weakened_parity["metadataParity"]["titleKeys"] = ["og:title"]
    metadata_mutations.append(("weakened metadata parity key list", weakened_parity))
    capture_only = json.loads(json.dumps(base))
    for metadata in capture_only["metadataParity"]["canonicalByPage"].values():
        metadata["description"] = "Check in with one mood."
    metadata_mutations.append(("config and three page descriptions weakened to capture-only", capture_only))
    for label, malformed in metadata_mutations:
        expect_failure(lambda malformed=malformed: validate_config_schema(malformed, strict_presence=False), label)

    reviewed_support = support_contract_failures(parse_dom("index.html"), base["promiseContract"]["index.html"]["supportContracts"])
    if reviewed_support:
        fail(f"selftest reviewed selector support contract failed: {reviewed_support}")
    index_source = read("index.html")
    support_mutations = {
        "deleted record support": index_source.replace('id="record-support"', "", 1),
        "weakened B1 answer": index_source.replace(
            "No. There are no streaks, no catching up, and no deficit counters. Days without entries stay just that, not failures.",
            "No. Check in whenever you want.",
            1,
        ),
        "generic G3 answer": index_source.replace(
            "Yes. A full Tendi backup is designed for restoring your journal into Tendi. JSON and CSV carry entries for use elsewhere, and PDF is a readable report rather than a backup. Export and restore need no payment and no account.",
            "Yes. You can export your journal.",
            1,
        ),
        "ambiguous duplicate B1 selector": index_source.replace(
            '<p id="daily-faq-answer">',
            '<p id="daily-faq-answer"></p><p id="daily-faq-answer">',
            1,
        ),
    }
    for label, mutated_source in support_mutations.items():
        if not support_contract_failures(
            parse_dom_text(mutated_source),
            base["promiseContract"]["index.html"]["supportContracts"],
        ):
            fail(f"selftest {label} did not fail the selector support contract")
    valid_cross = {"altKeys": ["og:image:alt", "twitter:image:alt"], "pages": CONFIG["pages"]}
    validate_metadata_cross_page_parity(valid_cross)
    for bad in [
        {"pages": CONFIG["pages"]},
        {"altKeys": [], "pages": CONFIG["pages"]},
        {"altKeys": "og:image:alt", "pages": CONFIG["pages"]},
        {"altKeys": ["og:image:alt"], "pages": CONFIG["pages"][:-1]},
        {"altKeys": ["og:image:alt"], "pages": CONFIG["pages"], "scope": "subset"},
    ]:
        expect_failure(lambda bad=bad: validate_metadata_cross_page_parity(bad), "malformed metadataCrossPageParity")
    valid_llms = {"mustContainVerbatim": ["promise"], "urlsMustAppearInSitemap": True, "sitemapPath": "sitemap.xml"}
    validate_llms_agreement(valid_llms)
    for bad in [
        {"urlsMustAppearInSitemap": True, "sitemapPath": "sitemap.xml"},
        {"mustContainVerbatim": [], "urlsMustAppearInSitemap": True, "sitemapPath": "sitemap.xml"},
        {"mustContainVerbatim": ["promise"], "urlsMustAppearInSitemap": False, "sitemapPath": "sitemap.xml"},
        {"mustContainVerbatim": ["promise"], "urlsMustAppearInSitemap": True, "sitemapPath": "missing.xml"},
        {"mustContainVerbatim": ["promise"], "urlsMustAppearInSitemap": True, "sitemapPath": "sitemap.xml", "unknown": True},
    ]:
        expect_failure(lambda bad=bad: validate_llms_agreement(bad), "malformed llmsAgreement")
    malformed_card = {"width": "1200", "height": 630, "version": "x", "pngSha256": "0" * 64, "chromiumRevision": "1228", "browserVersion": "x", "expectedSvgText": ["x"], "inputSha256": {path: "0" * 64 for path in SOCIAL_CARD_INPUT_PATHS}}
    expect_failure(lambda: validate_social_card(malformed_card), "non-integer social-card width")
    malformed_card_input = json.loads(json.dumps(base["socialCard"])); malformed_card_input["inputSha256"].pop(SOCIAL_CARD_INPUT_PATHS[0])
    expect_failure(lambda: validate_social_card(malformed_card_input), "missing social-card input digest")
    malformed_card_input = json.loads(json.dumps(base["socialCard"])); malformed_card_input["inputSha256"][SOCIAL_CARD_INPUT_PATHS[0]] = "nope"
    expect_failure(lambda: validate_social_card(malformed_card_input), "malformed social-card input digest")
    for strict_presence in (False, True):
        malformed_config = json.loads(json.dumps(base)); malformed_config["socialCard"]["width"] = "1200"
        expect_failure(
            lambda malformed_config=malformed_config, strict_presence=strict_presence: validate_config_schema(malformed_config, strict_presence=strict_presence),
            f"malformed socialCard in strict_presence={strict_presence}",
        )

    exact_deferrals = sorted(EXPECTED_DEFERRED)
    validate_deferred_summary(True, exact_deferrals)
    expect_failure(lambda: validate_deferred_summary(True, [*exact_deferrals, "sixth"]), "sixth staging deferral")
    expect_failure(lambda: validate_deferred_summary(False, ["check_metadata_parity"]), "strict run with a deferred check")

    expect_failure(
        lambda: resolve_invocation(None, "privacy.html", False),
        "path scoping without --only",
    )
    expect_failure(
        lambda: resolve_invocation("check_prohibited_claims", "outside-scan.txt", False),
        "path outside the scan universe",
    )
    global_selected, global_paths = resolve_invocation(
        "check_metadata_cross_page_parity,check_llms_agreement",
        "privacy.html,support.html",
        False,
    )
    if global_paths != frozenset({"privacy.html", "support.html"}) or any(name in PATH_SCOPED for name in global_selected):
        fail("selftest global checks were incorrectly classified as path-scoped")

    shared_alt = "shared alt"
    synthetic_pages = {
        "index.html": parse_dom_text(f'<meta property="og:image:alt" content="different"><meta name="twitter:image:alt" content="{shared_alt}">'),
        "privacy.html": parse_dom_text(f'<meta property="og:image:alt" content="{shared_alt}"><meta name="twitter:image:alt" content="{shared_alt}">'),
        "support.html": parse_dom_text(f'<meta property="og:image:alt" content="{shared_alt}"><meta name="twitter:image:alt" content="{shared_alt}">'),
    }
    expect_failure(
        lambda: assert_metadata_cross_page_parity(valid_cross, parser=lambda page: synthetic_pages[page]),
        "global metadata parity must still inspect index.html",
    )
    missing_sitemap_url = llms_agreement_failures(
        {"mustContainVerbatim": ["promise"], "urlsMustAppearInSitemap": True, "sitemapPath": "sitemap.xml"},
        "promise [missing](https://tendijournal.app/missing.html)",
        "<loc>https://tendijournal.app/</loc>",
        [],
    )
    if not any("absent from sitemap" in failure for failure in missing_sitemap_url):
        fail("selftest llms agreement ignored its sitemap dependency")

    includes, excludes = yaml_publish_rules()
    if coverage_membership_failure("docs-fixture/page.html", includes, excludes, set(), {}) is None:
        fail("selftest nested published page escaped claim scan coverage")
    if coverage_membership_failure("future.md", includes, excludes, set(), {}) is None:
        fail("selftest published Markdown escaped claim scan coverage")
    if coverage_membership_failure("tools/page.html", includes, excludes, set(), {}) is not None:
        fail("selftest nested excluded page was treated as published")
    comparative_mutation = Unit("llms.txt", "paragraph", "Tendi explains comparative patterns with their counts and exact dates.", 1)
    if not any(re.search(pattern, comparative_mutation.text, re.IGNORECASE) for pattern in CONFIG["comparativeClaim"]["index.html"]["exclusivePatterns"]):
        fail("selftest C1 exclusivity patterns missed comparative evidence outside the homepage")
    ofl_record = CONFIG["claimScanCoverage"]["exempt"][0]
    ofl_bytes = (ROOT / ofl_record["path"]).read_bytes()
    if hashlib.sha256(ofl_bytes).hexdigest() != ofl_record["sha256"]:
        fail("selftest OFL coverage pin is stale")
    mismatched_record = dict(ofl_record, sha256="0" * 64)
    mismatch_failure = coverage_digest_failure(
        ofl_record["path"], hashlib.sha256(ofl_bytes).hexdigest(), mismatched_record, detailed=True,
    )
    if mismatch_failure is None or "re-read and re-pin deliberately" not in mismatch_failure:
        fail("selftest mismatched coverage digest did not fail closed")
    appended_claim = "Your journal will still be there."
    appended_digest = hashlib.sha256(ofl_bytes + f"\n{appended_claim}\n".encode()).hexdigest()
    if appended_digest == ofl_record["sha256"] or not family_matches("D2", appended_claim):
        fail("selftest appended coverage claim did not invalidate the pinned exemption")

    banned = banned_public_term_failures({"social-card.svg": "<text>Mood tracking</text>"}, ["mood tracking"])
    if not banned or "social-card.svg" not in banned[0]:
        fail("selftest migrated banned-term scan missed social-card.svg")


CHECKS = {
    "check_required_files": check_required_files,
    "check_pages_publish_config": check_pages_publish_config,
    "check_html_pages": check_html_pages,
    "check_forms": check_forms,
    "check_mailto_placeholders": check_mailto_placeholders,
    "check_sitemap_and_robots": check_sitemap_and_robots,
    "check_social_card": check_social_card,
    "check_security_txt": check_security_txt,
    "check_banned_public_terms": check_banned_public_terms,
    "check_disallowed_script_markers": check_disallowed_script_markers,
    "check_prohibited_claims": check_prohibited_claims,
    "check_promise_contract": check_promise_contract,
    "check_comparative_claim": check_comparative_claim,
    "check_metadata_parity": check_metadata_parity,
    "check_metadata_cross_page_parity": check_metadata_cross_page_parity,
    "check_social_card_reference_parity": check_social_card_reference_parity,
    "check_no_structured_data": check_no_structured_data,
    "check_llms_agreement": check_llms_agreement,
    "check_prohibited_page_text": check_prohibited_page_text,
    "check_claim_scan_coverage": check_claim_scan_coverage,
    "check_claim_contract_selftest": check_claim_contract_selftest,
}

PATH_SCOPED = frozenset({
    "check_prohibited_claims", "check_prohibited_page_text", "check_html_pages",
    "check_metadata_parity", "check_no_structured_data",
})


def resolve_invocation(
    only: str | None,
    paths_argument: str | None,
    allow_deferred: bool,
) -> tuple[list[str], frozenset[str] | None]:
    if allow_deferred and (only or paths_argument):
        fail("--allow-deferred is the Step-3a whole-verifier mode and cannot be combined with scoping flags")
    if paths_argument and not only:
        fail("--paths requires --only; a strict whole-verifier run cannot be path-scoped")
    selected = list(CHECKS)
    if only:
        selected = [name.strip() for name in only.split(",") if name.strip()]
        unknown = sorted(set(selected) - set(CHECKS))
        if unknown:
            fail(f"Unknown --only check names: {unknown}")
        if not selected:
            fail("--only must name at least one check")
        if len(selected) != len(set(selected)):
            fail("--only must not name the same check more than once")
    active_paths: frozenset[str] | None = None
    if paths_argument:
        paths = [path.strip() for path in paths_argument.split(",") if path.strip()]
        if not paths:
            fail("--paths must name at least one file")
        if len(paths) != len(set(paths)):
            fail("--paths must not name the same file more than once")
        universe = set(derived_scan_universe())
        outside = sorted(set(paths) - universe)
        if outside:
            fail(f"--paths names files outside the derived scan universe: {outside}")
        active_paths = frozenset(paths)
    return selected, active_paths


def validate_deferred_summary(allow_deferred: bool, deferred: list[str]) -> None:
    if allow_deferred:
        if set(deferred) != EXPECTED_DEFERRED or len(deferred) != 5:
            fail(f"Step-3a staging requires exactly five known deferrals, got {deferred}")
    elif deferred:
        fail(f"Strict verifier forbids deferred checks: {deferred}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--allow-deferred", action="store_true")
    parser.add_argument("--only", help="comma-separated registered check names")
    parser.add_argument("--paths", help="comma-separated paths for path-scoped checks")
    return parser.parse_args()


def main() -> int:
    global ACTIVE_PATHS, ALLOW_DEFERRED, WHOLE_STRICT_RUN
    args = parse_args()
    if args.selftest:
        if args.allow_deferred or args.only or args.paths:
            fail("--selftest cannot be combined with other flags")
        validate_config_schema(CONFIG, strict_presence=False)
        check_claim_contract_selftest()
        print("Claim contract self-test passed: 14 configured regex families plus U, 12 ledger extractor kinds plus fenced blocks, all 20 extractor regressions, and targeted mutations for banned literals, canonical metadata, and selector-anchored support copy.")
        return 0
    selected, ACTIVE_PATHS = resolve_invocation(args.only, args.paths, args.allow_deferred)
    ALLOW_DEFERRED = args.allow_deferred
    WHOLE_STRICT_RUN = not args.only and not args.allow_deferred
    validate_config_schema(CONFIG, strict_presence=WHOLE_STRICT_RUN)
    failures: list[str] = []
    for name in selected:
        if ACTIVE_PATHS is not None and name not in PATH_SCOPED:
            print(f"scope ignored for {name} (global)")
        try:
            CHECKS[name]()
        except (AssertionError, KeyError, ValueError, ET.ParseError, subprocess.CalledProcessError) as error:
            failures.append(f"{name}: {error}")
    if ALLOW_DEFERRED:
        try:
            validate_deferred_summary(True, DEFERRED)
        except AssertionError as error:
            failures.append(str(error))
        print(f"deferred summary: {len(DEFERRED)} ({', '.join(DEFERRED)})")
    else:
        try:
            validate_deferred_summary(False, DEFERRED)
        except AssertionError as error:
            failures.append(str(error))
    if failures:
        print("Static verification failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print(f"Static verification passed for {CONFIG['siteName']}; deferred=0.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError, ValueError, ET.ParseError, re.error) as error:
        print(f"Static verification failed: {error}", file=sys.stderr)
        raise SystemExit(1)

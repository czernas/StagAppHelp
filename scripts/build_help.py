#!/usr/bin/env python3
"""Builds the app's Help JSON from the Markdown topics.

    python3 scripts/build_help.py            # writes build/help_cs.json and build/help_en.json
    python3 scripts/build_help.py --check    # only validates

Every topic is one file per language, `topics/<lang>/<name>.md`, the same <name> in each language. The file starts
with front matter, then the topic's sections:

    ---
    category: Začínáme        # the category's title in this language
    categoryOrder: 2          # where the category sits (the same in both languages)
    order: 1                  # where the topic sits in its category
    title: Jak aplikace pracuje s daty?
    icon: lock.shield         # an SF Symbol, or an image in this repository ("icons/timetable.png")
    ---

    ## Offline režim                 an open section with this heading
    Text. **Bold**, _italic_, [a link](https://…).

    ### Studujete na TUL?            a section folded under this heading until tapped
    - a list item
    - another

    ![Alt text](webview_login_tul.png)   an image from this repository

Text before the first heading is a section without a heading. Plain paragraphs become `text` items, anything with
formatting, a list or a line break becomes an `html` item; paragraphs in a row are one item. The JSON is the format
the app already reads (`HelpCategory.Categories`), so the app doesn't care where it came from.

The build fails (and the workflow publishes nothing) on a malformed topic, an image that isn't in the repository,
a topic missing in one of the languages, or categories whose order differs between the languages.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOPICS = ROOT / "topics"
BUILD = ROOT / "build"
LANGUAGES = ["cs", "en"]
REQUIRED = ["category", "categoryOrder", "order", "title"]


class TopicError(Exception):
    pass


def parse_front_matter(text, path):
    if not text.startswith("---\n"):
        raise TopicError(f"{path}: must start with front matter (---)")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise TopicError(f"{path}: front matter isn't closed with ---")
    meta = {}
    for number, line in enumerate(text[4:end].splitlines(), start=2):
        line = line.split(" #", 1)[0].rstrip()
        if not line.strip():
            continue
        if ":" not in line:
            raise TopicError(f"{path}:{number}: expected 'key: value', got {line!r}")
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip()
    for key in REQUIRED:
        if not meta.get(key):
            raise TopicError(f"{path}: front matter misses '{key}'")
    for key in ("categoryOrder", "order"):
        if not meta[key].isdigit():
            raise TopicError(f"{path}: '{key}' must be a whole number, got {meta[key]!r}")
        meta[key] = int(meta[key])
    return meta, text[end + 5:]


INLINE = [
    (re.compile(r"\*\*(.+?)\*\*"), r"<b>\1</b>"),
    (re.compile(r"(?<![\w*])_(.+?)_(?![\w*])"), r"<i>\1</i>"),
    (re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)"), r'<a href="\2">\1</a>'),
]


def inline_html(line):
    """Markdown's inline marks as the HTML the app's Help has always shown. HTML written in the Markdown stays as is"""
    for pattern, replacement in INLINE:
        line = pattern.sub(replacement, line)
    return line


def is_plain(lines):
    return all(inline_html(line) == line and not line.startswith("- ") and "<" not in line for line in lines)


def block_item(paragraphs):
    """Paragraphs in a row, as one item: plain text when it's one unformatted paragraph, HTML otherwise"""
    if len(paragraphs) == 1 and len(paragraphs[0]) == 1 and is_plain(paragraphs[0]):
        return {"kind": "text", "value": paragraphs[0][0]}
    rendered = []
    for paragraph in paragraphs:
        lines = [("• " + inline_html(line[2:])) if line.startswith("- ") else inline_html(line) for line in paragraph]
        rendered.append("<br>".join(lines))
    return {"kind": "html", "value": "<br><br>".join(rendered)}


IMAGE = re.compile(r"^!\[([^\]]*)\]\(([^)\s]+)\)$")


def parse_sections(body, path):
    sections = []
    items = None
    paragraphs = []
    paragraph = []

    def end_paragraph():
        nonlocal paragraph
        if paragraph:
            paragraphs.append(paragraph)
            paragraph = []

    def end_block():
        nonlocal paragraphs
        end_paragraph()
        if paragraphs:
            current().append(block_item(paragraphs))
            paragraphs = []

    def current():
        nonlocal items
        if items is None:
            items = []
            sections.append({"items": items})
        return items

    def new_section(kind, title):
        nonlocal items
        end_block()
        items = []
        sections.append({"items": items})
        items.append({"kind": kind, "value": title})

    for number, raw in enumerate(body.splitlines(), start=1):
        line = raw.rstrip()
        if line.startswith("### "):
            new_section("expandable", line[4:].strip())
        elif line.startswith("## "):
            new_section("expanded", line[3:].strip())
        elif line.startswith("#"):
            raise TopicError(f"{path}: line {number}: only ## (open) and ### (folded) headings are used")
        elif IMAGE.match(line):
            end_block()
            alt, file = IMAGE.match(line).groups()
            image = {"kind": "imageUrl", "value": file}
            if alt:
                image["title"] = alt
            current().append(image)
        elif not line.strip():
            end_paragraph()
        else:
            paragraph.append(line.strip())
    end_block()
    return [section for section in sections if section["items"]]


def load_topic(path):
    meta, body = parse_front_matter(path.read_text(encoding="utf-8"), path)
    sections = parse_sections(body, path)
    if not sections:
        raise TopicError(f"{path}: has no content")
    for section in sections:
        for item in section["items"]:
            if item["kind"] == "imageUrl" and not (ROOT / item["value"]).is_file():
                raise TopicError(f"{path}: image {item['value']!r} isn't in the repository")
    icon = meta.get("icon")
    if icon and "/" in icon and not (ROOT / icon).is_file():
        raise TopicError(f"{path}: icon {icon!r} isn't in the repository")
    topic = {"title": meta["title"], "sections": sections}
    if icon:
        topic["imageName"] = icon
    return meta, topic


def build(language):
    folder = TOPICS / language
    files = sorted(folder.glob("*.md"))
    if not files:
        raise TopicError(f"{folder}: no topics")
    categories = {}
    for path in files:
        meta, topic = load_topic(path)
        key = meta["categoryOrder"]
        category = categories.setdefault(key, {"title": meta["category"], "topics": []})
        if category["title"] != meta["category"]:
            raise TopicError(f"{path}: categoryOrder {key} is '{category['title']}' elsewhere, here '{meta['category']}'")
        category["topics"].append((meta["order"], path.stem, topic))
    result = []
    for key in sorted(categories):
        category = categories[key]
        orders = [order for order, _, _ in category["topics"]]
        if len(orders) != len(set(orders)):
            raise TopicError(f"{language}: two topics share an 'order' in '{category['title']}'")
        result.append({"title": category["title"],
                       "topics": [topic for _, _, topic in sorted(category["topics"])]})
    return {"categories": result}, {path.stem: load_topic(path)[0] for path in files}


def main():
    check_only = "--check" in sys.argv
    errors = []
    built = {}
    metas = {}
    for language in LANGUAGES:
        try:
            built[language], metas[language] = build(language)
        except TopicError as error:
            errors.append(str(error))
    if len(metas) == len(LANGUAGES):
        names = [set(metas[language]) for language in LANGUAGES]
        for language, own in zip(LANGUAGES, names):
            for other_language, other in zip(LANGUAGES, names):
                for missing in sorted(other - own):
                    errors.append(f"topics/{language}/{missing}.md is missing ({other_language} has it)")
        for name in set.intersection(*names):
            places = {(metas[language][name]["categoryOrder"], metas[language][name]["order"]) for language in LANGUAGES}
            if len(places) > 1:
                errors.append(f"{name}: categoryOrder/order differ between the languages")
    if errors:
        print("Help is not valid:", *errors, sep="\n  ", file=sys.stderr)
        sys.exit(1)
    if check_only:
        print("Help is valid.")
        return
    BUILD.mkdir(exist_ok=True)
    for language, data in built.items():
        target = BUILD / f"help_{language}.json"
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        topics = sum(len(category["topics"]) for category in data["categories"])
        print(f"{target.relative_to(ROOT)}: {len(data['categories'])} categories, {topics} topics")


if __name__ == "__main__":
    main()

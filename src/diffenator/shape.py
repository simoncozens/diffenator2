"""
Check fonts for shaping regressions using real words.
"""
from __future__ import annotations
import argparse
import unicodedata2 as uni
from dataclasses import dataclass
from lxml import etree
from lxml import objectify
from collections import defaultdict
import uharfbuzz as hb
import os
from diffenator.ft_hb_shape import render_text
from pkg_resources import resource_filename
import ahocorasick
from jinja2 import pass_environment
from threading import Thread


# functions to build word lists


def build_words(fps: list[str], out: str, keep_chars: set[str]=None):
    keep_chars |= set("'")  # used for quoting obscure words in wikipedia
    bank = set()
    word_freq = defaultdict(int)
    seen_keep_chars = set()
    for fp in fps:
        with open(fp) as doc:
            # This is memory effecient. We do not want to use doc.read()
            # since this will try and load the whole file into memory
            for line in doc:
                words = line.split()
                for word in words:
                    word = word.replace("'", "")  # remove the quote marks
                    if keep_chars and all(c in keep_chars for c in word):
                        word_freq[word] += 1
                        seen_keep_chars |= set(word)
                        bank.add(word)

    unseen_keep_chars = keep_chars - seen_keep_chars
    unseen_count = len(unseen_keep_chars)
    print(
        f"Following {unseen_count}/{len(keep_chars)} characters not seen in any words {unseen_keep_chars}."
    )

    words = remove_substring_words(bank)
    res = set()
    for word in words:
        # if word_freq[word] <= 2:
        #    continue
        res.add(word)
    with open(out, "w", encoding="utf8") as doc:
        doc.write("\n".join(res))


def remove_substring_words(words:set[str]) -> set[str]:
    res = set()
    auto = ahocorasick.Automaton()
    for word in words:
        auto.add_word(word, word)
        res.add(word)
    auto.make_automaton()

    for word in words:
        for end_ind, found in auto.iter(word):
            if word != found:
                try:
                    res.remove(found)
                except:
                    all
    return res


# Functions to test word lists


ot_to_html_lang = {
    (None, None): "en",
    ("latn", "dflt"): "en",
    ("arab", "ARA"): "ar",
    ("dev2", "HIN"): "hi",
    ("dev2", "MAR"): "mr",
    ("dev2", "NEP"): "ne",
    ("latn", "MOL"): "mo",
    ("cyrl", "SRB"): "sr",
}

ot_to_dir = {None: "ltr", "arab": "rlt", "hebr": "rtl"}


class Renderable:
    @pass_environment
    def render(self, jinja):
        classname = self.__class__.__name__
        template = jinja.get_template(f"{classname}.partial.html")
        return template.render(self.__dict__)


@dataclass
class Word:
    string: str
    hb: str

    def __eq__(self, other):
        return (self.string, hb) == (other.string, other.hb)

    def __hash__(self):
        return hash((self.string, self.hb))


@dataclass
class WordDiff(Renderable):
    string: str
    hb_a: str
    hb_b: str
    ot_features: tuple
    lang: str
    direction: str
    diff_map: list[int]

    def __hash__(self):
        return hash((self.string, self.hb_a, self.hb_b, self.ot_features))


@dataclass
class Glyph(Renderable):
    string: str
    name: str
    unicode: str

    def __hash__(self):
        return hash((self.string, self.name, self.unicode))


@dataclass
class GlyphDiff(Renderable):
    string: str
    name: str
    unicode: str
    changed_pixels: float
    diff_map: list[int]

    def __hash__(self):
        return hash((self.string, self.name, self.unicode))


@dataclass
class GlyphItems:
    missing: list
    new: list
    modified: list


def gid_pos_hash(info, pos):
    return f"gid={info.codepoint}, pos={pos.position}<br>"


def gid_hash(info, _):
    return f"gid={info.codepoint}<br>"


def test_fonts(font_a, font_b):
    glyphs = test_font_glyphs(font_a, font_b)
    skip_glyphs = glyphs.missing + glyphs.new
    words = test_font_words(font_a, font_b, skip_glyphs)
    return {"glyphs": glyphs, "words": words}


def test_font_glyphs(font_a, font_b):
    cmap_a = set(chr(c) for c in font_a.ttFont.getBestCmap())
    cmap_b = set(chr(c) for c in font_b.ttFont.getBestCmap())
    missing_glyphs = set(Glyph(c, uni.name(c), ord(c)) for c in cmap_a - cmap_b)
    new_glyphs = set(Glyph(c, uni.name(c), ord(c)) for c in cmap_b - cmap_a)
    same_glyphs = cmap_a & cmap_b
    skip_glyphs = missing_glyphs | new_glyphs
    modified_glyphs = []
    for g in same_glyphs:
        pc, diff_map = px_diff(font_a, font_b, g)
        if pc > 0.000002:
            try:
                uni_name = uni.name(g)
            except ValueError:
                uni_name = ""
            glyph = GlyphDiff(g, uni_name, ord(g), pc, diff_map)
            modified_glyphs.append(glyph)
    modified_glyphs.sort(key=lambda k: k.changed_pixels, reverse=True)

    return GlyphItems(
        list(sorted(missing_glyphs, key=lambda k: k.string)),
        list(sorted(new_glyphs, key=lambda k: k.string)),
        modified_glyphs,
    )


def test_font_words(font_a, font_b, skip_glyphs=set()):
    from youseedee import ucd_data
    from collections import defaultdict

    scripts = defaultdict(int)
    cmap_a = font_a.ttFont.getBestCmap()
    for k in cmap_a:
        data = ucd_data(k)
        try:
            scripts[data["Script"]] += 1
        except:
            continue

    res = {}
    for script, count in scripts.items():
        if count < 10:
            continue
        wordlist = resource_filename("diffenator", f"data/wordlists/{script}.txt")
        if not os.path.exists(wordlist):
            print(f"No wordlist for {script}")
            continue
        res[script] = test_words(wordlist, font_a, font_b, skip_glyphs)
    return res


def test_words(
    word_file,
    font_a,
    font_b,
    skip_glyphs=set(),
    hash_func=gid_pos_hash,
    threshold=0.000002,
):
    res = set()
    from collections import defaultdict

    seen_gids = defaultdict(int)
    with open(word_file, encoding="utf8") as doc:
        words = doc.read().split("\n")
        print(f"testing {len(words)} words")
        word_total = len(words)
        for i, line in enumerate(words):
            skip_px = False
            items = line.split(",")
            try:
                word, script, lang, features = items[0], items[1], items[2], items[3:]
            # for wordlists which just contain words
            except IndexError:
                word, script, lang, features = items[0], None, None, []
            features = {k: True for k in features}
            if any(c.string in word for c in skip_glyphs):
                continue

            buf_a = hb.Buffer()
            if script:
                buf_a.script = script
            if lang:
                buf_a.language = lang
            buf_a.add_str(word)
            buf_a.guess_segment_properties()
            hb.shape(font_a.hbFont, buf_a, features=features)

            infos_a = buf_a.glyph_infos
            pos_a = buf_a.glyph_positions
            hb_a = "".join(hash_func(i, j) for i, j in zip(infos_a, pos_a))
            word_a = Word(word, hb_a)

            buf_b = hb.Buffer()
            if script:
                buf_b.script = script
            if lang:
                buf_b.language = lang
            buf_b.add_str(word)
            buf_b.guess_segment_properties()
            hb.shape(font_b.hbFont, buf_b, features=features)

            infos_b = buf_b.glyph_infos
            pos_b = buf_b.glyph_positions
            hb_b = "".join(hash_func(i, j) for i, j in zip(infos_b, pos_b))
            word_b = Word(word, hb_b)

            if all(seen_gids[hash_func(i, j)] >= 1 for i, j in zip(infos_b, pos_b)):
                continue
            pc, diff_map = px_diff(
                font_a, font_b, word, script=script, lang=lang, features=features
            )
            if pc >= threshold:
                for i, j in zip(infos_b, pos_b):
                    h = hash_func(i, j)
                    seen_gids[h] += 1
                res.add(
                    (
                        pc,
                        WordDiff(
                            word,
                            word_a.hb,
                            word_b.hb,
                            tuple(features.keys()),
                            ot_to_html_lang.get((script, lang)),
                            ot_to_dir.get(script, None),
                            diff_map,
                        ),
                    )
                )
    return [w[1] for w in sorted(res, key=lambda k: k[0], reverse=True)]


def px_diff(font_a, font_b, string, script=None, lang=None, features=None):
    pc = 0.0
    if hasattr(font_a, "variations"):
        variations_a = font_a.variations
    else:
        variations_a = None

    if hasattr(font_b, "variations"):
        variations_b = font_b.variations
    else:
        variations_b = None
    img_a = render_text(
        font_a,
        string,
        fontSize=3,  # 3pt really is enough!
        margin=0,
        features=features,
        script=script,
        lang=lang,
        variations=variations_a,
    )
    img_b = render_text(
        font_b,
        string,
        fontSize=3,
        margin=0,
        features=features,
        script=script,
        lang=lang,
        variations=variations_b,
    )
    width = min([img_a.width, img_b.width])
    height = min([img_a.height, img_b.height])
    diff_pixels = 0
    diff_map = []
    for x in range(width):
        for y in range(height):
            px_a = img_a.getpixel((x, y))
            px_b = img_b.getpixel((x, y))
            if px_a != px_b:
                if isinstance(px_a, int) and isinstance(px_b, int):
                    diff_pixel = abs(px_a - px_b)
                    diff_pixels += diff_pixel
                    diff_map.append(diff_pixel)
                else:
                    diff_pixel += abs(px_a[0] - px_b[0])
                    diff_pixel += abs(px_a[1] - px_b[1])
                    diff_pixel += abs(px_a[2] - px_b[2])
                    diff_pixel += abs(px_a[3] - px_b[3])
                    diff_pixels += diff_pixel
                    diff_map.append(diff_pixel)
            else:
                diff_map.append(0)
    try:
        pc = diff_pixels / (width * height * 256 * 4)
    except ZeroDivisionError:
        pc = 0
    return pc, diff_map


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(required=True, dest="cmd")

    build = subparsers.add_parser("build")
    build.add_argument(
        "input_files", nargs="+", help="Text files to extract words from"
    )
    build.add_argument("--glyphs", "-g", required=True)
    build.add_argument("-o", "--out", default="out.txt")

    args = parser.parse_args()

    if args.cmd == "build":
        glyphs = None if not args.glyphs else set(args.glyphs)
        build_words(args.input_files, args.out, glyphs)
    else:
        raise ValueError(f"{args.cmd} unsupported command")


if __name__ == "__main__":
    main()

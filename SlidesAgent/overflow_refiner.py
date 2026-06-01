"""Readability-aware overflow handling for SlideGen v2.

Runs *after* the arranger produces a slide plan and *before* the filler builds
the deck. For every content slide it estimates whether the title or body text
will overflow its placeholder (using a character-capacity heuristic, since
python-pptx does not render), and applies a three-step fallback ladder that
mirrors the design trade-off described in the paper:

  (i)   text compression   — shorten long titles / verbose bullets (one LLM call)
  (ii)  bounded font scaling — shrink fonts within a readability-preserving range
  (iii) template switch / split — move to a more text-friendly layout, or split
        the content onto an additional supporting slide.

The plan JSON is rewritten in place: overflowing slides may gain a per-slide
``"font"`` block, a changed ``"template_id"``, or be followed by a new
continuation slide. The filler honors all of these.

Everything is best-effort: any failure logs a warning and leaves the slide
unchanged, so the refiner can never break generation.
"""

from __future__ import annotations

import json
import math
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from SlidesAgent.template_introspect import (
    LayoutSpec,
    TemplateSpec,
    resolve_template_path,
    scan_template,
)

EMU_PER_INCH = 914400

# Readability floors (pt) — never scale below these.
FONT_FLOOR = {"title": 20, "body": 16, "sub": 14}
# Default starting sizes (match layout_filler.DEFAULT_FONTS).
FONT_START = {"title": 28, "body": 24, "sub": 24}
FONT_STEP = 2

# Overflow safety margin: a box is "overflowing" when the text needs more than
# this fraction of the available lines. Slightly above 1.0 tolerates a box that
# is exactly full (floor() on available lines is already conservative).
OVERFLOW_RATIO = 1.0

# Heuristic glyph metrics (shared with utils/wei_utils estimators).
AVG_CHAR_RATIO = 0.5      # avg char width / font size
LINE_SPACING_RATIO = 1.2  # line height / font size


# --------------------------------------------------------------------------- #
# Geometry / capacity helpers
# --------------------------------------------------------------------------- #
def _emu_to_in(emu: int) -> float:
    return (emu or 0) / EMU_PER_INCH


def _lines_needed(text: str, chars_per_line: int) -> int:
    text = text or ""
    if chars_per_line <= 0:
        return 1
    return max(1, math.ceil(len(text) / chars_per_line))


def _box_capacity_lines(width_in: float, height_in: float, font_pt: float):
    """Return (chars_per_line, lines_available) for a text box."""
    width_pt = width_in * 72
    height_pt = height_in * 72
    chars_per_line = max(1, int(width_pt / (AVG_CHAR_RATIO * font_pt)))
    lines_available = max(1, int(height_pt / (LINE_SPACING_RATIO * font_pt)))
    return chars_per_line, lines_available


def _box_char_capacity(width_in: float, height_in: float, font_pt: float) -> int:
    """Total characters a box holds at a given font size (chars_per_line * lines)."""
    cpl, lines = _box_capacity_lines(width_in, height_in, font_pt)
    return cpl * lines


def _bullets_lines_needed(bullets: List[dict], chars_per_line: int) -> int:
    """Total wrapped lines needed to render a bullet list."""
    total = 0
    for b in bullets or []:
        total += _lines_needed(b.get("text", ""), chars_per_line)
        for sub in b.get("sub", []) or []:
            # sub-bullets are indented -> slightly narrower; approximate 90%.
            total += _lines_needed(sub, max(1, int(chars_per_line * 0.9)))
    return total


def _bullets_char_count(bullets: List[dict]) -> int:
    n = 0
    for b in bullets or []:
        n += len(b.get("text", "") or "")
        for sub in b.get("sub", []) or []:
            n += len(sub or "")
    return n


# --------------------------------------------------------------------------- #
# Overflow measurement for one slide under a given font set
# --------------------------------------------------------------------------- #
def _columns_for(slide_info: dict) -> List[dict]:
    cols = slide_info.get("columns")
    if cols:
        return [{"subsection": c.get("subsection", ""), "bullets": c.get("bullets", []) or []}
                for c in cols]
    return [{"subsection": slide_info.get("subsection", ""),
             "bullets": slide_info.get("bullets", []) or []}]


def _distribute(columns: List[dict], n_body: int) -> List[dict]:
    if n_body <= 0:
        return []
    out = [{"subsection": "", "bullets": []} for _ in range(n_body)]
    if not columns:
        return out
    if len(columns) <= n_body:
        for i, c in enumerate(columns):
            out[i] = c
    else:
        for i in range(n_body - 1):
            out[i] = columns[i]
        merged = {"subsection": columns[n_body - 1].get("subsection", ""), "bullets": []}
        for c in columns[n_body - 1:]:
            merged["bullets"].extend(c.get("bullets", []))
        out[n_body - 1] = merged
    return out


def _worst_overflow(slide_info: dict, layout: LayoutSpec, fonts: dict) -> float:
    """Return the worst (max) fill ratio across title + body boxes.

    Ratio = lines_needed / lines_available (vertical fill). >1 means the text
    needs more rows than the box has and will overflow.
    """
    if layout is None:
        return 0.0
    ratios = [0.0]

    # Title box.
    if layout.title_idx is not None:
        tph = layout.ph_by_idx(layout.title_idx)
        if tph is not None:
            cpl, lines_av = _box_capacity_lines(
                _emu_to_in(tph.width), _emu_to_in(tph.height), fonts["title"]
            )
            title_txt = slide_info.get("subsection") or slide_info.get("section") or ""
            need = _lines_needed(title_txt, cpl)
            ratios.append(need / max(1, lines_av))

    # Body boxes (column model: measure the bullet box of each column).
    body_cols = layout.body_cols or [{"title": None, "body": i} for i in layout.body_idxs]
    columns = _columns_for(slide_info)
    mapped = _distribute(columns, len(body_cols))
    multi = len(body_cols) > 1
    for slot, col in zip(body_cols, mapped):
        bph = layout.ph_by_idx(slot["body"])
        if bph is None:
            continue
        bullets = list(col.get("bullets", []))
        subsec = col.get("subsection", "")
        # Heading text only counts toward the body box when there is no separate
        # heading placeholder for the column.
        if multi and subsec and slot.get("title") is None:
            bullets = [{"text": subsec, "sub": []}] + bullets
        cpl, lines_av = _box_capacity_lines(
            _emu_to_in(bph.width), _emu_to_in(bph.height), fonts["body"]
        )
        need = _bullets_lines_needed(bullets, cpl)
        ratios.append(need / max(1, lines_av))

    return max(ratios)


def _body_capacity_score(layout: LayoutSpec, fonts: dict) -> float:
    """Total body line-capacity of a layout (used to rank text-friendliness)."""
    if layout is None:
        return 0.0
    body_cols = layout.body_cols or [{"title": None, "body": i} for i in layout.body_idxs]
    total = 0
    for slot in body_cols:
        bph = layout.ph_by_idx(slot["body"])
        if bph is None:
            continue
        cpl, lines_av = _box_capacity_lines(
            _emu_to_in(bph.width), _emu_to_in(bph.height), fonts["body"]
        )
        total += cpl * lines_av
    return total


# --------------------------------------------------------------------------- #
# (i) text compression via one LLM call
# --------------------------------------------------------------------------- #
def _compress_slide(slide_info: dict, layout: LayoutSpec, args) -> bool:
    """Shorten title/bullets in place. Returns True if anything changed."""
    try:
        from jinja2 import Environment, StrictUndefined
        from camel.models import ModelFactory
        from camel.agents import ChatAgent
        from utils.wei_utils import get_agent_config, account_token
        from utils.src.utils import get_json_from_response
    except Exception as exc:  # noqa: BLE001
        print(f"[overflow] compress unavailable ({exc}); skipping step (i)")
        return False

    # Char budgets from the title/body box widths (1 line for title; modest for bullets).
    title_budget, bullet_budget, sub_budget = 60, 90, 80
    if layout is not None and layout.title_idx is not None:
        tph = layout.ph_by_idx(layout.title_idx)
        if tph is not None:
            cpl, _ = _box_capacity_lines(_emu_to_in(tph.width), _emu_to_in(tph.height),
                                         FONT_START["title"])
            title_budget = max(20, cpl)

    cfg_path = "utils/prompt_templates/overflow_compress.yaml"
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            prompt_cfg = yaml.safe_load(f)
    except Exception as exc:  # noqa: BLE001
        print(f"[overflow] cannot read {cfg_path}: {exc}")
        return False

    env = Environment(undefined=StrictUndefined)
    render_args = {
        "title": slide_info.get("subsection", ""),
        "bullets_json": json.dumps(slide_info.get("bullets", []), ensure_ascii=False, indent=1),
        "title_budget": title_budget,
        "bullet_budget": bullet_budget,
        "sub_budget": sub_budget,
    }
    system_prompt = env.from_string(prompt_cfg["system_prompt"]).render(**render_args)
    user_prompt = env.from_string(prompt_cfg["template"]).render(**render_args)

    try:
        cfg = get_agent_config(args.model_name_t)
        model = ModelFactory.create(
            model_platform=cfg["model_platform"],
            model_type=cfg["model_type"],
            model_config_dict=cfg["model_config"],
            url=cfg.get("url"),
        )
        agent = ChatAgent(system_message=system_prompt, model=model, message_window_size=2)
        agent.reset()
        resp = agent.step(user_prompt)
        raw = resp.msgs[0].content
        data = get_json_from_response(raw)
    except Exception as exc:  # noqa: BLE001
        print(f"[overflow] compression LLM call failed: {exc}")
        return False

    if not isinstance(data, dict):
        return False
    changed = False
    new_title = data.get("title")
    if isinstance(new_title, str) and new_title.strip() and new_title != slide_info.get("subsection"):
        slide_info["subsection"] = new_title.strip()
        changed = True
    new_bullets = data.get("bullets")
    if isinstance(new_bullets, list) and new_bullets:
        slide_info["bullets"] = new_bullets
        changed = True
    return changed


# --------------------------------------------------------------------------- #
# (ii) bounded font scaling
# --------------------------------------------------------------------------- #
def _scale_fonts(slide_info: dict, layout: LayoutSpec) -> Optional[dict]:
    """Find the largest font set (down to the floor) that fits. Returns the font
    dict if scaling helped fit, else the floor font set (best effort)."""
    fonts = dict(FONT_START)
    # Step body+sub together, then title, toward the floor.
    while True:
        if _worst_overflow(slide_info, layout, fonts) <= OVERFLOW_RATIO:
            return fonts
        stepped = False
        if fonts["body"] - FONT_STEP >= FONT_FLOOR["body"]:
            fonts["body"] -= FONT_STEP
            stepped = True
        if fonts["sub"] - FONT_STEP >= FONT_FLOOR["sub"]:
            fonts["sub"] -= FONT_STEP
            stepped = True
        if fonts["title"] - FONT_STEP >= FONT_FLOOR["title"]:
            fonts["title"] -= FONT_STEP
            stepped = True
        if not stepped:
            return fonts  # hit the floor; caller decides on (iii)


# --------------------------------------------------------------------------- #
# (iii) template switch / split
# --------------------------------------------------------------------------- #
def _has_visuals(slide_info: dict) -> bool:
    return bool(slide_info.get("images") or slide_info.get("tables") or slide_info.get("formulas"))


def _switch_layout(slide_info: dict, spec: TemplateSpec, fonts: dict) -> bool:
    """For a text-only slide, switch to the content layout with the most body
    capacity (and no picture slots). Returns True if switched to a better one."""
    if _has_visuals(slide_info):
        return False
    cur = spec.get(slide_info["template_id"])
    cur_score = _body_capacity_score(cur, fonts)
    best_name, best_score = slide_info["template_id"], cur_score
    for name in spec.content_layouts:
        ls = spec.get(name)
        if ls.n_pic != 0:
            continue
        score = _body_capacity_score(ls, fonts)
        if score > best_score:
            best_name, best_score = name, score
    if best_name != slide_info["template_id"]:
        slide_info["template_id"] = best_name
        return True
    return False


def _split_slide(slide_info: dict, layout: LayoutSpec, spec: TemplateSpec, fonts: dict) -> Optional[dict]:
    """Move trailing bullets onto a new continuation slide.

    Keeps as many leading bullets on the original slide as fit; returns the new
    continuation slide dict (or None if nothing could be moved).
    """
    bullets = slide_info.get("bullets", []) or []
    if len(bullets) <= 1:
        return None

    # Pick a text-only layout for the continuation (prefer the original if text-only).
    cont_template = slide_info["template_id"]
    if _has_visuals(slide_info):
        # continuation carries text only -> pick best text layout
        text_layouts = [n for n in spec.content_layouts if spec.get(n).n_pic == 0]
        if text_layouts:
            cont_template = max(text_layouts, key=lambda n: _body_capacity_score(spec.get(n), fonts))

    # Greedily keep leading bullets on the first slide.
    keep = []
    for i in range(len(bullets)):
        trial = deepcopy(slide_info)
        trial["bullets"] = bullets[: i + 1]
        if _worst_overflow(trial, layout, fonts) > OVERFLOW_RATIO and keep:
            break
        keep = bullets[: i + 1]
    if len(keep) >= len(bullets):
        keep = bullets[: max(1, len(bullets) // 2)]
    move = bullets[len(keep):]
    if not move:
        return None

    slide_info["bullets"] = keep
    # Avoid stacking " (cont.)" across cascaded splits.
    base_title = slide_info.get("subsection", "")
    cont_title = base_title if base_title.endswith("(cont.)") else (base_title + " (cont.)").strip()
    cont = {
        "section": slide_info.get("section"),
        "subsection": cont_title,
        "template_id": cont_template,
        "bullets": move,
        "images": [],
        "tables": [],
        "formulas": [],
    }
    if "font" in slide_info:
        cont["font"] = dict(slide_info["font"])
    return cont


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def _refine_one(slide_info: dict, spec: TemplateSpec, args, stats: dict, depth: int = 0) -> List[dict]:
    """Apply the ladder to one slide. Returns [slide] or [slide, continuation...].

    Continuation slides produced by a split are recursively refined (bounded by
    ``depth``) so a single very dense subsection can cascade across several
    supporting slides instead of leaving the tail overflowing.
    """
    layout = spec.get(slide_info.get("template_id"))
    if layout is None:
        return [slide_info]  # unknown layout; leave to filler to error clearly

    if _worst_overflow(slide_info, layout, FONT_START) <= OVERFLOW_RATIO:
        return [slide_info]

    # (i) compress (skip on continuation slides — already compressed once)
    if depth == 0 and _compress_slide(slide_info, layout, args):
        stats["compressed"] += 1
        if _worst_overflow(slide_info, layout, FONT_START) <= OVERFLOW_RATIO:
            return [slide_info]

    # (ii) bounded font scaling
    fonts = _scale_fonts(slide_info, layout)
    if fonts and fonts != FONT_START:
        slide_info["font"] = fonts
        stats["scaled"] += 1
    fonts = slide_info.get("font", FONT_START)
    if _worst_overflow(slide_info, layout, fonts) <= OVERFLOW_RATIO:
        return [slide_info]

    # (iii) switch to a more text-friendly layout, then re-scale
    if _switch_layout(slide_info, spec, fonts):
        stats["switched"] += 1
        layout = spec.get(slide_info["template_id"])
        fonts = _scale_fonts(slide_info, layout) or fonts
        if fonts != FONT_START:
            slide_info["font"] = fonts
        if _worst_overflow(slide_info, layout, fonts) <= OVERFLOW_RATIO:
            return [slide_info]

    # (iii) split into a supporting slide (cap recursion to avoid runaway splits)
    MAX_SPLIT_DEPTH = 4
    cont = _split_slide(slide_info, layout, spec, fonts)
    if cont is not None:
        stats["split"] += 1
        if depth < MAX_SPLIT_DEPTH:
            return [slide_info, *_refine_one(cont, spec, args, stats, depth + 1)]
        return [slide_info, cont]

    return [slide_info]


def refine_overflow(args, spec: Optional[TemplateSpec] = None) -> dict:
    """Rewrite the slide plan in place with readability-aware overflow handling.

    Returns a stats dict; never raises (logs and returns on error).
    """
    stats = {"compressed": 0, "scaled": 0, "switched": 0, "split": 0, "slides": 0}
    plan_json = f'contents/{args.paper_name}/<{args.model_name_t}_{args.model_name_v}>_slide_plan.json'
    try:
        plan = json.loads(Path(plan_json).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"[overflow] cannot read plan {plan_json}: {exc}")
        return stats

    if spec is None:
        template_path = getattr(args, "template_path", None) or resolve_template_path(
            getattr(args, "template_name", None),
            getattr(args, "template_dir", "utils/slides_template"),
        )
        spec = scan_template(template_path)

    new_slides: List[dict] = []
    for slide_info in plan.get("slides", []):
        stats["slides"] += 1
        try:
            new_slides.extend(_refine_one(slide_info, spec, args, stats))
        except Exception as exc:  # noqa: BLE001
            print(f"[overflow] slide refine failed ({exc}); keeping original")
            new_slides.append(slide_info)

    plan["slides"] = new_slides
    try:
        Path(plan_json).write_text(json.dumps(plan, ensure_ascii=False, indent=4), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"[overflow] cannot write plan {plan_json}: {exc}")

    print(f"[overflow] {stats}")
    return stats


if __name__ == "__main__":
    import argparse
    import types

    p = argparse.ArgumentParser(description="Run overflow refiner on an existing plan.")
    p.add_argument("--paper_name", required=True)
    p.add_argument("--model_name_t", default="gpt-4o-mini")
    p.add_argument("--model_name_v", default="gpt-4o-mini")
    p.add_argument("--template", default="slides3_template")
    p.add_argument("--template_dir", default="utils/slides_template")
    p.add_argument("--no_compress", action="store_true", help="skip the LLM compression step")
    a = p.parse_args()

    args = types.SimpleNamespace(
        paper_name=a.paper_name,
        model_name_t=a.model_name_t,
        model_name_v=a.model_name_v,
        template_name=a.template,
        template_path=None,
        template_dir=a.template_dir,
    )
    if a.no_compress:
        _compress_slide = lambda *x, **k: False  # noqa: E731,F811
    refine_overflow(args)

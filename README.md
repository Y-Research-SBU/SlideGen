 
## Using your own template

SlideGen no longer hard-codes a fixed set of layouts. Drop **any** `.pptx` into
`utils/slides_template/` and select it by its filename (without the extension):

```bash
python -m SlidesAgent.new_pipeline_logtime \
    --paper_path=your_path \
    --model_name_t="4o" --model_name_v="4o" \
    --template=my_template          # uses utils/slides_template/my_template.pptx
```

- If `--template` is omitted it defaults to `slides3_template`; if you keep only
  one `.pptx` in the folder, that one is used automatically.
- The template's **slide layouts** are auto-parsed: each layout's text/picture
  placeholders are inspected to infer roles (title bar, section number, bullet
  blocks, image slots) and to detect the four special slides — **cover**,
  **table of contents**, **section divider**, and **last/thanks** page. The
  Arranger agent is then given a layout library generated from *your* deck, so
  it only ever picks layouts that actually exist.
- **Authoring tips for a custom deck:** add slide *layouts* (in the Slide Master)
  with the placeholder mix you want — e.g. a wide title bar plus one large body
  box for a text slide, or a body box plus one or more PICTURE placeholders for
  a figure slide. Role detection uses placeholder type + position, with name
  hints (`Title Slide`, a contents/`Mulu` layout, a section/`dan_mulu` divider,
  a `Last_page`) recognized but not required.
- **Override (optional):** if auto-detection guesses wrong, place a sidecar
  `<template_stem>.slidegen.json` next to the `.pptx` to override role mapping
  or per-layout placeholder roles:

  ```json
  {
    "roles": {"cover": "Title Slide", "toc": "Contents", "divider": "Section", "last": "Thanks"},
    "layouts": {
      "MyTextLayout": {"part_idx": 13, "title_idx": 1, "body_idxs": [14], "pic_idxs": []}
    }
  }
  ```

## Overflow handling (readability-aware)

Long titles or dense bullet text used to overflow because font sizes were fixed.
The pipeline now runs a **readability-aware refiner** after planning that, for
any slide whose text would not fit its placeholders, applies a fallback ladder:

1. **Text compression** — an LLM shortens long titles and verbose bullets while
   preserving the key meaning.
2. **Bounded font scaling** — fonts are reduced within a readable range
   (body ≥ 16pt, sub-bullets ≥ 14pt, title ≥ 20pt).
3. **Template switch / split** — if content still does not fit, the slide moves
   to a more text-friendly layout or is split onto an additional supporting
   slide (titled "… (cont.)").

Disable it with `--no_overflow_refine`.
 
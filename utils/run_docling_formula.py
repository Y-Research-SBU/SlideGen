# run_docling_formula.py  (no BaseDocItem import)
import sys, json, argparse
from pathlib import Path
from collections import Counter
import os,   re
  
from docling_core.types.doc.document import BoundingBox, CoordOrigin
 
ROOT = "SlideGen"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
IMAGE_RESOLUTION_SCALE = 5
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import DocItemLabel, TextItem



import re
  
_SP_FIX = re.compile(
    r"(?:(?<=\\)\s+)|(?<=_)\s+|(?<=\^)\s+|(?<=\{)\s+|\s+(?=\})|\s+(?=[,=)])|(?<=\()\s+"
)

# \cmd { → \cmd{
_CMD_BRACE_FIX = re.compile(r"\\([A-Za-z]+)\s+\{")

def _collapse_spaces_inside_braces(text: str) -> str:
    # 把 { v i t } → {vit} 
    def repl(m):
        inner = m.group(1)
        inner2 = re.sub(r"(?<=\w)\s+(?=\w)", "", inner)
        return "{" + inner2 + "}"
    return re.sub(r"\{([^{}]+)\}", repl, text)


def normalize_formula(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return s
    s = _SP_FIX.sub("", s)                  
    s = _CMD_BRACE_FIX.sub(r"\\\1{", s)    # \cmd { → \cmd{
    s = _collapse_spaces_inside_braces(s)  # { v i t } → {vit}
    return re.sub(r"\s+", " ", s).strip()

def make_latex_compact(s: str) -> str:
     
    s = normalize_formula(s)
    return re.sub(r"\s+", "", s)

def build_converter(image_scale: float = 2.0) -> DocumentConverter:
    pipeline_options = PdfPipelineOptions()
    pipeline_options.images_scale = IMAGE_RESOLUTION_SCALE
    pipeline_options.generate_page_images = True
    pipeline_options.generate_picture_images = True
    pipeline_options.do_ocr = True
    pipeline_options.do_formula_enrichment = True

    # ✅ 能开的都开（不存在的字段不会报错）
    for opt in (
        "keep_backend", "keep_layout", "store_layout",
        "return_bboxes", "return_item_images", "generate_element_images",
    ):
        if hasattr(pipeline_options, opt):
            setattr(pipeline_options, opt, True)
            
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
    )
 
def _as_item(x):
     
    if isinstance(x, tuple) and len(x) >= 1:
        return x[0]
    return x

from collections import Counter
from docling_core.types.doc import DocItemLabel, TextItem

def extract_formulas_and_stats(pdf_path: Path, image_scale: float = 2.5):
    converter = build_converter(image_scale=image_scale)
    result = converter.convert(pdf_path)
    doc = result.document

    formulas = []
    label_counter = Counter()
    preview = []

    for el_raw in doc.iterate_items():
        el = _as_item(el_raw)   
        label = getattr(el, "label", None)
        label_counter[str(label)] += 1

        if isinstance(el, TextItem) and label == DocItemLabel.FORMULA:
            if getattr(el, "text", None):
                formulas.append(el.text.strip())

        if len(preview) < 30:
            snippet = getattr(el, "text", "")
            if isinstance(snippet, str):
                s = snippet.strip().replace("\n", " ")
                if len(s) > 60:
                    s = s[:57] + "..."
            else:
                s = ""
            preview.append({
                "type": el.__class__.__name__,
                "label": str(label),
                "text_snippet": s
            })

    return formulas, label_counter, preview

import re, os
from pathlib import Path
from docling_core.types.doc import ImageRefMode, DocItemLabel, TextItem
from docling_core.types.doc.document import BoundingBox, CoordOrigin

# Remove common OCR-introduced spaces in LaTeX
LATEX_SPACE_FIX = re.compile(
    r"(?:(?<=\\)\s+)|"      # spaces after a backslash command: \mathcal {L} -> \mathcal{L}
    r"(?<=_)\s+|"           # spaces after subscript marker: _ {x} -> _{x}
    r"(?<=\^)\s+|"          # spaces after superscript marker: ^ {2} -> ^{2}
    r"(?<=\{)\s+|"          # spaces right after '{'
    r"\s+(?=\})|"           # spaces right before '}'
    r"\s+(?=[,=)])|"        # spaces before ',', '=', or ')'
    r"(?<=\()\s+"           # spaces right after '('
)

 
def _save_image_any(img, path: Path) -> bool:
    
    try:
        # PIL.Image?
        from PIL.Image import Image as PILImage
        if isinstance(img, PILImage):
            img.save(path)
            return True
    except Exception:
        pass
    #   to_pil() ?
    try:
        pil = getattr(img, "to_pil", None)
        if callable(pil):
            pil().save(path)
            return True
    except Exception:
        pass
    # numpy  
    try:
        import numpy as np
        from PIL import Image
        if isinstance(img, np.ndarray):
            if img.dtype != np.uint8:
                
                arr = img
                arr = arr - arr.min()
                m = arr.max()
                arr = (arr / m * 255.0).astype("uint8") if m > 0 else arr.astype("uint8")
            else:
                arr = img
            if arr.ndim == 2:
                Image.fromarray(arr).save(path)
            elif arr.ndim == 3:
                Image.fromarray(arr).save(path)
            else:
                return False
            return True
    except Exception:
        pass
    #   .save()  ？
    try:
        save_fn = getattr(img, "save", None)
        if callable(save_fn):
            save_fn(path)
            return True
    except Exception:
        pass
    return False

def _unpack_item_image(x):
    
    if isinstance(x, tuple):
        if len(x) == 2:
            return x[0], x[1]
        if len(x) >= 1:
            return x[0], None
    return x, None
 

import re
from typing import Any, Optional, Tuple
import fitz  # PyMuPDF
from PIL import Image

def _get_first_attr(obj: Any, names: Tuple[str, ...]):
    for n in names:
        if hasattr(obj, n):
            v = getattr(obj, n)
            if v is not None:
                return v
    return None

def _coerce_bbox(bb: Any) -> Optional[Tuple[float,float,float,float]]:
    #   docling BoundingBox / tuple(list)
    if bb is None:
        return None
    #   obj.x/obj.y/obj.width/obj.height
    if all(hasattr(bb, k) for k in ("x","y","width","height")):
        return float(bb.x), float(bb.y), float(bb.width), float(bb.height)
    #   (x,y,w,h)
    if isinstance(bb, (tuple, list)) and len(bb) >= 4:
        x, y, w, h = bb[:4]
        return float(x), float(y), float(w), float(h)
    return None

def _save_pil(img_like: Any, path: Path) -> bool:
    try:
        if isinstance(img_like, Image.Image):
            img_like.save(path)
            return True
         
        try:
            import numpy as np
            if isinstance(img_like, np.ndarray):
                Image.fromarray(img_like).save(path)
                return True
        except Exception:
            pass
    except Exception:
        return False
    return False

def _get_page_images_from_result(result) -> list:
    
    candidates = []
    for holder in (result, getattr(result, "document", None), getattr(result, "bundle", None)):
        if holder is None:
            continue
        for name in ("page_images", "pages_images", "images"):
            v = getattr(holder, name, None)
            if v:
                candidates.append(v)
    
    for v in candidates:
        try:
            # list[PIL.Image]
            if isinstance(v, (list, tuple)) and all(isinstance(x, Image.Image) for x in v):
                return list(v)
            #   dict {page_idx: PIL}
            if isinstance(v, dict):
                items = sorted(v.items())
                imgs = [im for _, im in items if isinstance(im, Image.Image)]
                if imgs:
                    return imgs
        except Exception:
            pass
    return []
import re
from typing import Any, Optional, Tuple
import fitz  # PyMuPDF
from PIL import Image

def _get_first_attr(obj: Any, names: Tuple[str, ...]):
    for n in names:
        if hasattr(obj, n):
            v = getattr(obj, n)
            if v is not None:
                return v
    return None

def _coerce_bbox(bb: Any) -> Optional[Tuple[float,float,float,float]]:
    #   docling BoundingBox / tuple(list)
    if bb is None:
        return None
    #   obj.x/obj.y/obj.width/obj.height
    if all(hasattr(bb, k) for k in ("x","y","width","height")):
        return float(bb.x), float(bb.y), float(bb.width), float(bb.height)
    #   (x,y,w,h)
    if isinstance(bb, (tuple, list)) and len(bb) >= 4:
        x, y, w, h = bb[:4]
        return float(x), float(y), float(w), float(h)
    return None

def _save_pil(img_like: Any, path: Path) -> bool:
    try:
        if isinstance(img_like, Image.Image):
            img_like.save(path)
            return True
        
        try:
            import numpy as np
            if isinstance(img_like, np.ndarray):
                Image.fromarray(img_like).save(path)
                return True
        except Exception:
            pass
    except Exception:
        return False
    return False

def _get_page_images_from_result(result) -> list:
    
    candidates = []
    for holder in (result, getattr(result, "document", None), getattr(result, "bundle", None)):
        if holder is None:
            continue
        for name in ("page_images", "pages_images", "images"):
            v = getattr(holder, name, None)
            if v:
                candidates.append(v)
     
    for v in candidates:
        try:
            #   list[PIL.Image]
            if isinstance(v, (list, tuple)) and all(isinstance(x, Image.Image) for x in v):
                return list(v)
            #   dict {page_idx: PIL}
            if isinstance(v, dict):
                items = sorted(v.items())
                imgs = [im for _, im in items if isinstance(im, Image.Image)]
                if imgs:
                    return imgs
        except Exception:
            pass
    return []

def export_formulas_with_bbox_and_crops(result, out_json: Path, crop_dir: Path, pdf_path: Optional[Path]=None):
    
    crop_dir.mkdir(parents=True, exist_ok=True)
    doc = result.document
    out = []
    idx = 0
 
    page_images = _get_page_images_from_result(result)
 
    pdf_doc = None
    if pdf_path and pdf_path.exists():
        try:
            pdf_doc = fitz.open(pdf_path)
        except Exception:
            pdf_doc = None

    output_path = "elements_dump.txt"

    with open(output_path, "w", encoding="utf-8") as f:
        for x in doc.iterate_items():
            if isinstance(x, tuple):
                el = x[0]
                img = x[1] if len(x) > 1 else None
            else:
                el = x
                img = None

            f.write("=== Document Element ===\n")
            f.write(f"type:   {type(el).__name__}\n")
            f.write(f"label:  {getattr(el, 'label', 'None')}\n")
            f.write(f"bbox:   {getattr(el, 'bbox', 'None')}\n")

            if hasattr(el, "text"):
                f.write(f"text:   {el.text[:200]}...\n")
            elif hasattr(el, "caption"):
                f.write(f"caption: {el.caption[:200]}...\n")

            f.write(f"has_img: {'Yes' if img is not None else 'No'}\n")
            f.write("\n")

 
    for x in doc.iterate_items():
        # iterate_items()  return (item, image)  or  item
        if isinstance(x, tuple) and len(x) >= 1:
            el = x[0]
            img = x[1] if len(x) > 1 else None
        else:
            el, img = x, None
         
        # print("==== TEXT ITEM ====")
        # print("label:", el.label)
        # print("has bbox:", hasattr(el, "bbox"), getattr(el, "bbox", None))
        # print("has box:", hasattr(el, "box"), getattr(el, "box", None))
        # print("has bounds:", hasattr(el, "bounds"), getattr(el, "bounds", None))
        # print("has span_bbox:", hasattr(el, "span_bbox"), getattr(el, "span_bbox", None))
        # print("has origin:", getattr(getattr(el, "bbox", None), "origin", None))


 
        if not (hasattr(el, "label") and str(getattr(el, "label")).lower().endswith("formula")):
            
            from docling_core.types.doc import TextItem, DocItemLabel
            if not (isinstance(el, TextItem) and getattr(el, "label", None) == DocItemLabel.FORMULA):
                continue

        idx += 1
        latex_raw = (getattr(el, "text", "") or "").strip()

        
        page_num = _get_first_attr(el, ("page_no","page_idx","page_index","page","page_number"))
        bbox_obj = _get_first_attr(el, ("bbox","box","bounds","rect","span_bbox","source_bbox"))

         
        if page_num is None or bbox_obj is None:
            for src_name in ("origin","source","origin_ref","source_item","backend_item","node"):
                src = getattr(el, src_name, None)
                if src is None:
                    continue
                if page_num is None:
                    page_num = _get_first_attr(src, ("page_no","page_idx","page_index","page","page_number"))
                if bbox_obj is None:
                    bbox_obj = _get_first_attr(src, ("bbox","box","bounds","rect","span_bbox","source_bbox"))
                if page_num is not None and bbox_obj is not None:
                    break

        bbox_tuple = _coerce_bbox(bbox_obj)
        origin = getattr(bbox_obj, "origin", "unknown") if bbox_obj is not None else "unknown"

         
        crop_path = ""
        if img is not None:
            crop_path = str(crop_dir / f"formula_{idx:04d}.png")
            if not _save_pil(img, Path(crop_path)):
                crop_path = ""

         
        if not crop_path and isinstance(page_num, int) and bbox_tuple is not None and pdf_doc is not None:
            try:
                
                p0 = page_num if page_num < pdf_doc.page_count else page_num - 1
                x, y, w, h = bbox_tuple
                rect = fitz.Rect(x, y, x + w, y + h)
                pm = pdf_doc[p0].get_pixmap(matrix=fitz.Matrix(2.0, 2.0), clip=rect)  # 2x 放大
                crop_path = str(crop_dir / f"formula_{idx:04d}.png")
                pm.save(crop_path)
            except Exception:
                crop_path = ""

        out.append({
            "page": int(page_num) if isinstance(page_num, int) else None,
            "bbox": {
                "x": float(bbox_tuple[0]) if bbox_tuple else None,
                "y": float(bbox_tuple[1]) if bbox_tuple else None,
                "w": float(bbox_tuple[2]) if bbox_tuple else None,
                "h": float(bbox_tuple[3]) if bbox_tuple else None,
                "origin": str(origin) if origin is not None else "unknown",
            },
            "latex_raw": latex_raw,
            "latex": make_latex_compact(latex_raw) if latex_raw else "",
            "crop_path": crop_path
        })

     
    try:
        if pdf_doc is not None:
            pdf_doc.close()
    except Exception:
        pass

    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"Exported {len(out)} formulas with bbox to {out_json}")
    print(f"Crops saved under: {crop_dir}")
 


 
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--image-scale", type=float, default=2.5)
    args = ap.parse_args()

    pdf_path = Path(args.pdf)
    assert pdf_path.exists(), f"PDF not found: {pdf_path}"

    print("Going to convert document batch...")
    formulas, label_counter, preview_samples = extract_formulas_and_stats(
        pdf_path, image_scale=args.image_scale
    )

   
    print("\nLabel distribution:")
    for k, v in label_counter.most_common():
        print(f"  {k}: {v}")

    print(f"\nFormula count: {len(formulas)}")
    for i, f in enumerate(formulas[:10], 1):
        print(f"[{i}] {f}")

    print("\nFirst ~30 items preview (type / label / text_snippet):")
    for i, s in enumerate(preview_samples, 1):
        print(f"{i:02d}. {s['type']:>14} | {s['label']:<22} | {s['text_snippet']}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(formulas, f, ensure_ascii=False, indent=2)
        print(f"\nSaved formulas to {args.out}")
     
    converter = build_converter(image_scale=2.5)
    result = converter.convert(pdf_path)
    export_formulas_with_bbox_and_crops(
        result,
        out_json=Path("formulas_with_bbox.json"),
        crop_dir=Path("formula_crops")
    )


if __name__ == "__main__":
    main()



'''
CUDA_VISIBLE_DEVICES= \
PYTHONPATH=SlideGen:$PYTHONPATH \
python SlideGen/run_docling_formula.py \
  --pdf "SlideGen/assets/poster_data/STEP A General and Scalable Framework for Solving Video Inverse Problems/STEP A General and Scalable Framework for Solving Video Inverse Problems.pdf" \
  --out formulas.json \
  --image-scale 2.5


export  CUDA_VISIBLE_DEVICES=3 
PYTHONPATH=SlideGen:$PYTHONPATH \
python SlideGen/run_docling_formula.py \
  --pdf "SlideGen/assets/poster_data/Vision as LoRA/2503.20680v1.pdf" \
  --out formulas.json \
  --image-scale 1 



'''
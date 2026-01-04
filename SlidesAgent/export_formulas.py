from __future__ import annotations
import os
import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List

  
import fitz  # PyMuPDF
import sys
from PIL import Image as PILImage
ROOT = "SlideGen"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
# docling
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption




def build_converter(image_scale: float = 2.0) -> DocumentConverter:
    opts = PdfPipelineOptions()
    # 常用
    opts.images_scale = image_scale
    opts.generate_page_images = True
    opts.generate_picture_images = True

    # 尽量开启与坐标/公式相关的选项(不同版本可能不存在，做 hasattr 保护)
    for name in (
        "do_ocr",
        "do_formula_enrichment",       
        "do_formula_understanding",     
        "keep_layout",
        "store_layout",
        "return_bboxes",
        "return_item_images",
        "generate_element_images",
        "extract_tables",
        "extract_figures",
    ):
        if hasattr(opts, name):
            setattr(opts, name, True)

    # 你之前提到的 code enrichment，保持关闭
    for name in ("do_code_enrichment",):
        if hasattr(opts, name):
            setattr(opts, name, False)

    conv = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )
    return conv

 

import fitz  # PyMuPDF
from PIL import Image as PILImage
from pathlib import Path
import json

def _page_size_from_doc(doc, page_no: int):
     
    pages = getattr(doc, "pages", {}) or {}
    page = pages.get(page_no)
    if page is None:
        return None, None
    size = getattr(page, "size", None)
    if size is None:
        return None, None
    return getattr(size, "width", None), getattr(size, "height", None)

def _doc_bbox_bottomleft_to_xyxy(bbox: dict, page_h: float):
     
    l = float(bbox["l"]); r = float(bbox["r"])
    t = float(bbox["t"]); b = float(bbox["b"])
    # BOTTOMLEFT -> TOPLEFT：y_top = page_h - y_bottom
    y0 = page_h - b
    y1 = page_h - t
    x0, x1 = l, r
    
    if x1 < x0: x0, x1 = x1, x0
    if y1 < y0: y0, y1 = y1, y0
    return (x0, y0, x1, y1)

def export_formula_crops_from_texts(conv_res, pdf_path: Path, out_root: Path, paper_name: str, scale: float = 2.0):
     
    doc = conv_res.document
    pdf = fitz.open(str(pdf_path))

    paper_dir = out_root / paper_name
    paper_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_root / f"{paper_name}_formulas.json"

    formulas = {}
    idx = 1

    for el in getattr(doc, "texts", []):
        if str(getattr(el, "label", "")).lower() != "formula":
            continue

        text = (getattr(el, "text", "") or "").strip()
        prov = getattr(el, "prov", None) or getattr(el, "provenance", None)
        if not text or not prov or len(prov) == 0:
            continue

        pno = getattr(prov[0], "page_no", None)
        bb = getattr(prov[0], "bbox", None)
 
        if bb is None:
            continue
        if not isinstance(bb, dict):
             
            bb = {
                "l": getattr(bb, "l", None),
                "t": getattr(bb, "t", None),
                "r": getattr(bb, "r", None),
                "b": getattr(bb, "b", None),
                "coord_origin": str(getattr(bb, "coord_origin", "BOTTOMLEFT")),
            }
        if None in (bb.get("l"), bb.get("t"), bb.get("r"), bb.get("b")):
            continue
 
        w, h = _page_size_from_doc(doc, int(pno))
        if h is None:
            
            try:
                page = pdf[(pno - 1)]
                rect = page.rect
                w, h = float(rect.width), float(rect.height)
            except Exception:
                continue

        x0, y0, x1, y1 = _doc_bbox_bottomleft_to_xyxy(bb, page_h=h)
 
        out_png = paper_dir / f"{paper_name}-formula-{idx}.png"
        try:
            page = pdf[(pno - 1)]
            pm = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=fitz.Rect(x0, y0, x1, y1))
            pm.save(str(out_png))
        except Exception as e:
            print(f"[Warn] crop failed at idx={idx}: {e}")
            idx += 1
            continue
 
        width = height = size = aspect = None
        try:
            im = PILImage.open(out_png)
            width, height = im.width, im.height
            size = width * height
            aspect = width / height if height else None
        except Exception:
            pass

        formulas[str(idx)] = {
            "text": text,
            "page_no": int(pno),
            "bbox_doc": {k: float(v) if isinstance(v, (int, float)) else v for k, v in bb.items()},   
            "clip_rect_xyxy": [float(x0), float(y0), float(x1), float(y1)],   
            "formula_path": str(out_png),
            "width": width, "height": height,
            "figure_size": size, "figure_aspect": aspect,
            "container_attr": "texts", "method": "crop"
        }
        idx += 1

    pdf.close()
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(formulas, f, ensure_ascii=False, indent=2)

    print(f"[Formulas] JSON: {out_json}")
    print(f"[Formulas] PNG dir: {paper_dir}")
    print(f"[Formulas] total: {len(formulas)}")
    return formulas



def main():
    import argparse

    ap = argparse.ArgumentParser( )
    ap.add_argument("--pdf", required=True )
    ap.add_argument("--paper-name", required=True )
    ap.add_argument("--model-name-t", required=True )
    ap.add_argument("--model-name-v", required=True )
    ap.add_argument("--scale", type=float, default=2.0 )
    args = ap.parse_args()

    pdf_path = Path(args.pdf)
    assert pdf_path.exists(), f"PDF 不存在：{pdf_path}"
 
    converter = build_converter(image_scale=args.scale)
    conv_res = converter.convert(pdf_path)

 

    out_root = Path(f"<{args.model_name_t}_{args.model_name_v}>_images_and_tables")
    export_formula_crops_from_texts(
        conv_res=conv_res,
        pdf_path=Path(args.pdf),
        out_root=out_root,
        paper_name=args.paper_name,
        scale=args.scale,
    )

if __name__ == "__main__":
    main()




'''

CUDA_VISIBLE_DEVICES= \
python export_formulas.py \
  --pdf "SlideGen/assets/poster_data/Vision as LoRA/2503.20680v1.pdf" \
  --paper-name "Vision as LoRA" \
  --model-name-t "4o" \
  --model-name-v "4o" \
  --scale 16

CUDA_VISIBLE_DEVICES= \
python export_formulas.py \
  --pdf "SlideGen/assets/poster_data/STEP A General and Scalable Framework for Solving Video Inverse Problems/STEP A General and Scalable Framework for Solving Video Inverse Problems.pdf" \
  --paper-name "STEP A General and Scalable Framework for Solving Video Inverse Problems" \
  --model-name-t "4o" \
  --model-name-v "4o" \
  --scale 1 

'''

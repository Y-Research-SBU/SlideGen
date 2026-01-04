from dotenv import load_dotenv
from utils.src.utils import get_json_from_response
from utils.src.model_utils import parse_pdf
import json
import random
import time
from camel.models import ModelFactory
from camel.agents import ChatAgent
from tenacity import retry, stop_after_attempt
from docling_core.types.doc import ImageRefMode, PictureItem, TableItem 
 
from openai import OpenAI
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc.document import BoundingBox
from docling_core.types.doc.document import CoordOrigin
from pathlib import Path

import PIL

from marker.models import create_model_dict

from utils.wei_utils import *

from utils.pptx_utils import *
from utils.critic_utils import *
import torch
from jinja2 import Template
import re
import argparse    

load_dotenv()
IMAGE_RESOLUTION_SCALE = 5.0

pipeline_options = PdfPipelineOptions()
pipeline_options.images_scale = IMAGE_RESOLUTION_SCALE
pipeline_options.generate_page_images = True
pipeline_options.generate_picture_images = True

doc_converter = DocumentConverter(
    format_options={
        InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
    }
)

 
def build_converter( ) -> DocumentConverter:
    opts = PdfPipelineOptions() 
    opts.images_scale = IMAGE_RESOLUTION_SCALE
    opts.generate_page_images = True
    opts.generate_picture_images = True
     
    for name in (
        "do_ocr",
        "do_formula_enrichment",       
        "do_formula_understanding",    
    ):
        if hasattr(opts, name):
            setattr(opts, name, True)

     
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
    """
    返回 (width, height)。doc.pages 是 1-based 字典。
    """
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
    # BOTTOMLEFT -> TOPLEFT:y_top = page_h - y_bottom
    y0 = page_h - b
    y1 = page_h - t
    x0, x1 = l, r
     
    if x1 < x0: x0, x1 = x1, x0
    if y1 < y0: y0, y1 = y1, y0
    return (x0, y0, x1, y1)

def export_formula_crops_from_texts(args,raw_result ):
     
    # doc_converter = build_converter() 
    # conv_res = doc_converter.convert(args.paper_path)
    conv_res=raw_result
    doc = conv_res.document
     
    pdf = fitz.open(str(args.paper_path))
    out_root = Path(f'<{args.model_name_t}_{args.model_name_v}>_images_and_tables/{args.paper_name}')
    out_root.mkdir(parents=True, exist_ok=True)
    out_json = out_root / f"{args.paper_name}_formulas.json"

    formulas = {}
    idx = 1

    for el in getattr(doc, "texts", []):
        if str(getattr(el, "label", "")).lower() != "formula":
            continue
        raw = (getattr(el, "latex", None) or getattr(el, "text", "") or getattr(el, "content", "") or "")
        text = str(raw).strip()
        print("text")
        # text = (getattr(el, "text", "") or "").strip()
        prov = getattr(el, "prov", None) or getattr(el, "provenance", None)
        if not text or not prov or len(prov) == 0:
            continue

        pno = getattr(prov[0], "page_no", None)
        bb = getattr(prov[0], "bbox", None)
         
        if bb is None:
            continue
        if not isinstance(bb, dict):
            # 对象 -> dict
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
 
     
        scale = IMAGE_RESOLUTION_SCALE
 
         
        out_png = out_root / f"{args.paper_name}-formula-{idx}.png"
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
            "bbox_doc": {k: float(v) if isinstance(v, (int, float)) else v for k, v in bb.items()},  # 原始 l/t/r/b
            "clip_rect_xyxy": [float(x0), float(y0), float(x1), float(y1)],  # 转换后的裁剪框
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
    print(f"[Formulas] PNG dir: {out_root}")
    print(f"[Formulas] total: {len(formulas)}")
    return formulas,conv_res

from pathlib import Path
import re, json

 

def export_formula_sections_grouped_json_from_texts(args, conv_res, max_page_no_exclusive: int = 12):
 
    doc = conv_res.document
    out_root = Path(f'<{args.model_name_t}_{args.model_name_v}>_images_and_tables/{args.paper_name}')
    out_root.mkdir(parents=True, exist_ok=True)
    out_json = out_root / f"{args.paper_name}_formula_sections.json"

    def _get_prov(el):
        return getattr(el, "prov", None) or getattr(el, "provenance", None)

    def _get_page_no(el):
        prov = _get_prov(el)
        return getattr(prov[0], "page_no", None) if prov and len(prov) > 0 else None

    def _get_bbox_t(el):
        prov = _get_prov(el)
        bb = getattr(prov[0], "bbox", None) if prov and len(prov) > 0 else None
        if isinstance(bb, dict):
            return bb.get("t", None)
        if bb is not None:
            return getattr(bb, "t", None)
        return None

    def _norm(s): return (s or "").strip()

    def _is_text_label(label: str) -> bool:
        label = (label or "").lower()
        return label in {"paragraph", "text", "list_item", "bullet", "caption"}

    def _clean_latex(text: str) -> str:
        text = text.strip()
        m = re.fullmatch(r"\${1,2}\s*(.+?)\s*\${1,2}", text, flags=re.S) \
            or re.fullmatch(r"\\\((.+?)\\\)", text, flags=re.S) \
            or re.fullmatch(r"\\\[(.+?)\\\]", text, flags=re.S)
        return m.group(1).strip() if m else text

    _re_heading_num = re.compile(r"^\s*(\d+(?:\.\d+)*)\b")

    def _is_section_header(txt, label: str) -> bool:
        if not txt: return False
        if _re_heading_num.match(txt): return True
        lab = (label or "").lower()
        return ("section" in lab and "header" in lab)

    def _heading_level(title: str) -> int:
        m = _re_heading_num.match(title or "")
        return 1 + m.group(1).count(".") if m else 99

    # -------- linearize all content --------
    linear = []
    for el in getattr(doc, "texts", []):
        label = str(getattr(el, "label", "")).lower()
        page_no = _get_page_no(el)
        if page_no is None or page_no >= max_page_no_exclusive:
            continue
        y_top = _get_bbox_t(el)
        y_top = float(y_top) if y_top is not None else -1e9
        text = _norm(getattr(el, "text", "") or "")
        if not text:
            continue

        if label == "formula":
            linear.append({
                "kind": "formula",
                "latex": _clean_latex(text),
                "page_no": page_no,
                "y_top": y_top
            })
        elif _is_text_label(label):
            linear.append({
                "kind": "text",
                "content": text,
                "page_no": page_no,
                "y_top": y_top
            })
        elif _is_section_header(text, label):
            linear.append({
                "kind": "header",
                "title": text,
                "level": _heading_level(text),
                "page_no": page_no,
                "y_top": y_top
            })

    # -------- sort by page + y_top (PDF = bottom-left origin) --------
    linear.sort(key=lambda x: (x["page_no"], -x["y_top"]))

    # -------- section partitioning --------
    sections = []
    cur_section = {
        "section_title": None,
        "section_number": None,
        "section_pages": set(),
        "content": []
    }

    def is_garbage_text(text: str) -> bool:
        text = text.strip()
        if not text:
            return True
        if len(text) <= 3:
            return True  
        if re.fullmatch(r"[\s\.,;:()\[\]<>*/\\\"'=+\-~!?^|]{0,2}\s*[A-Za-z0-9]\s*", text):
            return True 
        if re.fullmatch(r"[A-Za-z0-9+/=\\<>|*_\"'.-]{1,8}", text):
            return True 
        if any(substr in text for substr in ["<latexi", "64=", "base64", ">A", "kS+Q", "Kr/", "\\", "†"]):
            return True
        return False

    def _flush_section(): 
        if any(x["type"] == "formula" for x in cur_section["content"]):
            out = {
                "section_title": cur_section["section_title"],
                "section_number": cur_section["section_number"],
                "section_pages": sorted(cur_section["section_pages"]),
                "content": cur_section["content"]
            }
            sections.append(out)


    for item in linear:
        if item["kind"] == "header": 
            _flush_section()
            cur_section = {
                "section_title": item["title"],
                "section_number": _re_heading_num.match(item["title"]).group(1) if _re_heading_num.match(item["title"]) else None,
                "section_pages": set(),
                "content": []
            }
        elif item["kind"] in {"text", "formula"}:
            if item["kind"] == "text":
                if is_garbage_text(item["content"]):
                    continue
            cur_section["content"].append({
                "type": item["kind"],
                **({"content": item["content"]} if item["kind"] == "text" else {"latex": item["latex"], "page_no": item["page_no"]})
            })
            cur_section["section_pages"].add(item["page_no"])

    _flush_section()

    # -------- write output --------
    out_json.parent.mkdir(parents=True, exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(sections, f, ensure_ascii=False, indent=2)

    print(f"[LinearSections] Saved to {out_json}")
    print(f"[LinearSections] Sections: {len(sections)}")
    return sections
 
@retry(stop=stop_after_attempt(1))
def parse_raw(args, actor_config, version=1):
    raw_source = args.paper_path
    markdown_clean_pattern = re.compile(r"<!--[\s\S]*?-->")

    raw_result = doc_converter.convert(raw_source)
    input_token, output_token =0,0
    
    raw_markdown = raw_result.document.export_to_markdown()
    text_content = markdown_clean_pattern.sub("", raw_markdown)
    start_time = time.time()
    if len(text_content) < 500:
        print('\nParsing with docling failed, using marker instead\n')
        parser_model = create_model_dict(device='cuda', dtype=torch.float16)
        text_content, rendered = parse_pdf(raw_source, model_lst=parser_model, save_file=False)

    if version == 1:
        template = Template(open("utils/prompts/gen_poster_raw_content.txt").read())
    elif version == 2:
        template = Template(open("utils/prompts/gen_slides_raw_content_v2.txt").read())
    use_gpt5_responses = False

    actor_sys_msg = 'You are the author of the paper, and you will create an academic presentation (slides) to explain the paper'
 
    if "gpt-5" in args.model_name_t.lower():  
        client = OpenAI()  
        use_gpt5_responses = True
    
    if "qwen" in str(args.model_name_t).lower():
        print("model_type=actor_config['model_type']: ", actor_config['model_type'])
        actor_model = ModelFactory.create(
            model_platform=actor_config['model_platform'],
            model_type=actor_config['model_type'],
            model_config_dict=actor_config['model_config'],
            url=actor_config['url'],
        ) 
    else:
        actor_model = ModelFactory.create(
            model_platform=actor_config['model_platform'],
            model_type=actor_config['model_type'],
            model_config_dict=actor_config['model_config'],
        )

    actor_agent = ChatAgent(
        system_message=actor_sys_msg,
        model=actor_model,
        message_window_size=10,
        token_limit=actor_config.get('token_limit', None)
    )

    while True:
        prompt = template.render(
            markdown_document=text_content, 
        )
        if use_gpt5_responses: 
            # response = client.responses.create(
            #     model=args.model_name_t,
            #     input=prompt,
            #     reasoning={"effort": "minimal"},   
            #     text={"verbosity": "low"},         
            # )
            # raw_output = extract_text_from_responses(response)
            # u = getattr(response, "usage", None) or {}
            # input_token  = getattr(u, "input_tokens",  getattr(u, "input_token_count", None))
            # output_token = getattr(u, "output_tokens", getattr(u, "output_token_count", None)) 
            # print("input_token: ",input_token)
            
            
            # Use a gateway-compatible helper:
            # try /v1/responses first, then fall back to /v1/chat/completions.
            # raw_output, input_token, output_token = openai_chat_text(
            #     client=client,
            #     model=args.model_name_t,
            #     user_prompt=prompt,
            #     system_prompt=actor_sys_msg,
            #     prefer_responses=True,
            # )
            # print("input_token: ", input_token)
            # print("change success")
            actor_agent.reset()
            response = actor_agent.step(prompt)
            input_token, output_token = account_token(response)
            raw_output = response.msgs[0].content
            print("gpt can run")
            
        else:
            if "qwen" in str(args.model_name_t).lower():
                response = chat_via_vllm(prompt,actor_config,actor_model,actor_sys_msg)
                raw_output = response.choices[0].message.content
                print("raw_output by qwen : ")
                print(raw_output) 
                input_token = response.usage.prompt_tokens
                output_token = response.usage.completion_tokens
                print("input_token: ",input_token)
            else:
                actor_agent.reset()
                response = actor_agent.step(prompt)
                input_token, output_token = account_token(response)
                raw_output = response.msgs[0].content
                print("gpt can run")


        content_json = get_json_from_response(raw_output)

        if len(content_json) > 0:
            break
        print('Error: Empty response, retrying...')
        if "qwen" in str(args.model_name_t).lower():
            text_content = text_content[:80000]


    print(type(content_json))
    print("content_json",content_json)
    # if len(content_json['sections']) > 9:
    #     # First 2 sections + randomly select 5 sections + last 2 sections
    #     selected_sections = content_json['sections'][:2] + random.sample(content_json['sections'][2:-2], 5) + content_json['sections'][-2:]
    #     content_json['sections'] = selected_sections

    has_title = False

    for section in content_json['sections']:
   
        if 'title' in section['title'].lower():
            has_title = True

    # if not has_title:
    #     print('Ouch! The response is invalid, the LLM is not following the format :(')
    #     raise
    end_time = time.time()
    time_taken = end_time - start_time
    os.makedirs(f'contents/{args.paper_name}', exist_ok=True)
   
    json.dump(content_json, open(f'contents/{args.paper_name}/<{args.model_name_t}_{args.model_name_v}>_raw_content.json', 'w'), indent=4)
    return input_token, output_token, time_taken, raw_result

from pprint import pprint

def safe_print_element_fields(element):
    print(f"[Type] {type(element)}")
    safe_dict = {}
    for k, v in vars(element).items():
        if isinstance(v, (str, int, float, tuple, list, dict, type(None))):
            safe_dict[k] = v
        else:
            safe_dict[k] = f"<{type(v).__name__}>"
    pprint(safe_dict)
 
def convert_bbox_to_pil_coords(bbox, page_width, page_height, pad=10):
    """
    将 BOTTOMLEFT 坐标的 bbox 转为 PIL 坐标（左上角原点） 
    """
    x0 = bbox.l
    x1 = bbox.r 
    y0 = page_height - bbox.b  # PIL y0: corresponds to the bottom of the PDF bbox
    y1 = page_height - bbox.t  # PIL y1: corresponds to the top of the PDF bbox

    # Add padding (and clip to image boundaries)
    return (
        max(0, x0 - pad),
        max(0, y1 - pad),
        min(page_width, x1 + pad),
        min(page_height, y0 + pad),
    )
  
def gen_image_and_table(args, conv_res):
    input_token, output_token = 0, 0
    raw_source = args.paper_path

    output_dir = Path(f'<{args.model_name_t}_{args.model_name_v}>_images_and_tables/{args.paper_name}')

    output_dir.mkdir(parents=True, exist_ok=True)
    doc_filename = args.paper_name

    # Save page images
    for page_no, page in conv_res.document.pages.items():
        page_no = page.page_no
        page_image_filename = output_dir / f"{doc_filename}-{page_no}.png"
        with page_image_filename.open("wb") as fp:
            page.image.pil_image.save(fp, format="PNG")
 
    # 修改裁剪框（扩大上下左右）  
    from PIL import Image
    
    for i, picture in enumerate(conv_res.document.pictures):
        page_no = picture.prov[0].page_no
        page = conv_res.document.pages[page_no]  
        full_img = page.image.pil_image   
        page_size_in_points = page.size  #  PDF 中的尺寸（单位 pt）
        page_width_pt, page_height_pt = page_size_in_points.width, page_size_in_points.height

        scale = full_img.width / page_width_pt
         
        bbox = picture.prov[0].bbox
        pad = 1   
        padded_bbox = BoundingBox(
            l= bbox.l - pad ,
            r=  bbox.r + pad ,
            b=   bbox.b - pad ,
            t=   bbox.t + pad ,
            coord_origin=bbox.coord_origin,
        )
        tl_bbox = padded_bbox.to_top_left_origin(page_height=page_height_pt)
        pil_box = tl_bbox.scaled(scale=scale).as_tuple() 
        left, top, right, bottom = pil_box
        cropped = full_img.crop((left, top, right, bottom))  
        cropped.save( f'<{args.model_name_t}_{args.model_name_v}>_images_and_tables/{args.paper_name}/{args.paper_name}-picture-{i+1}.png')
    
    table_counter = 0 
    for element, _level in conv_res.document.iterate_items():
        if isinstance(element, TableItem):
            table_counter += 1
            element_image_filename = (
                output_dir / f"{doc_filename}-table-{table_counter}.png"
            )
            with element_image_filename.open("wb") as fp:
                element.get_image(conv_res.document).save(fp, "PNG")
   

    # Save markdown with embedded pictures
    md_filename = output_dir / f"{doc_filename}-with-images.md"
    conv_res.document.save_as_markdown(md_filename, image_mode=ImageRefMode.EMBEDDED)

    # Save markdown with externally referenced pictures
    md_filename = output_dir / f"{doc_filename}-with-image-refs.md"
    conv_res.document.save_as_markdown(md_filename, image_mode=ImageRefMode.REFERENCED)

    # Save HTML with externally referenced pictures
    html_filename = output_dir / f"{doc_filename}-with-image-refs.html"
    conv_res.document.save_as_html(html_filename, image_mode=ImageRefMode.REFERENCED)

    tables = {}

    table_index = 1
    for table in conv_res.document.tables:
        caption = table.caption_text(conv_res.document)
        if len(caption) > 0:
            table_img_path = f'<{args.model_name_t}_{args.model_name_v}>_images_and_tables/{args.paper_name}/{args.paper_name}-table-{table_index}.png'
            table_img = PIL.Image.open(table_img_path)
            tables[str(table_index)] = {
                'caption': caption,
                'page_no': table.prov[0].page_no,
                'table_path': table_img_path,
                'width': table_img.width,
                'height': table_img.height,
                'figure_size': table_img.width * table_img.height,
                'figure_aspect': table_img.width / table_img.height,
            }

        table_index += 1

    images = {}
    image_index = 1
    for image in conv_res.document.pictures:
        caption = image.caption_text(conv_res.document) 
        print(f"[{i}] caption: {caption}")
        if len(caption) > 0:
            image_img_path = f'<{args.model_name_t}_{args.model_name_v}>_images_and_tables/{args.paper_name}/{args.paper_name}-picture-{image_index}.png'
            image_img = PIL.Image.open(image_img_path)
            images[str(image_index)] = {
                'caption': caption,
                'page_no': image.prov[0].page_no,
                'image_path': image_img_path,
                'width': image_img.width,
                'height': image_img.height,
                'figure_size': image_img.width * image_img.height,
                'figure_aspect': image_img.width / image_img.height,
            }
        image_index += 1

    json.dump(images, open(f'<{args.model_name_t}_{args.model_name_v}>_images_and_tables/{args.paper_name}_images.json', 'w'), indent=4)
    json.dump(tables, open(f'<{args.model_name_t}_{args.model_name_v}>_images_and_tables/{args.paper_name}_tables.json', 'w'), indent=4)

    return input_token, output_token, images, tables

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--paper_name', type=str, default=None)
    parser.add_argument('--model_name', type=str, default='4o')
    parser.add_argument('--paper_path', type=str, required=True)
    parser.add_argument('--index', type=int, default=0)
    args = parser.parse_args()

    agent_config = get_agent_config(args.model_name)

    if args.paper_name is None:
        args.paper_name = args.paper_path.split('/')[-1].replace('.pdf', '').replace(' ', '_')

    # Parse raw content
    input_token, output_token = parse_raw(args, agent_config)

    # Generate images and tables
    _, _ = gen_image_and_table(args)

    print(f'Token consumption: {input_token} -> {output_token}')

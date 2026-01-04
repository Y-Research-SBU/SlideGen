from dotenv import load_dotenv
import os
import json
import copy
from io import BytesIO
import yaml
from jinja2 import Environment, StrictUndefined
import base64
from utils.pptx_utils import extract_text_from_responses
from utils.src.utils import ppt_to_images, get_json_from_response
from docling_core.types.doc import TextItem
from camel.models import ModelFactory
from camel.agents import ChatAgent
from camel.messages import BaseMessage
from openai import OpenAI
from utils.pptx_utils import *
from utils.wei_utils import * 
import time

import pickle as pkl
import argparse

load_dotenv()

IMAGE_SCALE_RATIO_MIN = 50
IMAGE_SCALE_RATIO_MAX = 40
TABLE_SCALE_RATIO_MIN = 100
TABLE_SCALE_RATIO_MAX = 80

def compute_tp(raw_content_json): 
    total_length = 0
 
    section_lengths = []
    for section in raw_content_json['sections']:
        section_len = sum(len(sub['content']) for sub in section.get('subsections', []))
        section_lengths.append(section_len)
        total_length += section_len
 
    for i, section in enumerate(raw_content_json['sections']):
        section['tp'] = section_lengths[i] / total_length if total_length > 0 else 0
        section['text_len'] = section_lengths[i]

def compute_gp(table_info, image_info):
    total_area = 0
    for k, v in table_info.items():
        total_area += v['figure_size']

    for k, v in image_info.items():
        total_area += v['figure_size']

    for k, v in table_info.items():
        v['gp'] = v['figure_size'] / total_area

    for k, v in image_info.items():
        v['gp'] = v['figure_size'] / total_area
from openai import BadRequestError

def compress_image_bytes(img_bytes: bytes, max_side: int = 768, quality: int = 60) -> bytes:
    
    im = Image.open(BytesIO(img_bytes)).convert("RGB")
    w, h = im.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    out = BytesIO()
    im.save(out, format="JPEG", quality=quality, optimize=True)
    return out.getvalue()

def hard_truncate(text: str, limit: int) -> str:
     
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # 尝试在最后一个句点/换行处截断
    m = re.search(r'(?s)^.*([。．\.!\?]\s|\n)', cut)
    return m.group(0).strip() if m else cut.strip()

def get_outline_location(outline, subsection=False):
    outline_location = {}
    for k, v in outline.items():
        if k == 'meta':
            continue
        outline_location[k] = {
            'location': v['location'],
        }
        if subsection:
            if 'subsections' in v:
                outline_location[k]['subsections'] = get_outline_location(v['subsections'])
    return outline_location

def apply_outline_location(outline, location, subsection=False):
    new_outline = {}
    for k, v in outline.items():
        if k == 'meta':
            new_outline[k] = v
            continue
        new_outline[k] = copy.deepcopy(v)
        new_outline[k]['location'] = location[k]['location']
        if subsection:
            if 'subsections' in v:
                new_outline[k]['subsections'] = apply_outline_location(v['subsections'], location[k]['subsections'])

    return new_outline

def fill_location(outline, section_name, location_dict):
    new_outline = copy.deepcopy(outline)
    if 'subsections' not in new_outline[section_name]:
        return new_outline
    for k, v in new_outline[section_name]['subsections'].items():
        v['location'] = location_dict[k]['location']
    return new_outline

def recover_name_and_location(outline_no_name, outline):
    new_outline = copy.deepcopy(outline_no_name)
    for k, v in outline_no_name.items():
        if k == 'meta':
            continue
        new_outline[k]['name'] = outline[k]['name']
        if type(new_outline[k]['location']) == list:
            new_outline[k]['location'] = {
                'left': v['location'][0],
                'top': v['location'][1],
                'width': v['location'][2],
                'height': v['location'][3]
            }
        if 'subsections' in v:
            for k_sub, v_sub in v['subsections'].items():
                new_outline[k]['subsections'][k_sub]['name'] = outline[k]['subsections'][k_sub]['name']
                if type(new_outline[k]['subsections'][k_sub]['location']) == list:
                    new_outline[k]['subsections'][k_sub]['location'] = {
                        'left': v_sub['location'][0],
                        'top': v_sub['location'][1],
                        'width': v_sub['location'][2],
                        'height': v_sub['location'][3]
                    }
    return new_outline


def validate_and_adjust_subsections(section_bbox, subsection_bboxes):
    """
    Validate that the given subsections collectively occupy the entire section.
    If not, return an adjusted version that fixes the layout.
    
    We assume all subsections are intended to be stacked vertically with no gaps,
    spanning the full width of the section.

    :param section_bbox: dict with keys ["left", "top", "width", "height"]
    :param subsection_bboxes: dict of subsection_name -> bounding_box (each also
                              with keys ["left", "top", "width", "height"])
    :return: (is_valid, revised_subsections)
             where is_valid is True/False,
             and revised_subsections is either the same as subsection_bboxes if valid,
             or a new dict of adjusted bounding boxes if invalid.
    """

    # Helper functions
    def _right(bbox):
        return bbox["left"] + bbox["width"]
    
    def _bottom(bbox):
        return bbox["top"] + bbox["height"]
    
    section_left = section_bbox["left"]
    section_top = section_bbox["top"]
    section_right = section_left + section_bbox["width"]
    section_bottom = section_top + section_bbox["height"]

    # Convert dictionary to a list of (subsection_name, bbox) pairs
    items = list(subsection_bboxes.items())
    if not items:
        # No subsections is definitely not valid if we want to fill the section
        return False, None

    # Sort subsections by their 'top' coordinate
    items_sorted = sorted(items, key=lambda x: x[1]["top"])

    # ---------------------------
    # Step 1: Validate
    # ---------------------------
    # We'll check:
    # 1. left/right boundaries match the section for each subsection
    # 2. The first subsection's top == section_top
    # 3. The last subsection's bottom == section_bottom
    # 4. Each pair of consecutive subsections lines up exactly
    #    (previous bottom == current top) with no gap or overlap.

    is_valid = True

    # Check left/right for each
    for name, bbox in items_sorted:
        if bbox["left"] != section_left or _right(bbox) != section_right:
            is_valid = False
            break

    # Check alignment for the first and last
    if is_valid:
        first_sub_name, first_sub_bbox = items_sorted[0]
        if first_sub_bbox["top"] != section_top:
            is_valid = False

    if is_valid:
        last_sub_name, last_sub_bbox = items_sorted[-1]
        if _bottom(last_sub_bbox) != section_bottom:
            is_valid = False

    # Check consecutive alignment
    if is_valid:
        for i in range(len(items_sorted) - 1):
            _, current_bbox  = items_sorted[i]
            _, next_bbox     = items_sorted[i + 1]
            if _bottom(current_bbox) != next_bbox["top"]:
                is_valid = False
                break

    # If everything passed, we return
    if is_valid:
        return True, subsection_bboxes

    # ---------------------------
    # Step 2: Revise
    # ---------------------------
    # We will adjust all subsection bboxes so that they occupy
    # the entire section exactly, preserving each original bbox's
    # height *ratio* if possible.

    # 2a. Compute total original height (in the order of sorted items)
    original_heights = [bbox["height"] for _, bbox in items_sorted]
    total_original_height = sum(original_heights)

    # Avoid divide-by-zero if somehow there's a 0 height
    if total_original_height <= 0:
        # Fallback: split the section equally among subsections
        # to avoid zero or negative heights
        chunk_height = section_bbox["height"] / len(items_sorted)
        scale_heights = [chunk_height] * len(items_sorted)
    else:
        # Scale each original height by the ratio of
        # (section total height / sum of original heights)
        scale = section_bbox["height"] / total_original_height
        scale_heights = [h * scale for h in original_heights]

    # 2b. Assign bounding boxes top->bottom, ensuring no gap
    revised = {}
    current_top = section_top
    for i, (name, original_bbox) in enumerate(items_sorted):
        revised_height = scale_heights[i]
        # If there's floating error, we can clamp in the last iteration
        # so that the bottom exactly matches section_bottom.
        # But for simplicity, we'll keep it straightforward unless needed.

        revised[name] = {
            "left": section_left,
            "top": current_top,
            "width": section_bbox["width"],
            "height": revised_height
        }
        # Update current_top for next subsection
        current_top += revised_height

    # Due to potential float rounding, we can enforce the last subsection
    # to exactly end at section_bottom:
    last_name = items_sorted[-1][0]
    # Recompute the actual bottom after the above assignment
    new_bottom = revised[last_name]["top"] + revised[last_name]["height"]
    diff = new_bottom - section_bottom
    if abs(diff) > 1e-9:
        # Adjust the last subsection's height
        revised[last_name]["height"] -= diff

    # Return the revised dictionary
    return False, revised


def filter_image_table(args, filter_config):
    images = json.load(open(f'<{args.model_name_t}_{args.model_name_v}>_images_and_tables/{args.paper_name}_images.json', 'r'))
    tables = json.load(open(f'<{args.model_name_t}_{args.model_name_v}>_images_and_tables/{args.paper_name}_tables.json', 'r'))
    doc_json = json.load(open(f'contents/{args.paper_name}/<{args.model_name_t}_{args.model_name_v}>_raw_content.json', 'r'))
    agent_filter = 'image_table_filter_agent'
    with open(f"utils/prompt_templates/{agent_filter}.yaml", "r") as f:
        config_filter = yaml.safe_load(f)

    image_information = {}
    for k, v in images.items():
        image_information[k] = copy.deepcopy(v)
        image_information[k]['min_width'] = v['width'] // IMAGE_SCALE_RATIO_MIN
        image_information[k]['min_height'] = v['height'] // IMAGE_SCALE_RATIO_MIN
        image_information[k]['max_width'] = v['width'] // IMAGE_SCALE_RATIO_MAX
        image_information[k]['max_height'] = v['height'] // IMAGE_SCALE_RATIO_MAX

    table_information = {}
    for k, v in tables.items():
        table_information[k] = copy.deepcopy(v)
        table_information[k]['min_width'] = v['width'] // TABLE_SCALE_RATIO_MIN
        table_information[k]['min_height'] = v['height'] // TABLE_SCALE_RATIO_MIN
        table_information[k]['max_width'] = v['width'] // TABLE_SCALE_RATIO_MAX
        table_information[k]['max_height'] = v['height'] // TABLE_SCALE_RATIO_MAX

    filter_actor_sys_msg = config_filter['system_prompt']

    use_gpt5_responses = False

    if "gpt-5" in args.model_name_t.lower():  
        client = OpenAI()  
        use_gpt5_responses = True
    else: 
        if "qwen" in str(args.model_name_t).lower():
            filter_model = ModelFactory.create(
                model_platform=filter_config['model_platform'],
                model_type=filter_config['model_type'],
                model_config_dict=filter_config['model_config'],
                url=filter_config['url'],
            )
        else:
            filter_model = ModelFactory.create(
                model_platform=filter_config['model_platform'],
                model_type=filter_config['model_type'],
                model_config_dict=filter_config['model_config'],
            )

        filter_actor_agent = ChatAgent(
            system_message=filter_actor_sys_msg,
            model=filter_model,
            message_window_size=10,
        )

    filter_jinja_args = {
        'json_content': doc_json,
        'table_information': json.dumps(table_information, indent=4),
        'image_information': json.dumps(image_information, indent=4),
    }
    jinja_env = Environment(undefined=StrictUndefined)
    filter_prompt = jinja_env.from_string(config_filter["template"])
    user_prompt = filter_prompt.render(**filter_jinja_args)
     
    if use_gpt5_responses:
         
        response = client.responses.create(
            model=args.model_name_v,               
            input=user_prompt,
            reasoning={"effort": "minimal"},
            text={"verbosity": "low"}, 
        )
        raw_text = extract_text_from_responses(response)
        input_token = getattr(getattr(response, "usage", None), "input_tokens", None)
        output_token = getattr(getattr(response, "usage", None), "output_tokens", None)
    else:
        if "qwen" in str(args.model_name_t).lower():
            response = chat_via_vllm(user_prompt,filter_config,filter_model,filter_actor_sys_msg)
            raw_text = response.choices[0].message.content
            print("raw_output by qwen : ")
            print(raw_text) 
            input_token = response.usage.prompt_tokens
            output_token = response.usage.completion_tokens
            print("input_token: ",input_token)
        else: 
            filter_actor_agent.reset()
            response = filter_actor_agent.step(user_prompt)
            input_token, output_token = account_token(response)
            raw_text = response.msgs[0].content
    
    response_json = get_json_from_response(raw_text)
    table_information = response_json['table_information']
    image_information = response_json['image_information']
    json.dump(images, open(f'<{args.model_name_t}_{args.model_name_v}>_images_and_tables/{args.paper_name}/images_filtered.json', 'w'), indent=4)
    json.dump(tables, open(f'<{args.model_name_t}_{args.model_name_v}>_images_and_tables/{args.paper_name}/tables_filtered.json', 'w'), indent=4)

    return input_token, output_token


import re
CAPTION_OK_PATTERN = re.compile(r'^\s*(Figure|Fig\.?)\s*\d+', re.I)
 
def fix_image_captions(args, raw_result, actor_config, filtered_image_information):
    """
    Iterate through all images. If a caption is invalid, call the LLM to regenerate it.
    Returns the updated filtered_image_information and token statistics.
    """
 
    total_in, total_out = 0, 0
    fixer_config = yaml.safe_load(open("utils/prompt_templates/caption_fixer.yaml"))
    
    
    use_gpt5_responses = False

    if "gpt-5" in args.model_name_t.lower():  
        client = OpenAI()  
        use_gpt5_responses = True
    else:
    
        fixer_model = ModelFactory.create(
            model_platform=actor_config['model_platform'],
            model_type=actor_config['model_type'],
            model_config_dict=actor_config['model_config'],
            url=actor_config.get('url'),
        )
        fixer_agent = ChatAgent(
            system_message=fixer_config['system_prompt'],
            model=fixer_model,
            message_window_size=5,
        )
 
    for img_id, meta in filtered_image_information.items():
        caption = meta['caption']
        if CAPTION_OK_PATTERN.match(caption):
            print("Caption looks good.")
            continue  
        print("❌Caption Needs fixing.")
        img_path =  meta['image_path']
        with open(img_path, "rb") as f:
            img_bytes = f.read() 
        
        
        thumb_bytes = compress_image_bytes(img_bytes, max_side=768, quality=60)
        fig_b64_small = base64.b64encode(thumb_bytes).decode()

        # 2)   page_content
        
        page_content = get_page_text(raw_result, meta['page_no'])
        PAGE_LIMIT = 8000  
        page_text_small = hard_truncate(page_content or "", PAGE_LIMIT)

        jinja_env = Environment(undefined=StrictUndefined)
        tmpl = jinja_env.from_string(fixer_config["template"])
        prompt = tmpl.render( 
            fig_b64=fig_b64_small,  
            page_content=page_text_small,
            # page_b64=base64.b64encode(page_bytes).decode(),
        )
        
        if use_gpt5_responses:
            
            response = client.responses.create(
                model=args.model_name_v,               
                input=prompt,
                reasoning={"effort": "minimal"},
                text={"verbosity": "low"}, 
            )
            new_caption = extract_text_from_responses(response) 
            
            u = getattr(response, "usage", None) or {}
            in_tok  = getattr(u, "input_tokens",  getattr(u, "input_token_count", 0)) or 0
            out_tok = getattr(u, "output_tokens", getattr(u, "output_token_count", 0)) or 0
        else:
            fixer_agent.reset()
            response = fixer_agent.step(prompt)
            new_caption = response.msgs[0].content.strip() 
            in_tok, out_tok = account_token(response)
        total_in += in_tok; total_out += out_tok

        meta['caption'] = new_caption

    return filtered_image_information, total_in, total_out
 

def gen_figure_match(args, actor_config, raw_result):
    total_input_token, total_output_token = 0, 0
    agent_name = 'figure_match'
    doc_json = json.load(open(f'contents/{args.paper_name}/<{args.model_name_t}_{args.model_name_v}>_raw_content.json', 'r'))
    filtered_table_information = json.load(open(f'<{args.model_name_t}_{args.model_name_v}>_images_and_tables/{args.paper_name}/tables_filtered.json', 'r'))
    filtered_image_information = json.load(open(f'<{args.model_name_t}_{args.model_name_v}>_images_and_tables/{args.paper_name}/images_filtered.json', 'r'))
 
    filtered_image_information, fix_in, fix_out = fix_image_captions(
        args, raw_result, actor_config, filtered_image_information
    ) 
    json.dump(filtered_image_information, open(f'<{args.model_name_t}_{args.model_name_v}>_images_and_tables/{args.paper_name}/images_filtered.json', 'w'), indent=4)

    total_input_token, total_output_token = fix_in, fix_out

    filtered_table_information_captions = {}
    filtered_image_information_captions = {}

    for k, v in filtered_table_information.items():
        filtered_table_information_captions[k] = {
            v['caption']
        }

    for k, v in filtered_image_information.items():
        filtered_image_information_captions[k] = {
            v['caption']
        }
    print("agent_name: ",agent_name)  # slides_planner_new_v2
    start_time = time.time()
    with open(f"utils/prompt_templates/{agent_name}.yaml", "r") as f:
        planner_config = yaml.safe_load(f)

    compute_tp(doc_json)

    jinja_env = Environment(undefined=StrictUndefined)
    outline_template = jinja_env.from_string(planner_config["template"])
    planner_jinja_args = {
        'json_content': doc_json,
        'table_information': filtered_table_information_captions,
        'image_information': filtered_image_information_captions,
    }

    use_gpt5_responses = False
    if "gpt-5" in args.model_name_t.lower():  
        client = OpenAI()  
        use_gpt5_responses = True
    else:
        if "qwen" in str(args.model_name_t).lower():
            planner_model = ModelFactory.create(
                model_platform=actor_config['model_platform'],
                model_type=actor_config['model_type'],
                model_config_dict=actor_config['model_config'],
                url=actor_config['url'],
            )
        else:
            planner_model = ModelFactory.create(
                model_platform=actor_config['model_platform'],
                model_type=actor_config['model_type'],
                model_config_dict=actor_config['model_config'],
            )
        planner_agent = ChatAgent(
            system_message=planner_config['system_prompt'],
            model=planner_model,
            message_window_size=10,
        )
        print("planner_config['system_prompt']")
        print(planner_config['system_prompt'])
    print(f'Generating outline...')
    planner_prompt = outline_template.render(**planner_jinja_args)
    # print("=== Rendered Prompt ===")
    # print(planner_prompt)

    if use_gpt5_responses:
        response = client.responses.create(
            model=args.model_name_v,               
            input=planner_prompt,
            reasoning={"effort": "minimal"},
            text={"verbosity": "low"}, 
        )
        res_result = extract_text_from_responses(response)
        
        u = getattr(response, "usage", None) or {}
        input_token  = getattr(u, "input_tokens",  getattr(u, "input_token_count", None))
        output_token = getattr(u, "output_tokens", getattr(u, "output_token_count", None)) 
    else:
        if "qwen" in str(args.model_name_t).lower():
            response = chat_via_vllm(planner_prompt,actor_config,planner_model,planner_config['system_prompt'])
            print("raw_output by qwen : ")
            res_result = response.choices[0].message.content   
            input_token = response.usage.prompt_tokens
            output_token = response.usage.completion_tokens
            print("input_token: ",input_token)
            print(res_result)
        else:
            planner_agent.reset()
            response = planner_agent.step(planner_prompt)
            res_result=response.msgs[0].content
            input_token, output_token = account_token(response)

    total_input_token += input_token
    total_output_token += output_token 
    end_time = time.time()
    time_taken = end_time - start_time
    print("time_taken:",time_taken)
    figure_arrangement = get_json_from_response(res_result)
    figures_save_path = f"contents/{args.paper_name}/<{args.model_name_t}_{args.model_name_v}>_figures.json" 
    os.makedirs(os.path.dirname(figures_save_path), exist_ok=True)
    with open(figures_save_path, "w") as f:
        json.dump(figure_arrangement, f, indent=4)
    print(f'Figure arrangement: {json.dumps(figure_arrangement, indent=4)}')

    arranged_images = {}
    arranged_tables = {}
    assigned_images = set()
    assigned_tables = set()
    
    for section_name, figure in figure_arrangement.items():
        if 'image' in figure:
            image_id = str(figure['image'])
            if image_id in assigned_images:
                continue
            if image_id in filtered_image_information:
                arranged_images[image_id] = filtered_image_information[image_id]
                assigned_images.add(image_id)
        if 'table' in figure:
            table_id = str(figure['table'])
            if table_id in assigned_tables:
                continue
            if table_id in filtered_table_information:
                arranged_tables[table_id] = filtered_table_information[table_id]
                assigned_tables.add(table_id)
    
    compute_gp(arranged_tables, arranged_images)

    return total_input_token, total_output_token, time_taken, figure_arrangement


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--paper_name', type=str, default=None)
    parser.add_argument('--model_name', type=str, default='4o')
    parser.add_argument('--paper_path', type=str, required=True)
    parser.add_argument('--index', type=int, default=0)
    parser.add_argument('--max_retry', type=int, default=3)
    args = parser.parse_args()

    actor_config = get_agent_config(args.model_name)
    critic_config = get_agent_config(args.model_name)

    if args.paper_name is None:
        args.paper_name = args.paper_path.split('/')[-1].replace('.pdf', '').replace(' ', '_')

    # input_token, output_token = filter_image_table(args, actor_config)
    print(f'Token consumption: {input_token} -> {output_token}')

    input_token, output_token = gen_outline_layout(args, actor_config, critic_config)
    print(f'Token consumption: {input_token} -> {output_token}')
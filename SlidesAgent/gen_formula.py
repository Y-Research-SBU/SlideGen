from utils import extract_formulas_from_yellowbox 
from pathlib import Path 
from dotenv import load_dotenv
import os
import json
import copy
import yaml
from jinja2 import Environment, StrictUndefined
import base64
import  re
from utils.src.utils import ppt_to_images, get_json_from_response
from docling_core.types.doc import TextItem
from camel.models import ModelFactory
from camel.agents import ChatAgent
from camel.messages import BaseMessage
import time
from utils.pptx_utils import *
from utils.wei_utils import *

import pickle as pkl
import argparse

load_dotenv()
 
import os
import cv2
import json
from pathlib import Path

def generate_formula_size_json(image_folder, output_json_path):
 
    image_folder = Path(image_folder)
    size_info = {}

    for image_path in sorted(image_folder.glob("*.png")):
        img = cv2.imread(str(image_path))
        if img is None:
            continue  # skip unreadable images

        height, width = img.shape[:2]
        size_info[image_path.name] = {
            "width": width,
            "height": height
        } 
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(size_info, f, indent=2)
    print(f"Saved formula image sizes to: {output_json_path}")

# mode 1 
def gen_formula_match_v1(args, actor_config, raw_result):
    total_input_token, total_output_token = 0, 0
    agent_name = 'formula_match'
    print("start preparing formula")
    # Load subsection structure and formula info
    subsection_json = json.load(open(f'contents/{args.paper_name}/<{args.model_name_t}_{args.model_name_v}>_raw_content.json', 'r'))
    formula_json = json.load(open(f'<{args.model_name_t}_{args.model_name_v}>_images_and_tables/{args.paper_name}/{args.paper_name}_formula_sections.json', 'r'))
    start_time = time.time()
    # Load prompt template
    with open(f"utils/prompt_templates/{agent_name}.yaml", "r") as f:
        planner_config = yaml.safe_load(f)

    # Prepare Jinja template and input variables
    jinja_env = Environment(undefined=StrictUndefined)
    formula_template = jinja_env.from_string(planner_config["template"])
    
    planner_prompt = formula_template.render(
        json_content=subsection_json,
        formula_information=formula_json,
    )
    planner_jinja_args = {
        'json_content': subsection_json,
        'formula_information': formula_json,
    }
    jinja_env.filters['tojson'] = lambda x: json.dumps(x, ensure_ascii=False)

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
 
    print(f'Generating formula-subsection mapping...')
    planner_prompt = formula_template.render(**planner_jinja_args)

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
    elif "qwen" in str(args.model_name_t).lower():
        response = chat_via_vllm(planner_prompt,actor_config,planner_model,planner_config['system_prompt'])
        res_result = response.choices[0].message.content 
        print("raw_output by qwen : ")
        input_token = response.usage.prompt_tokens
        output_token = response.usage.completion_tokens
        print(res_result)
    else:
        planner_agent.reset()
        response = planner_agent.step(planner_prompt)
        res_result = response.msgs[0].content
        input_token, output_token = account_token(response)
    total_input_token += input_token
    total_output_token += output_token
    end_time = time.time()
    time_taken = end_time - start_time
    print("time_taken:",time_taken)
    # Parse and save response
    formula_match = get_json_from_response(res_result)
    save_path = f"contents/{args.paper_name}/<{args.model_name_t}_{args.model_name_v}>_formula_match.json"
    with open(save_path, "w") as f:
        json.dump(formula_match, f, indent=4)

    print(f'Formula match saved to: {save_path}')
    print(f'Total tokens used: input={total_input_token}, output={total_output_token}')
    return total_input_token,total_output_token,time_taken



# mode 3  
def build_formula_json( 
        args,
        raw_result 
): 
    print("start build_formula_json")
    results = []
    total_in, total_out = 0, 0
    paper_outline_json = f'contents/{args.model_name_t}_{args.model_name_v}_{args.paper_name}_raw_content.json' 
    pattern = re.compile(r"page_(\d+)_formula_(\d+)\.png")
    formula_imgs_paths = f"contents/{args.paper_name}/formula_images"
    folder = Path(formula_imgs_paths)
    start_time = time.time()
    for img_path in folder.iterdir():
        if img_path.is_file():
            match = pattern.match(img_path.name)
            if match:
                page_no = int(match.group(1))
                formula_no = int(match.group(2))
        
        page_content = get_page_text(raw_result, page_no)
        with open(img_path, "rb") as f:
            img_bytes = f.read() 
        
        img_b64 = base64.b64encode(img_bytes).decode("ascii")

        formula_config  = get_agent_config(args.model_name_v)
        with open("utils/prompt_templates/match_formula_to_outline.yaml") as f:
            formula_prompt_cfg  = yaml.safe_load(f) 
 
        formula_model = ModelFactory.create(
            model_platform=formula_config['model_platform'],
            model_type=formula_config['model_type'],
            model_config_dict=formula_config['model_config'],
            url=formula_config.get('url'),
        )
        formula_agent = ChatAgent(
            system_message=formula_prompt_cfg['system_prompt'],
            model=formula_model,
            message_window_size=5,
        ) 
        actor_sys_msg = "You are a meticulous research assistant.  Your task is to locate where a specific mathematical formula belongs within the structure of a scientific paper, and describe its purpose."
 
        def chat_via_vllm_mm(prompt_text: str, img_b64: str) -> str: 
            model_name = formula_config.get("model") or formula_config.get("model_type")
            cfg = formula_config.get("model_config", {})
            resp = formula_model._client.chat.completions.create(
                model=model_name,  # e.g., "Qwen/Qwen2-VL-7B-Instruct"
                messages=[
                    {"role": "system", "content": actor_sys_msg},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt_text},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{img_b64}"}
                            }
                        ],
                    },
                ],
                max_tokens=cfg.get("max_tokens", 1024),
                temperature=cfg.get("temperature", 0.2),
                top_p=cfg.get("top_p", 0.95),
                # timeout=60,
            )
            return resp.choices[0].message.content


        # page_dir = Path(f'<{args.model_name_t}_{args.model_name_v}>_images_and_tables/{args.paper_name}')
        # page_path = page_dir / f"{args.paper_name}-{page_no}.png"
        # with open(page_path, "rb") as f:
        #     page_bytes = f.read()
        jinja_env = Environment(undefined=StrictUndefined)
        formula_template = jinja_env.from_string(formula_prompt_cfg["template"])
 
        formula_jinja_args = {
            'paper_outline_json': paper_outline_json,
            'page_context': page_content,
            'formula_image_b64': img_b64,
            'page_prev_no': max(page_no - 1, 0),
            'page_curr_no':page_no
        }
        prompt = formula_template.render(**formula_jinja_args)
 
        if "qwen" in str(args.model_name_t).lower():
            resp = chat_via_vllm_mm(prompt, img_b64  )
            print("formula_agent  resp by qwen : ")
            print(resp)
        else:
            formula_agent.reset()
            resp = formula_agent.step(prompt)
            in_tok, out_tok = account_token(resp)
            total_in += in_tok; total_out += out_tok
        new_caption = get_json_from_response(resp.msgs[0].content.strip()) 
        new_caption["page_no"] = page_no
        new_caption["formula_no"] = formula_no
        new_caption['img_name']=img_path.name 
        img = cv2.imread(str(img_path)) 
        # pixels, px
        height, width = img.shape[:2]  
        new_caption['width']=width
        new_caption['height']=height 
        results.append(new_caption)
        print("new_caption",new_caption)
    end_time = time.time()
    time_taken = end_time - start_time
    print("time_taken:",time_taken)
    output_path = f"contents/{args.paper_name}/formula_index.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=4)
    print("results",results)
    return results,total_in,total_out,time_taken




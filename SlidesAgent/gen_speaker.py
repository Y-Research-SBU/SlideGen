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


def gen_speaker_script(args, actor_config, raw_result):
    total_input_token, total_output_token = 0, 0
    agent_name = 'speaker_script'
     
    doc_json = json.load(open(f'contents/{args.paper_name}/<{args.model_name_t}_{args.model_name_v}>_raw_content.json', 'r'))
 
    speaker_script = {}
    start_time = time.time()
 
    with open(f"utils/prompt_templates/{agent_name}.yaml", "r") as f:
        planner_config = yaml.safe_load(f)
 
    jinja_env = Environment(undefined=StrictUndefined)
    script_template = jinja_env.from_string(planner_config["template"])
 
    planner_jinja_args = {
        'json_content': doc_json,
        'raw_result':raw_result,
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
     
    planner_prompt = script_template.render(**planner_jinja_args)

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
            res_result = response.choices[0].message.content   
            input_token = response.usage.prompt_tokens
            output_token = response.usage.completion_tokens
        else:
            planner_agent.reset()
            response = planner_agent.step(planner_prompt)
            res_result = response.msgs[0].content
            input_token, output_token = account_token(response)

    total_input_token += input_token
    total_output_token += output_token 
    
    end_time = time.time()
    time_taken = end_time - start_time
    print("time_taken:", time_taken)
    speaker_script = get_json_from_response(res_result) 
    
    speaker_script_save_path = f"contents/{args.paper_name}/<{args.model_name_t}_{args.model_name_v}>_speaker_script.json" 
    os.makedirs(os.path.dirname(speaker_script_save_path), exist_ok=True)
    with open(speaker_script_save_path, "w") as f:
        json.dump(speaker_script, f, indent=4)
    
    print(f'Generated speaker script: {json.dumps(speaker_script, indent=4)}')
    print("speaker_script",speaker_script)
    return total_input_token, total_output_token, time_taken

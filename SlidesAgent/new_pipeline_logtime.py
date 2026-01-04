from SlidesAgent.parse_raw import parse_raw, gen_image_and_table,export_formula_crops_from_texts,export_formula_sections_grouped_json_from_texts
from SlidesAgent.gen_figure_match import gen_figure_match,filter_image_table
from utils.wei_utils import get_agent_config, utils_functions, run_code, style_bullet_content, scale_to_target_area, char_capacity
from SlidesAgent.gen_formula import build_formula_json,gen_formula_match_v1
from utils.src.utils import ppt_to_images
from SlidesAgent.gen_speaker import gen_speaker_script
from SlidesAgent.layout_agent_xin import generate_slide_plan
from SlidesAgent.layout_filler import generate_pptx_from_plan
from utils.ablation_utils import no_tree_get_layout 
from math import ceil
import sys
 
from pathlib import Path 
from utils.src.utils import ppt_to_images

from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.enum.text import PP_ALIGN
 
import argparse
import json
import os
import time
 

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

pipeline_options = PdfPipelineOptions() 

doc_converter = DocumentConverter(
    format_options={
        InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
    }
)
# Create a theme profile here
theme_title_text_color = (255,255,0)
theme_title_fill_color = (255,255,0)
theme = {
    'panel_visible': True,
    'textbox_visible': False,
    'figure_visible': False,
    'panel_theme': {
        'color': theme_title_fill_color,
        'thickness': 5,
        'line_style': 'solid',
    },
    'textbox_theme': None,
    'figure_theme': None,
}

def extract_title_text(title_raw):
    """ title 为 str / list / dict / list[dict]"""
    if isinstance(title_raw, list):
        parts = []
        for t in title_raw:
            if isinstance(t, dict) and "runs" in t:
                for run in t["runs"]:
                    parts.append(run.get("text", ""))
            else:
                parts.append(str(t))
        return ' '.join(parts)
    elif isinstance(title_raw, dict):
        return str(title_raw.get('text', ''))
    else:
        return str(title_raw)

def extract_bullet_text(bullet_raw): 
    if isinstance(bullet_raw, list):
        return ' '.join([extract_bullet_text(b) for b in bullet_raw])
    elif isinstance(bullet_raw, dict):
        if "text" in bullet_raw:
            return bullet_raw["text"]
        elif "runs" in bullet_raw:
            return ''.join([r.get("text", "") for r in bullet_raw["runs"]])
        else:
            return ""
    else:
        return str(bullet_raw)

def save_panels(panels, paper_name, save_dir="outputs"):
    os.makedirs(save_dir, exist_ok=True)
    with open(os.path.join(save_dir, f"{paper_name}_panels.json"), "w") as f:
        json.dump(panels, f, indent=4)

def load_panels(paper_name, save_dir="outputs"):
    with open(os.path.join(save_dir, f"{paper_name}_panels.json"), "r") as f:
        return json.load(f)


from pptx.util import Inches, Pt
from pptx.enum.text import PP_ALIGN
from pptx import Presentation
 
  

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Poster Generation Pipeline')
    parser.add_argument('--paper_path', type=str)
    parser.add_argument('--model_name_t', type=str, default='4o')
    parser.add_argument('--model_name_v', type=str, default='4o')
    parser.add_argument('--index', type=int, default=0)
    parser.add_argument('--paper_name', type=str, default=None)
    parser.add_argument('--tmp_dir', type=str, default='tmp')  
    parser.add_argument('--no_blank_detection', action='store_true', help='When overflow is severe, try this option.')
    parser.add_argument('--ablation_no_tree_layout', action='store_true', help='Ablation study: no tree layout')
    parser.add_argument('--ablation_no_commenter', action='store_true', help='Ablation study: no commenter')
    parser.add_argument('--ablation_no_example', action='store_true', help='Ablation study: no example')
    parser.add_argument("--formula_mode", type=int, choices=[1, 2, 3], default=1,
                    help="Method to add formulas: "
                        "1 = use bbox crop from docling, "
                        "2 = use LaTeX code rendering, "
                        "3 = use user-marked boxes")
    args = parser.parse_args()

    
    if args.formula_mode == 1:
        print("👉 Using Docling bbox crop method...") 
    elif args.formula_mode == 2:
        print("👉 Using Docling LaTeX rendering method...") 
    elif args.formula_mode == 3:
        print("👉 Using user-marked boxes method...")


    start_time = time.time()
    os.makedirs(args.tmp_dir, exist_ok=True)

    detail_log = {} 
    slide_width_inches = 13.33
    slide_height_inches = 7.5
    slide_width = Inches(slide_width_inches)
    slide_height = Inches(slide_height_inches)
  

    if args.paper_name is None: 
        base_name = os.path.basename(args.paper_path)           
        paper_name = os.path.splitext(base_name)[0]             
        paper_name = paper_name.replace(' ', '_')              
        args.paper_name = paper_name
    else:
        paper_name = args.paper_name.replace(' ', '_')
            

    output_pptx = f'contents/{args.paper_name}/{args.model_name_t}_{args.model_name_v}_output_slides.pptx'
 
    # if os.path.exists(output_pptx):
    #     print(f"[SKIP] 已存在 {output_pptx}，程序结束。")
    #     sys.exit(0)


    paper_key = f"<{args.model_name_t}_{args.model_name_v}>_{paper_name}"
   
    agent_config_t = get_agent_config(args.model_name_t)
    agent_config_v = get_agent_config(args.model_name_v)
    total_input_tokens_t, total_output_tokens_t = 0, 0
    total_input_tokens_v, total_output_tokens_v = 0, 0


    meta_json_path = args.paper_path.replace('paper.pdf', 'meta.json')

    print(f'slides size: {slide_width_inches} x {slide_height_inches} inches')


    figs_json_path  = f"contents/{args.paper_name}/<{args.model_name_t}_{args.model_name_v}>_figures.json"
    formula_json_path = f"contents/{args.paper_name}/<{args.model_name_t}_{args.model_name_v}>_formula_match.json"
    paper_outline_json = f'contents/{args.paper_name}/<{args.model_name_t}_{args.model_name_v}>_raw_content.json'
    plan_json = f'contents/{args.paper_name}/<{args.model_name_t}_{args.model_name_v}>_slide_plan.json'
 
    # if not all(os.path.exists(p) for p in [figs_json_path, formula_json_path, paper_outline_json, plan_json]):
    if True:
        raw_source = args.paper_path 
        raw_result = doc_converter.convert(raw_source)
        # Step 1: Parse the raw paper
        input_token, output_token, time_taken, raw_result = parse_raw(args, agent_config_t, version=2)
            
        total_input_tokens_t += input_token
        total_output_tokens_t += output_token
        
        print(f'Parsing token consumption: {input_token} -> {output_token}')

        detail_log['outliner_in_t'] = input_token
        detail_log['outliner_out_t'] = output_token 
        detail_log['outliner_time'] = time_taken 
        _, _, images, tables = gen_image_and_table(args, raw_result) 
        
        if args.formula_mode == 1:
            print("start export_formula_crops_from_texts")
            formulas,conv_res = export_formula_crops_from_texts(args,raw_result)
            print("start export_formula_sections_grouped_json_from_texts")
            export_formula_sections_grouped_json_from_texts(args, conv_res)
        elif args.formula_mode == 3: 
            print("add formula")
            _,total_in,total_out,time_taken = build_formula_json(args, raw_result) 
            detail_log['formula_in_t3'] = input_token
            detail_log['formula_out_t3'] = output_token 
            detail_log['formula_time3'] = time_taken 
        # Step 2: Filter unnecessary images and tables
        input_token, output_token = filter_image_table(args, agent_config_t)
        total_input_tokens_t += input_token
        total_output_tokens_t += output_token
        print(f'Filter figures token consumption: {input_token} -> {output_token}')

        detail_log['filter_in_t'] = input_token
        detail_log['filter_out_t'] = output_token 
        input_token, output_token, time_taken, figures = gen_figure_match(args, agent_config_t,raw_result)
        total_input_tokens_t += input_token
        total_output_tokens_t += output_token
        detail_log['mapper_in_t'] = input_token
        detail_log['mapper_out_t'] = output_token 
        detail_log['mapper_time'] = time_taken 
        
        input_token, output_token, time_taken = gen_speaker_script(args, agent_config_t, raw_result)
        detail_log['speaker_in_t'] = input_token
        detail_log['speaker_out_t'] = output_token
        detail_log['speaker_time'] = time_taken
        
        input_token,output_token,time_taken = gen_formula_match_v1(args, agent_config_t,raw_result)
        total_input_tokens_t += input_token
        total_output_tokens_t += output_token
        detail_log['Formula_in_t'] = input_token
        detail_log['Formula_out_t'] = output_token
        detail_log['Formula_time'] = time_taken

        input_token, output_token, time_taken  = generate_slide_plan(args)
        total_input_tokens_t += input_token
        total_output_tokens_t += output_token
 
        detail_log['arranger_in_t'] = input_token
        detail_log['arranger_out_t'] = output_token
        detail_log['arranger_time'] = time_taken
        end_time = time.time()
        time_taken = end_time - start_time
        print("time_taken:",time_taken)
        # log
        output_dir = f'contents/{args.paper_name}'
         
         
    detail_log_file = os.path.join(output_dir, f'<{args.model_name_t}_{args.model_name_v}>_log.json')
    with open(detail_log_file, 'w') as f:
        json.dump(detail_log, f, indent=4)
    print("✅ all files exist……")
    generate_pptx_from_plan(args,3)
 
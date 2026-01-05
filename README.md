<div align="center">
  
  <img src="./asset/logo2.jpg" height="200" style="object-fit: contain;">

  <h2>SlideGen: Collaborative Multimodal Agents for Scientific Slide Generation </h2>
  <!-- <h4>🌟 🌟</h4> -->
  
  <br>
  
  <p>
    <a href="https://LiangXin1001.github.io">Xin Liang</a><sup>1</sup>&nbsp;
    <a href="https://wyattz23.github.io">Xiang Zhang</a><sup>2</sup>&nbsp;
    <a href="https://Y-Research-SBU.github.io/SlideGen">Yiwei Xu</a><sup>3</sup>&nbsp;
    <a href="https://Y-Research-SBU.github.io/SlideGen">Siqi Sun</a><sup>4</sup>&nbsp;
    <a href="https://chenyuyou.me/">Chenyu You</a><sup>1</sup>
  </p>

  <p>
    <sup>1</sup> Stony Brook University &nbsp;&nbsp; 
    <sup>2</sup> University of British Columbia &nbsp;&nbsp; <br>
    <sup>3</sup> University of California, Los Angeles &nbsp;&nbsp; 
    <sup>4</sup> Fudan University &nbsp;&nbsp; 
  
  </p>

<p align="center">
  <a href="https://arxiv.org/pdf/2512.04529">
    <img src="https://img.shields.io/badge/ArXiv-2508.17188-B31B1B?style=flat-square&logo=arxiv" alt="Paper">
  </a>
   
    
  <a href="https://Y-Research-SBU.github.io/SlideGen">
    <img src="https://img.shields.io/badge/Project-Website-blue?style=flat-square&logo=googlechrome" alt="Project Website">
  </a>

  <a href="https://github.com/Y-Research-SBU/SlideGen/blob/main/docs/images/wechat.jpg">
    <img src="https://img.shields.io/badge/WeChat-Group-green?style=flat-square&logo=wechat" alt="WeChat Group">
  </a>
</p>

  
  <!-- <a href="https://huggingface.co/spaces/Y-Research-Group/PosterGen">
    <img src="https://img.shields.io/badge/Hugging%20Face-Demo-yellow?style=flat-square&logo=huggingface" alt="Hugging Face Demo">
  </a>
  -->
   
 

</div>

## Abstract

> **SlideGen** is a collaborative, multimodal agent framework for automatically generating high-quality presentation slides from scientific papers.  
> Unlike prior approaches that reduce slide generation to text-only summarization, SlideGen treats slide generation as a **design-aware multimodal reasoning problem**, explicitly modeling structure planning, visual composition, and iterative refinement.
>
> SlideGen orchestrates a set of **specialized vision–language agents**, each responsible for a distinct stage in a professional slide creation workflow:
>
> - **Outliner Agent** – analyzes the paper structure and constructs a coherent slide-level outline with ordered bullet points.  
> - **Mapper Agent** – aligns figures and tables with their most relevant textual content.  
> - **Formulizer Agent** – identifies and assigns equations to appropriate slides with contextual explanations.  
> - **Arranger Agent** – selects layout templates and places multimodal elements to achieve balanced and diverse visual compositions.  
> - **Speaker Agent** – generates concise presenter notes to support oral explanation.  
> - **Refiner Agent** – performs slide merging, layout adjustment, and visual emphasis refinement for readability and consistency.
>
> By integrating **visual-in-the-loop reasoning** with an extensible template library, SlideGen produces **editable PPTX slides** that exhibit strong logical flow, aesthetic balance, and faithful content coverage—without relying on reference decks.  
> Extensive evaluations across visual quality, content faithfulness, and communication effectiveness demonstrate that SlideGen consistently outperforms existing automated slide generation systems.

![](./asset/teaser.jpg)


## Quick start

### 1) Environment

Requirements:
- Python 3.10+ (recommended)
- An OpenAI API key

Create and activate an environment (example with conda), then install dependencies from `requirements.txt`:

```bash
conda create -n paper2pptx python=3.12 -y
conda activate paper2pptx

cd  SlideGen

python -m pip install --no-build-isolation \
  "python-pptx @ https://codeload.github.com/Force1ess/python-pptx/zip/dc356685d4d210a10abe1ffab3c21315cdfae63d"

pip install -r requirements.txt

```

Set your API key:

```bash
export OPENAI_API_KEY=your_key
```

###   Install LibreOffice

LibreOffice is useful if your pipeline converts slide formats or needs headless office rendering.

**Windows**
1. Download and install LibreOffice from the official website.
2. Add LibreOffice to your system `PATH`:
   - Default install: add `C:\Program Files\LibreOffice\program` to `PATH`
   - Custom install: add `<your_install_path>\LibreOffice\program` to `PATH`

**macOS**
```bash
brew install --cask libreoffice
```

**Ubuntu/Linux**
```bash
sudo apt install libreoffice
# Or using snap:
sudo snap install libreoffice
```

### 2) Run on one paper

This matches your usual command:

```bash
conda activate paper2pptx
cd SlideGen
export OPENAI_API_KEY=your_key

python -m SlidesAgent.new_pipeline_logtime   \
    --paper_path=your_path   \
    --model_name_t="4o"  \
    --model_name_v="4o"
```

Notes:
- Change `CUDA_VISIBLE_DEVICES` to pick a different GPU. If you do not have CUDA, you can omit that prefix.
- Replace `--paper_path` with your PDF path.

## Output

By default, the pipeline writes a generated PPTX under `contents/<paper_name>/` (the exact filename depends on your pipeline code and arguments).  
The deck is a standard PPTX that you can open and edit in PowerPoint or Keynote.

## What the system does

Conceptually, SlideGen runs a sequence of agents:
- Outliner builds the slide structure and bullet plan
- Mapper assigns figures and tables to the most relevant slides
- Formulizer assigns equations to slides
- Arranger selects a layout template and places assets
- Refiner merges sparse slides and applies a consistent theme color for readability

## WebUI (Slides Generator) — Quick Start
 

---

### 1) Terminal A — Start Backend  

```bash
cd webui/backend
  
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 2) Terminal B — Start Frontend 

```bash
cd webui/frontend
npm install
npm run dev
```

## 📊 Example Results

Our system generates professional academic decks with high visual quality. Here are some examples of generated decks:



![Example 1](./asset/4o_4o_output_slides1_01.jpg)

![Example 2](./asset/4o_4o_output_slides2_01.jpg)
![Example 3](./asset/4o_4o_output_slidesshengwu_01.jpg)

## Citation
```
@article{liang2025slidegen,
  title={SlideGen: Collaborative Multimodal Agents for Scientific Slide Generation},
  author={Liang, Xin and Zhang, Xiang and Xu, Yiwei and Sun, Siqi and You, Chenyu},
  journal={arXiv preprint arXiv:2512.04529},
  year={2025}
}
```

## Acknowledgments
This codebase is built upon following open-source projects. We express our sincere gratitude to:
- **[Docling](https://www.docling.ai/)**: An open-source document processing framework that supports parsing and converting multiple document formats (e.g., PDF, DOCX, PPTX).  
- **[Marker](https://github.com/datalab-to/marker)**: High-quality PDF parsing library that enables accurate content extraction from research papers.
- **[python-pptx](https://github.com/scanny/python-pptx)**: Python library for creating PowerPoint (.PPTX) poster files.
- **[Paper2poster](https://github.com/Paper2Poster/Paper2Poster)**: Multi-agent LLMs for creating PowerPoint (.PPTX) poster files.

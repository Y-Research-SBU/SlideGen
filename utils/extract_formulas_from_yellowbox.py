from pathlib import Path
import fitz  # PyMuPDF
import cv2
import os
import numpy as np
import pandas as pd
from pathlib import Path
import cv2
import numpy as np
 

def render_pdf_to_images(pdf_path: str, output_folder: str, dpi: int = 300):
    """
    Render each page of a PDF as an image.
    """
    doc = fitz.open(pdf_path)
    Path(output_folder).mkdir(parents=True, exist_ok=True)
    image_paths = []

    for i, page in enumerate(doc):
        pix = page.get_pixmap(dpi=dpi)
        output_path = os.path.join(output_folder, f"page_{i+1}.png")
        pix.save(output_path)
        image_paths.append(output_path)

    return image_paths

def extract_formulas_from_images(image_paths, output_folder: str, min_area: int = 4000):
    """
    Detect black-bordered rectangles (likely formulas) from images and save them.
    """
    Path(output_folder).mkdir(parents=True, exist_ok=True)
    formula_images = []

    for img_path in image_paths:
        img = cv2.imread(img_path)
        # yellow bbox
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        lower_yellow = np.array([20, 150, 150])
        upper_yellow = np.array([35, 255, 255])
        mask = cv2.inRange(hsv, lower_yellow, upper_yellow)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contours = sorted(contours, key=lambda c: cv2.boundingRect(c)[1])
        page_name = Path(img_path).stem
        count = 0

        for cnt in contours: 
            x, y, w, h = cv2.boundingRect(cnt) 
            # Adjust to the approximate pixel width of the yellow border.
            border_thickness = 9   
            x_in = x + border_thickness
            y_in = y + border_thickness
            w_in = w - 2 * border_thickness
            h_in = h - 2 * border_thickness

            if w > 50  and h > 5: 
                crop = img[y_in:y_in+h_in, x_in:x_in+w_in]
                output_path = os.path.join(output_folder, f"{page_name}_formula_{count+1}.png")
                cv2.imwrite(output_path, crop)
                formula_images.append(output_path)
                count += 1

    return formula_images


def extract_formulas_from_yellowbox(pdf_path, page_img_folder ,formula_output_folder):
    page_images = render_pdf_to_images(pdf_path, page_img_folder)
    formula_imgs = extract_formulas_from_images(page_images, formula_output_folder)
  

if __name__ == '__main__':
    pdf_path = "assets/poster_data/STEP A General and Scalable Framework for Solving Video Inverse Problems/STEP A General and Scalable Framework for Solving Video Inverse Problems.pdf"   
    page_img_folder = "<4o_4o>_images_and_tables/STEP_A_General_and_Scalable_Framework_for_Solving_Video_Inverse_Problems"
    pdf_path="assets/poster_data/Vision as LoRA/Vision as LoRA.pdf"
    page_img_folder = "<4o_4o>_images_and_tables/Vision as LoRA" 
    pdf_name = Path(pdf_path).stem
    print(pdf_name) 
    formula_output_folder = f"contents/{pdf_name}/formula_images" 
    page_images = render_pdf_to_images(pdf_path, page_img_folder)
    formula_imgs = extract_formulas_from_images(page_images, formula_output_folder)
  
from typing import List, Tuple
import pandas as pd
import re
import xml.etree.ElementTree as ET
import numpy as np
import json

from xmot.logger import Logger
from xmot.digraph.particle import Particle
from xmot.mot.utils import areaBbox
from xmot.digraph import commons
from xmot import config

"""
Parser of xmot.digraph.particle in different formats.
"""

def load_blobs_from_excel(file_name: str) -> List[Particle]:
    """ A temporary io function to load data from Kerri-Lee's excel data.

    The function assumes a specific format of the excel data, and will be replaced by more
    general format later.
    """
    data_id = pd.read_excel(file_name, sheet_name="Particle ID", engine="openpyxl")
    data_pos = pd.read_excel(file_name, sheet_name="Raw_data", engine="openpyxl")

    # remove N/A
    data_id = data_id.fillna(0).astype(int)

    particles = []
    for i in range(0, len(data_id)):
        row_id = data_id.loc[i]
        row_pos = data_pos.loc[i]

        row_id = row_id[row_id.gt(0)]
        if len(row_id) <= 1:
            # Has only "Act_frame" column and no ided particle in this frame.
            continue

        time_frame = row_id["Act_frame"]
        # Iterate all particles ided in this frame
        for j in range(1, len(row_id)):
            id = row_id[j]
            pos = [row_pos[2 * id], row_pos[2 * id + 1]]
            # Has no bubble info and predicted positions for now.
            particles.append(Particle(id, time_frame, pos))

    return particles

def load_blobs_from_text(file_name: str, img_height=commons.PIC_DIMENSION[0], img_width=commons.PIC_DIMENSION[1]) -> List[Particle]:
    particles = []
    with open(file_name, "r") as f:
        for line in f:
            terms = line.split(";")
            terms = [term.strip() for term in terms]
            if len(terms) != 7:
                Logger.warning("Invalid blob info: {:s}".format(line))
            else:
                #x1, y1, x2, y2, width, height, id, time_frame = terms
                # The centroid_x, centroid_y could be negative, according to Kalman filter's prediction
                centroid_x, centroid_y, width, height, id, frame_id = [int(term) for term in terms[0:6]]
                contour = np.array(json.loads(terms[6]), dtype=np.float32)
                if len(contour) == 0:
                    contour = None
                ## Sanity check. During Kalman filter, the coordinates of the bbox might be out of
                ## the image. We need to check them before adding this particle into digraph.
                #if (x1 < 0 and x2 < 0) or \
                #        (x1 > img_width and x2 > img_width) or \
                #        (y1 < 0 and y2 < 0) or \
                #        (y1 > img_height and y2 > img_height):
                #    Logger.debug("Invalid particle. Coordinates outside the image. {:d} {:d} {:d} {:d}".format(x1, y1, x2, y2))
                #    continue  # Skip this particle. Invalid.

                #x1_new = x1 if x1 >= 0 else 0
                #y1_new = y1 if y1 >= 0 else 0
                #x2_new = x2 if x2 < img_width else img_width
                #y2_new = y2 if y2 < img_height else img_height

                #width_new = x2_new - x1_new
                #height_new = y2_new - y1_new

                ## If after adjustment, the particle doesn't have a valid size, discard it.
                #if width_new <=0 or height_new <=0:
                #    Logger.debug("Invalid particle. Non-positive width or height. {:d} {:d} {:d} {:d}".format(x1, y1, x2, y2))
                #    continue
                #particles.append(Particle([x1_new, y1_new], bbox=[width_new, height_new], id=id, time_frame=time_frame))
                particles.append(
                    Particle(
                        [centroid_x, centroid_y], bbox=[width, height], id=id, time_frame=frame_id,
                        contour=contour
                    )
                )
    return particles

def parse_pascal_xml(file_path: str, area_threshold=config.AREA_THRESHOLD) -> Tuple[List[Particle], str]:
    """
    Parse one labelled data in the PASCAL VOC format.

    Images are labelled by LabelImg with tags:
        particle_no-bubble_circle
        particle_no-bubble_non-circle
        particle_bubble_circle
        particle_bubble_non-circle
        shell_circle
        shell_non-circle
        agglomerate

    Return:
        List[Particle]: List of particles parsed from FILE.
        str: the file name of the image.
    """
    doc = ET.parse(file_path)
    file_name = doc.find("filename").text # Only the file name of the corresponding image.
    particles = []
    for obj in doc.findall("object"): # Only find direct child of doc.
        properties = obj.find("name").text.split("_") # label
        p_type=properties[0]
        p_shape=""
        p_bubble=None
        if p_type == "particle":
            p_shape = properties[2]
            if properties[1] == "bubble":
                p_bubble = Particle([0,0], [1,1]) # Dummy object.
        elif p_type == "shell":
            p_shape = properties[1]
            p_bubble = Particle([0, 0], [1, 1]) # Dummy object. A shell must have bubble.
        elif p_type == "agglomerate":
            p_shape = "non-circle"
            p_bubble = None
        p_bbox = obj.find("bndbox")
        xmin = int(p_bbox.find("xmin").text)
        ymin = int(p_bbox.find("ymin").text)
        xmax = int(p_bbox.find("xmax").text)
        ymax = int(p_bbox.find("ymax").text)
        bbox = [xmin, ymin, xmax, ymax]
        if areaBbox(bbox) <= area_threshold:
            continue
        p = Particle(position=[xmin, ymin], bbox=[xmax - xmin, ymax - ymin],
                     type=p_type, shape=p_shape, bubble=p_bubble)
        particles.append(p)

    # sort in ascending order of y (row-index of numpy), and then x (column-index of numpy).
    particles.sort(key=lambda p: list(reversed(p.get_position())))
    obj = re.match(".*_([0-9]+)_([a-zA-Z]*)([0-9]+)\.([a-zA-Z]+)", file_name)
    if obj is None:
        print(f"Cannot read video and image id from picture {file_name}")
    else:
        image_id = int(obj.group(3))
        video_id = int(obj.group(1))
        id = 0
        for p in particles:
            p.set_id(id)
            p.set_time_frame(image_id)
            id += 1
    return particles, file_name




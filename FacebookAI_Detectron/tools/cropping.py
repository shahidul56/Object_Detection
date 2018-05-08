# -*- coding: utf-8 -*-
"""
Created on Mon Apr  9 16:57:01 2018

@author: twang

cropping people box and mask rcnn 

"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from collections import defaultdict
import cv2  # NOQA (Must import before importing caffe2 due to bug in cv2)
import os

import numpy as np
import sys
import argparse

from caffe2.python import workspace

from core.config import assert_and_infer_cfg
from core.config import cfg
from core.config import merge_cfg_from_file
from utils.io import cache_url
from utils.timer import Timer
import core.test_engine as infer_engine
import datasets.dummy_datasets as dummy_datasets
import utils.c2 as c2_utils

import pycocotools.mask as mask_util
from utils.colormap import colormap

#import utils.vis as vis_utils

c2_utils.import_detectron_ops()
# OpenCL may be enabled by default in OpenCV3; disable it because it's not
# thread safe and causes unwanted GPU memory allocations.
cv2.ocl.setUseOpenCL(False)

_GRAY = (218, 227, 218)
_GREEN = (18, 127, 15)
_WHITE = (255, 255, 255)


def parse_args():
    parser = argparse.ArgumentParser(description='End-to-end inference')
    parser.add_argument(
        '--video_dir',dest='video_dir',help='image or folder of images', default=''
    )
    
    return parser.parse_args()

def convert_from_cls_format(cls_boxes, cls_segms, cls_keyps):
    """Convert from the class boxes/segms/keyps format generated by the testing
    code.
    """
    box_list = [b for b in cls_boxes if len(b) > 0]
    if len(box_list) > 0:
        boxes = np.concatenate(box_list)
    else:
        boxes = None
    if cls_segms is not None:
        segms = [s for slist in cls_segms for s in slist]
    else:
        segms = None
    if cls_keyps is not None:
        keyps = [k for klist in cls_keyps for k in klist]
    else:
        keyps = None
    classes = []
    for j in range(len(cls_boxes)):
        classes += [j] * len(cls_boxes[j])
    return boxes, segms, keyps, classes
    
def vis_mask(img, mask, col, alpha=0.4, show_border=True, border_thick=1):
    """Visualizes a single binary mask."""

    img = img.astype(np.float32)
    idx = np.nonzero(mask)

    img[idx[0], idx[1], :] *= 1.0 - alpha
    img[idx[0], idx[1], :] += alpha * col

    if show_border:
        _, contours, _ = cv2.findContours(
            mask.copy(), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
        cv2.drawContours(img, contours, -1, _WHITE, border_thick, cv2.LINE_AA)

    return img.astype(np.uint8)


def vis_class(img, pos, class_str, font_scale=0.35):
    """Visualizes the class."""
    x0, y0 = int(pos[0]), int(pos[1])
    # Compute text size.
    txt = class_str
    font = cv2.FONT_HERSHEY_SIMPLEX
    ((txt_w, txt_h), _) = cv2.getTextSize(txt, font, font_scale, 1)
    # Place text background.
    back_tl = x0, y0 - int(1.3 * txt_h)
    back_br = x0 + txt_w, y0
    cv2.rectangle(img, back_tl, back_br, _GREEN, -1)
    # Show text.
    txt_tl = x0, y0 - int(0.3 * txt_h)
    cv2.putText(img, txt, txt_tl, font, font_scale, _GRAY, lineType=cv2.LINE_AA)
    return img


def vis_bbox(img, bbox, thick=1):
    """Visualizes a bounding box."""
    (x0, y0, w, h) = bbox
    x1, y1 = int(x0 + w), int(y0 + h)
    x0, y0 = int(x0), int(y0)
    cv2.rectangle(img, (x0, y0), (x1, y1), _GREEN, thickness=thick)
    return img
    
def get_class_string(class_index, score, dataset):
    class_text = dataset.classes[class_index] if dataset is not None else \
        'id{:d}'.format(class_index)
    return class_text + ' {:0.2f}'.format(score).lstrip('0')
    
def main(args):
    
    cfg_file = r'/home/twang/Documents/detectron/configs/12_2017_baselines/e2e_mask_rcnn_R-101-FPN_2x.yaml'
    weights_file = r'/home/twang/Documents/detectron/model-weights/mask_rcnn_R-101-FPN_2x_model_final.pkl'
    
    video_dir = args.video_dir 
    print("video_dir",video_dir)
    
    video_name =os.path.basename(video_dir)
    video_name = os.path.splitext(video_name)[0]
    print("video_name",video_name)
    
    directory_box = os.path.join(os.path.join(r"/home/twang/Documents/HK-person",video_name),'box')
    print("directory_box",directory_box)
    os.makedirs(directory_box)
    
    directory_mask = os.path.join(os.path.join(r"/home/twang/Documents/HK-person",video_name),'mask')
    print("directory_mask",directory_mask)
    os.makedirs(directory_mask)
    
    merge_cfg_from_file(cfg_file)
    cfg.NUM_GPUS = 1
    weights = cache_url(weights_file, cfg.DOWNLOAD_CACHE)
    
    assert_and_infer_cfg()
    
    model = infer_engine.initialize_model_from_cfg(weights)
    
    dummy_coco_dataset = dummy_datasets.get_coco_dataset()
    

    cap = cv2.VideoCapture(video_dir)
    
    count= 0
    
    while cap.isOpened():
        
        ret, frame = cap.read()

        if not ret:
            break
        
        frame = cv2.resize(frame,dsize=(1280,720))   
        
        total_frame = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        
        current_frame = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
        Frame_step = 5
         
        if current_frame + Frame_step < total_frame:
            
            cap.set(cv2.CAP_PROP_POS_FRAMES, current_frame + Frame_step)
            
            timers = defaultdict(Timer)
    
            with c2_utils.NamedCudaScope(0):
                cls_boxes, cls_segms, cls_keyps = infer_engine.im_detect_all(model, frame, None, timers=timers) 
            
            thresh=0.9
    
            crop_box = True
            dataset=dummy_coco_dataset
            
            frame_for_box_crop = frame.copy()
            frame_for_mask = frame.copy()
            
            """Constructs a numpy array with the detections visualized."""
            if isinstance(cls_boxes, list):
                boxes, segms, keypoints, classes = convert_from_cls_format(cls_boxes, cls_segms, cls_keyps)
    
            if boxes is None or boxes.shape[0] == 0 or max(boxes[:, 4]) < thresh:
                return frame
    
            if segms is not None and len(segms) > 0:
                masks = mask_util.decode(segms)
                color_list = colormap()
                mask_color_id = 0
            
            # Display in largest to smallest order to reduce occlusion
            areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
            sorted_inds = np.argsort(-areas)
            
            for i in sorted_inds:
                bbox = boxes[i, :4]
                score = boxes[i, -1]
                if score < thresh:
                    continue   
                
                # crop each box 
                if crop_box:
                    #frame = vis_bbox(frame, (bbox[0], bbox[1], bbox[2] - bbox[0], bbox[3] - bbox[1]))
                    
                    (x1, y1, w, h) = (int(bbox[0]), int(bbox[1]), int(bbox[2] - bbox[0]), int(bbox[3] - bbox[1]))
                    x2 = x1 +w
                    y2 = y1 +h
                
                    cropped = frame_for_box_crop[y1:y2, x1:x2]
                    
                    cv2.imwrite("%s/person_Frame%i_%i.png"%(directory_box, current_frame, i), cropped)
    
                # crop each mask
                if segms is not None and len(segms) > i:
                    color_mask = color_list[mask_color_id % len(color_list), 0:3]
                    mask_color_id += 1
                    #frame = vis_mask(frame, masks[..., i], color_mask)
                    
                    (x1, y1, w, h) = (int(bbox[0]), int(bbox[1]), int(bbox[2] - bbox[0]), int(bbox[3] - bbox[1]))
                    x2 = x1 +w
                    y2 = y1 +h
                    
                    cropped_mask = masks[..., i]
                    
                    cropped_mask = cropped_mask[y1:y2, x1:x2]
                    cropped_img = frame_for_mask[y1:y2, x1:x2]
                    
                    cropped_img = vis_mask(cropped_img, cropped_mask, color_mask)
                    
                    cv2.imwrite("%s/person_Mask_Frame%i_%i.png"%(directory_mask, current_frame, i), cropped_img)
                    
            count +=1
            
            print("done:%i"%count)
            
        else:
            pass
        
                
    cap.release()    
    cv2.destroyAllWindows()            

if __name__ == '__main__':
    workspace.GlobalInit(['caffe2', '--caffe2_log_level=0'])
    args = parse_args()
    main(args)
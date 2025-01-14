#!/usr/bin/env python3
# -*- coding:utf-8 -*-
# Copyright (c) 2014-2021 Megvii Inc. All rights reserved.

import os

import cv2
import depthai as dai
import numpy as np
from depthai_sdk import toPlanar

__all__ = ["mkdir", "nms", "multiclass_nms", "demo_postprocess", "to_planar", "print_results", "run_nn"]


def mkdir(path):
    """
    Create a directory if it does not exist

    :param path: The path to the directory you want to create
    """
    if not os.path.exists(path):
        os.makedirs(path)


def nms(boxes, scores, nms_thr):
    """
    Given a list of boxes and a list of scores,
    return the indexes of the boxes you want to keep (peaks)

    :param boxes: a numpy array of shape (N, 4), where N is the number of boxes
    :param scores: a Numpy array of shape [N], representing N scores corresponding to N boxes
    :param nms_thr: threshold for NMS
    :return: The indices of the bounding boxes to keep.
    """
    """Single class NMS implemented in Numpy."""
    x1 = boxes[:, 0]
    y1 = boxes[:, 1]
    x2 = boxes[:, 2]
    y2 = boxes[:, 3]

    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = scores.argsort()[::-1]

    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])

        w = np.maximum(0.0, xx2 - xx1 + 1)
        h = np.maximum(0.0, yy2 - yy1 + 1)
        inter = w * h
        ovr = inter / (areas[i] + areas[order[1:]] - inter)

        inds = np.where(ovr <= nms_thr)[0]
        order = order[inds + 1]

    return keep


def multiclass_nms(boxes, scores, nms_thr, score_thr):
    """
    Given a set of bounding boxes, scores, and a threshold,
    this function will return the bounding boxes that have a score above the threshold,
    and also the score for that bounding box.

    :param boxes: a numpy array of shape (n, 4), each row is a box with [x1, y1, x2, y2] format
    :param scores: a numpy array of shape (N, C), where N is the number of boxes and C is the number of classes
    :param nms_thr: NMS IoU threshold
    :param score_thr: the threshold of detection confidence
    :return: a list of bounding boxes, where each bounding box is a list of coordinates, and each coordinate is a list of
    numbers.
    """
    """Multiclass NMS implemented in Numpy"""
    final_dets = []
    num_classes = scores.shape[1]
    for cls_ind in range(num_classes):
        cls_scores = scores[:, cls_ind]
        valid_score_mask = cls_scores > score_thr
        if valid_score_mask.sum() == 0:
            continue
        else:
            valid_scores = cls_scores[valid_score_mask]
            valid_boxes = boxes[valid_score_mask]
            keep = nms(valid_boxes, valid_scores, nms_thr)
            if len(keep) > 0:
                cls_inds = np.ones((len(keep), 1)) * cls_ind
                dets = np.concatenate([valid_boxes[keep], valid_scores[keep, None], cls_inds], 1)
                final_dets.append(dets)
    if len(final_dets) == 0:
        return None
    return np.concatenate(final_dets, 0)


def demo_postprocess(outputs, img_size, p6=False):
    """
    Given the outputs of the model, the image size, and whether or not the model is p6,
    return the postprocessed outputs

    :param outputs: The output of the model
    :param img_size: (height, width) of the input image
    :param p6: Whether to use P6 (P6 is the layer after the last feature map), defaults to False (optional)
    :return: The output of the model is a tensor of shape (batch_size, num_anchors, 4 + num_classes + 8), where the last
    axis contains the four box coordinates and the last eight are the respective confidence scores for each anchor box.
    """
    grids = []
    expanded_strides = []

    if not p6:
        strides = [8, 16, 32]
    else:
        strides = [8, 16, 32, 64]

    hsizes = [img_size[0] // stride for stride in strides]
    wsizes = [img_size[1] // stride for stride in strides]

    for hsize, wsize, stride in zip(hsizes, wsizes, strides):
        xv, yv = np.meshgrid(np.arange(hsize), np.arange(wsize))
        grid = np.stack((xv, yv), 2).reshape((1, -1, 2))
        grids.append(grid)
        shape = grid.shape[:2]
        expanded_strides.append(np.full((*shape, 1), stride))

    grids = np.concatenate(grids, 1)
    expanded_strides = np.concatenate(expanded_strides, 1)
    outputs[..., :2] = (outputs[..., :2] + grids) * expanded_strides
    outputs[..., 2:4] = np.exp(outputs[..., 2:4]) * expanded_strides

    return outputs


def to_planar(arr: np.ndarray, input_size: tuple = None) -> np.ndarray:
    """
    Given an image, resize it to the given size, and pad it to the given size if necessary.

    The image is expected to be in HWC format.


    :param arr: The image to be converted to a planar image
    :type arr: np.ndarray
    :param input_size: tuple of ints (H, W)
    :type input_size: tuple
    :return: padding image
    """
    if input_size is None or tuple(arr.shape[:2]) == input_size:
        return arr.transpose((2, 0, 1))

    input_size = np.array(input_size)
    if len(arr.shape) == 3:
        padded_img = np.ones((input_size[0], input_size[1], 3)) * 114.0
    else:
        padded_img = np.ones(input_size) * 114.0
    img = np.array(arr)
    r = min(input_size / img.shape[:2])
    resize_ = (np.array(img.shape[:2]) * r).astype(int)
    resized_img = cv2.resize(
        img,
        tuple(resize_[::-1]),
        interpolation=cv2.INTER_LINEAR,
    )
    padding = (input_size - resize_) // 2
    padded_img[
    padding[0]: padding[0] + int(img.shape[0] * r),
    padding[1]: padding[1] + int(img.shape[1] * r),
    ] = resized_img
    image = padded_img.transpose(2, 0, 1)
    return image


def print_results(result, data=False):
    """
    Prints the shape of the tensors in the result dictionary

    :param result: the output of the model
    :param data: A dictionary of data to pass to the model, defaults to False (optional)
    """
    for i in result:
        print(i, result[i].shape)
        if data:
            print(result[i])


def run_nn(img, input_queue, width, height):
    """
    It takes an image, converts it to a planar image, and sends it to the input queue

    :param img: The image to be processed
    :param input_queue: The input queue to the neural network
    :param width: The width of the image in pixels
    :param height: The height of the image in pixels
    """
    frameNn = dai.ImgFrame()
    frameNn.setType(dai.ImgFrame.Type.BGR888p)
    frameNn.setWidth(width)
    frameNn.setHeight(height)
    frameNn.setData(toPlanar(img, (height, width)))
    input_queue.send(frameNn)

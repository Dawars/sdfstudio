# Copyright 2022 The Nerfstudio Team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Dataset.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Dict, List

import numpy as np
import numpy.typing as npt
import torch
from PIL import Image
from rich.progress import Console, track
from torch.utils.data import Dataset
from torchtyping import TensorType

from nerfstudio.data.dataparsers.base_dataparser import DataparserOutputs
from nerfstudio.data.datasets.base_dataset import InputDataset
from nerfstudio.data.utils.data_utils import get_image_mask_tensor_from_path, get_depth_image_from_path, \
    get_normal_image_from_path
from nerfstudio.utils.images import BasicImages


class GeneralizedDataset(InputDataset):
    """Dataset that returns images, possibly of different sizes.

    The only thing that separates this from the inputdataset is that this will return
    image / mask tensors inside a list, meaning when collate receives the images, it will
    simply concatenate the lists together. The concatenation of images of different sizes would
    fail otherwise.

    Args:
        dataparser_outputs: description of where and how to read input images.
    """

    def __init__(self, dataparser_outputs: DataparserOutputs, scale_factor: float = 1.0):
        super().__init__(dataparser_outputs, scale_factor)

        h = None
        w = None
        all_hw_same = True
        for filename in track(
            self._dataparser_outputs.image_filenames, transient=True, description="Checking image sizes"
        ):
            image = Image.open(filename)
            if h is None:
                h = image.height
                w = image.width

            if image.height != h or image.width != w:
                all_hw_same = False
                break

        self.all_hw_same = all_hw_same

        self.depth_unit_scale_factor = self.metadata.get("depth_unit_scale_factor", 0.)

    def get_data(self, image_idx: int) -> Dict:
        """Returns the ImageDataset data as a dictionary.

        Args:
            image_idx: The image index in the dataset.
        """
        # If all images are the same size, we can just return the image and mask tensors in a regular way
        # if self.all_hw_same:  # todo additional data not impe
        #     return super().get_data(image_idx)

        # Otherwise return them in a custom struct
        if image_idx in self.image_cache:
            image = self.image_cache[image_idx]
        else:
            image = self.get_image(image_idx)
            self.image_cache[image_idx] = image

        data = {"image_idx": image_idx}
        data["is_gray"] = BasicImages([torch.ones_like(image[..., :1]) * image.shape[-1] == 1])
        # data["is_gray"] = BasicImages([torch.zeros_like(image[..., :1])])  # uncomment to disable grayscale
        if image.shape[-1] == 1:
            image = image.tile(1, 1, 3)
        data["image"] = BasicImages([image])
        for key, data_func_dict in self._dataparser_outputs.metadata.items():
            if isinstance(data_func_dict, dict) and "func" in data_func_dict:
                func = data_func_dict["func"]
                assert "kwargs" in data_func_dict, "No data to process: specify `kwargs` in `additional_inputs`"
                data.update(func(image_idx, **data_func_dict["kwargs"]))
        if self.has_masks:
            mask_filepath = self._dataparser_outputs.mask_filenames[image_idx]
            mask_image = get_image_mask_tensor_from_path(filepath=mask_filepath, scale_factor=self.scale_factor)
            assert (
                    mask_image.shape[:2] == image.shape[:2]
            ), f"Mask and image have different shapes. Got {mask_image.shape[:2]} and {image.shape[:2]}"

            # save nonzero_indices so that we only compute it once
            nonzero_indices = torch.nonzero(mask_image[..., 0], as_tuple=False)
            mask_tensor = nonzero_indices
            assert len(mask_tensor) > 0
            data["mask"] = BasicImages([mask_tensor])
        metadata = self.get_metadata(data)
        data.update(metadata)
        return data

    def get_metadata(self, data: Dict) -> Dict:
        metadata = {}

        image_idx = data["image_idx"]
        height, width, c = data["image"].images[0].shape

        if "depth_filenames" in self.metadata:
            depth_filepath = self.metadata["depth_filenames"][image_idx]

            # Scale depth images to meter units and also by scaling applied to cameras
            scale_factor = self.depth_unit_scale_factor * self._dataparser_outputs.dataparser_scale
            depth_image = get_depth_image_from_path(
                filepath=depth_filepath, height=height, width=width, scale_factor=scale_factor
            )

            metadata["depth_image"] = BasicImages([depth_image])  # [W, H, 1] ??

        if "sensor_filenames" in self.metadata:
            sensor_filepath = self.metadata["sensor_filenames"][image_idx]

            # Scale depth images to meter units and also by scaling applied to cameras
            scale_factor = self.depth_unit_scale_factor
            sensor_image = get_depth_image_from_path(
                filepath=sensor_filepath, height=height, width=width, scale_factor=scale_factor
            )

            metadata["sensor_depth"] = BasicImages([sensor_image])  # [W, H, 1] ??

        if "sensor_filenames" in self.metadata and "depth_filenames" in self.metadata:
            # scale depth
            mask = (sensor_image > 0.0).squeeze()
            # scale * depth_pred + offset * 1 - depth_gt = 0
            D = torch.cat((depth_image[mask], torch.ones_like(depth_image[mask])), dim=1)
            Sb = torch.linalg.lstsq(D, sensor_image[mask]).solution

            metadata["depth_image"] = BasicImages([depth_image * Sb[0] + Sb[1]])  # [W, H, 1] ??

        if "normal_filenames" in self.metadata:
            normal_filepath = self.metadata["normal_filenames"][image_idx]

            camera_to_world = self._dataparser_outputs.cameras.camera_to_worlds[image_idx]
            normal_image = get_normal_image_from_path(
                filepath=normal_filepath, height=height, width=width, camera_to_world=camera_to_world
            )
            metadata["normal_image"] = BasicImages([normal_image])

        return metadata

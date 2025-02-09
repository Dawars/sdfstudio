import blenderproc as bproc

import argparse

import pandas as pd
import yaml

"""
Script to generate sky mask, depth and normal maps using blender
Reads the colmap model and sets the camera
"""

from typing import Optional
from pathlib import Path
import numpy as np
import sys

from PIL import Image
from blenderproc.python.utility.Utility import UndoAfterExecution
from blenderproc.scripts.saveAsImg import save_array_as_image
from matplotlib import pyplot as plt, cm

sdfstudio_dir = Path("./")

sys.path.insert(0, str(sdfstudio_dir))

from nerfstudio.data.utils.colmap_utils import read_cameras_binary, read_images_binary


# import pydevd_pycharm

# pydevd_pycharm.settrace("localhost", port=12345, stdoutToServer=True, stderrToServer=True)


def apply_colormap(image, cmap="viridis"):
    """Convert single channel to a color image.

    Args:
        image: Single channel image.
        cmap: Colormap for image.

    Returns:
        TensorType: Colored image
    """

    colormap = plt.colormaps[cmap]
    image = np.nan_to_num(image, nan=0)
    image_long = (255 * np.array(image)).astype("int")
    image_long_min = np.min(image_long)
    image_long_max = np.max(image_long)
    assert image_long_min >= 0, f"the min value is {image_long_min}"
    assert image_long_max <= 255, f"the max value is {image_long_max}"
    return np.array(colormap.colors)[image_long]


def apply_depth_colormap(
    depth, accumulation, near_plane: Optional[float] = None, far_plane: Optional[float] = None, cmap="turbo"
):
    """Converts a depth image to color for easier analysis.

    Args:
        depth: Depth image.
        accumulation: Ray accumulation used for masking vis.
        near_plane: Closest depth to consider. If None, use min image value.
        far_plane: Furthest depth to consider. If None, use max image value.
        cmap: Colormap to apply.

    Returns:
        Colored depth image
    """

    near_plane = near_plane or float(np.min(depth))
    print(f"Min plane", near_plane)
    far_plane = far_plane or float(np.max(depth))

    depth = (depth - near_plane) / (far_plane - near_plane + 1e-10)
    depth = np.clip(depth, 0, 1)

    colored_image = apply_colormap(depth, cmap=cmap)

    if accumulation is not None:
        colored_image = colored_image * accumulation + (1 - accumulation)

    return colored_image


bproc.init()
bproc.renderer.enable_depth_output(
    activate_antialiasing=False,
)

import bpy


def render_scene(scene_name, resolution: int):
    for training_path in (sdfstudio_dir / "outputs" / scene_name).rglob("./**/nerfstudio_models/"):
        print(training_path)

        bproc.clean_up(clean_up_camera=True)
        devices = bpy.context.preferences.addons["cycles"].preferences.get_devices_for_type("CUDA")
        print(devices)
        cuda_ids = [i for i, device in enumerate(devices) if "NVIDIA" in device.name]
        cpu_ids = [i for i, device in enumerate(devices) if "CPU" in device.name or "AMD" in device.name]
        print("cuda ids", cuda_ids)
        print("cpu ids", cpu_ids)

        bproc.renderer.set_render_devices(desired_gpu_ids=cuda_ids[:1] + cpu_ids)

        ckpt_file = sorted(list(training_path.glob("*.ckpt")))[-1]
        render_dir = training_path.parent / f"renders_{resolution}"
        if render_dir.exists():
            print(f"Skipping {render_dir} already exists")
            continue
        render_dir.mkdir(exist_ok=True)
        config_file = ckpt_file.parent.with_name("config.yml")
        config = yaml.load(config_file.read_text(), Loader=yaml.Loader)

        setting = config.pipeline.datamanager.dataparser.setting
        setting_suffix = "" if setting == "" else f"_{setting}"
        data_path = sdfstudio_dir / config.pipeline.datamanager.dataparser.data
        image_list = list(data_path.glob(f"*{setting_suffix}.tsv"))[0]
        files = pd.read_csv(image_list, sep="\t")
        files = files[files["split"] == "test"]
        files.reset_index(inplace=True, drop=True)
        file_list = set(files["filename"])
        print(file_list)

        bproc.renderer.set_world_background([1, 1, 1], strength=35)

        # multiplier = 10

        mesh_path = list(config_file.parent.glob(f"*{resolution}_sfm.ply"))
        if not mesh_path:
            print(f"no mesh for {training_path}")
            continue
        print(f"Loading pcd {mesh_path}")
        pcd = bproc.loader.load_obj(str(mesh_path[0]))
        # pcd[0].set_scale([multiplier, multiplier, multiplier], 1)
        print("Loading pcd done")

        # load colmap cameras and visualize as spheres
        camdata = read_cameras_binary(str(data_path / "dense/sparse/cameras.bin"))
        imdata = read_images_binary(str(data_path / "dense/sparse/images.bin"))

        bottom = np.array([0, 0, 0, 1.0]).reshape(1, 4)

        # load tsv with test set

        for v in imdata.values():
            filename = v.name
            if filename not in file_list:
                continue
            print(filename)

            R = v.qvec2rotmat()
            t = v.tvec.reshape(3, 1)
            w2c = np.concatenate([np.concatenate([R, t], 1), bottom], 0)
            pose = np.linalg.inv(w2c)
            # pose[:3, 3:4] *= multiplier
            pose = bproc.math.change_source_coordinate_frame_of_transformation_matrix(pose, ["X", "-Y", "-Z"])

            img_id = v.id
            cam_id = v.camera_id

            cam = camdata[cam_id]

            fx = cam.params[0]
            fy = cam.params[1]
            cx = cam.params[2]
            cy = cam.params[3]

            bproc.utility.reset_keyframes()
            with UndoAfterExecution():
                #            if True:
                # define the camera resolution
                K = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]])
                bproc.camera.set_intrinsics_from_K_matrix(K, cam.width, cam.height)
                bproc.camera.add_camera_pose(pose)

                bproc.renderer.enable_normals_output()

                data = bproc.renderer.render(
                    load_keys={
                        "distance",
                        "depth",
                        "normals",
                    },
                    output_key="colors",
                )
                print(data.keys())

                depth_map = data["depth"][0]
                sky_mask = depth_map != 1e10
                # depth_map[sky_mask] /= multiplier
                if depth_map[sky_mask].any():
                    max_depth = depth_map[sky_mask].max() + 1e-5
                else:
                    continue
                print(max_depth)

                plt.imsave(
                    str(render_dir / f"{filename}_depth.png"),
                    apply_depth_colormap(data["depth"][0], far_plane=max_depth, accumulation=np.expand_dims(sky_mask, -1)),
                )
                plt.close()

                mask = Image.frombytes(mode="1", size=sky_mask.shape[::-1], data=np.packbits(sky_mask, axis=1))
                mask.save(str(render_dir / f"{filename}_mask.png"))

                normals = data["normals"][0].clip(0, 1)
                normals[~sky_mask] = 1  # bg to white
                save_array_as_image(normals, "normals", str(render_dir / f"{filename}_normals.png"))
                save_array_as_image(data["colors"][0], "colors", str(render_dir / f"{filename}_color.png"))

                # np.save(str(render_dir / f"{filename}_depth.npy"), data["depth"][0])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument("--name", required=True, help="reconstruction name")
    parser.add_argument("--resolution", type=int, default=512, help="Grid resolution")

    args = parser.parse_args()

    render_scene(args.name, args.resolution)

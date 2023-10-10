# ns-extract-mesh --load-config outputs/neus-facto-dtu65/neus-facto/XXX/config.yml --output-path meshes/neus-facto-dtu65.ply
import argparse
import json
from pathlib import Path

import numpy as np
import trimesh
import yaml
from tqdm import tqdm

from scripts.extract_mesh import ExtractMesh

save_path = Path("./outputs")


def extract_meshes(scene_name: str, simplify=False, resolution=1024):
    for training_path in tqdm(save_path.rglob(f"{scene_name}/**/nerfstudio_models/")):
        ckpt_file = sorted(list(training_path.glob("*.ckpt")))[-1]
        config_file = ckpt_file.parent.with_name("config.yml")
        out_path = config_file.with_name(f"mesh_{resolution}.ply")

        data_transform_path = ckpt_file.parent.with_name("dataparser_transforms.json")

        data_transform = json.loads(data_transform_path.read_text())

        config = yaml.load(config_file.read_text(), Loader=yaml.Loader)

        setting = config.pipeline.datamanager.dataparser.setting
        setting_suffix = "" if setting == "" else f"_{setting}"
        data_path = config.pipeline.datamanager.dataparser.data

        scene_config = yaml.load((data_path / f"config{setting_suffix}.yaml").read_text(), Loader=yaml.FullLoader)
        transform = np.array([*data_transform["transform"], [0.0, 0.0, 0.0, 1.0]])
        scale = data_transform["scale"]

        # origin = np.array([-0.579314, -0.579314, -0.579314, 1])
        # bb_min_world = origin - np.array([1.82, 1.32, 2.28, 0])
        # bb_max_world = origin + np.array([1.82, 1.32, 2.28, 0])
        #
        # bb_min = (bb_min_world @ transform) * scale
        # bb_max = (bb_max_world @ transform) * scale

        extract_mesh = ExtractMesh(
            config_file,
            resolution=resolution,
            output_path=out_path,
            simplify_mesh=simplify,
            bounding_box_min=(
                -0.5,
                -0.5,
                -0.5,
            ),
            bounding_box_max=(0.5, 0.5, 0.5),
        )
        extract_mesh.main()

        del extract_mesh

        if simplify:
            mesh = trimesh.load_mesh(str(out_path).replace(".ply", "-simplify.ply"))
        else:
            mesh = trimesh.load_mesh(out_path)
        mesh.apply_transform(np.linalg.inv(transform))
        mesh.apply_scale(1 / scale)
        mesh.apply_translation(np.array(scene_config["origin"]))  # add back origin

        mesh.export(config_file.with_name(f"mesh_{resolution}_sfm.ply"))
        if "sfm2gt" in scene_config:
            mesh.apply_transform(np.array(scene_config["sfm2gt"]))
            mesh.export(config_file.with_name(f"mesh_{resolution}_gt.ply"))

        print(ckpt_file)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument("--name", required=True, help="reconstruction name")
    parser.add_argument("--simplify", default=False, help="Simplify mesh (requires MeshLab)")
    parser.add_argument("--resolution", type=int, default=512, help="Grid resolution")

    args = parser.parse_args()

    extract_meshes(args.name, args.simplify, args.resolution)
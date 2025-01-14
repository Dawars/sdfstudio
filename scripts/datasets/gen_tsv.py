import os

dataset_name = "semperoper"
path = f"/media/dawars/Ventoy/dresden/{dataset_name}"
images = f"{path}/dense/images"
out_path = f"{path}/{dataset_name}.tsv"

db = []

header = ["filename", "id", "split", "dataset"]

for image in os.listdir(images):
    db.append([image, "-1", "train", dataset_name])

with open(out_path, "w") as f:
    f.write("\t".join(header))
    f.write("\n")

    for data in db:
        f.write("\t".join(data))
        f.write("\n")

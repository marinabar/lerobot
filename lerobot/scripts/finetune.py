from lerobot.scripts.train import train
from lerobot.scripts.eval import get_pretrained_policy_path
from lerobot.common.utils.utils import init_hydra_config
import shutil
import hydra
from pathlib import Path
import os


policy_repo_id = "lerobot/diffusion_pusht"
output_dir = "outputs/test_mps_operations"

def create_symlink_to_latest_checkpoint(base_dir: Path):
    # Define the new directory and symlink paths
    new_dir = base_dir / "100000"
    symlink_path = base_dir / "last"

    # Create a symbolic link named 'last' pointing to '100000'
    if symlink_path.exists() or symlink_path.is_symlink():
        symlink_path.unlink()  # Remove existing symlink or directory

    os.symlink(new_dir, symlink_path)

    print(f"Directory '{new_dir}' created.")
    print(f"Symlink '{symlink_path}' created pointing to '{new_dir}'.")

def prepare_checkpoint_dir(pretrained_policy_name_or_path, output_folder):
    pretrained_policy_path = get_pretrained_policy_path(pretrained_policy_name_or_path)
    last_checkpoint_dir = Path(output_folder) / "last"
    last_checkpoint_dir.mkdir(parents=True, exist_ok=True)
    
    for item in pretrained_policy_path.iterdir():
        dest = last_checkpoint_dir / item.name
        if item.is_file():
            shutil.copy(item, dest)
        else:
            shutil.copytree(item, dest)
    
    return last_checkpoint_dir

overrides = ["device=mps", "training.offline_steps=110000", "resume=true"]


def main():
    hydra_cfg = init_hydra_config(str(Path(output_dir)/ "checkpoints" / "last" / "pretrained_model" / "config.yaml"), overrides=overrides)
    train(hydra_cfg, output_dir, job_name="mps_test")

if __name__ == "__main__":
    main()
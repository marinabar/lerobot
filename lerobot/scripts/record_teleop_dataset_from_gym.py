"""This script demonstrates how to record a LeRobot dataset of training data using a very simple gym environment.
"""

import argparse
import copy
import os
import sys
import pathlib
import time
import importlib

import gymnasium as gym
import numpy as np
import torch

from datasets import Dataset, Features, Sequence, Value
from lerobot.common.datasets.compute_stats import compute_stats
from lerobot.common.datasets.lerobot_dataset import CODEBASE_VERSION, DATA_DIR, LeRobotDataset
from lerobot.common.datasets.push_dataset_to_hub.utils import concatenate_episodes, save_images_concurrently
from lerobot.common.datasets.utils import (
    hf_transform_to_torch,
)
from lerobot.common.datasets.video_utils import VideoFrame, encode_video_frames
from lerobot.scripts.push_dataset_to_hub import push_meta_data_to_hub, push_videos_to_hub, save_meta_data
from tqdm import tqdm


def process_args():
    # parse the repo_id name via command line
    parser = argparse.ArgumentParser()
    parser.add_argument("--module-name", type=str, default="gym_lowcostrobot")
    parser.add_argument("--env-name", type=str, default="ReachCube-v0")
    parser.add_argument("--num-episodes", type=int, default=2)
    parser.add_argument("--num-steps", type=int, default=20)
    parser.add_argument("--num-workers", type=int, default=8)
    parser.add_argument("--keep-last", action="store_true")
    parser.add_argument("--repo-id", type=str, default="myrepo")
    parser.add_argument("--push-to-hub", action="store_true")
    parser.add_argument("--fps", type=np.float32, default=30,
                        help="Frames (steps) per second of the recording (will be ignored if `step()` info provides timestamp).")

    parser.add_argument(
        "--image-keys",
        type=str,
        default="image_top,image_front",
        help="The keys of the image observations to record.",
    )
    parser.add_argument(
        "--state-keys",
        type=str,
        default="arm_qpos,arm_qvel,cube_pos",
        help="The keys of the state observations to record.",
    )

    parser.add_argument(
        "--revision", type=str, default=CODEBASE_VERSION, help="Codebase version used to generate the dataset."
    )

    return parser.parse_args()


if __name__ == "__main__":
    args = process_args()

    repo_id = args.repo_id
    num_episodes = args.num_episodes
    num_steps = args.num_steps
    revision = args.revision

    if DATA_DIR == None:
        DATA_DIR = pathlib.Path("data_traces")
        print("Warning: env variable DATA_DIR was not set, defaulting to './{}'.".format(DATA_DIR))

    out_data = DATA_DIR / repo_id

    # During data collection, frames are stored as png images in `images_dir`
    images_dir = out_data / "images"

    # After data collection, png images of each episode are encoded into a mp4 file stored in `videos_dir`
    videos_dir = out_data / "videos"
    meta_data_dir = out_data / "meta_data"

    # Create image and video directories
    if not os.path.exists(images_dir):
        os.makedirs(images_dir, exist_ok=True)
    if not os.path.exists(videos_dir):
        os.makedirs(videos_dir, exist_ok=True)

    # import the gym module containing the environment
    try:
        # because we want to import using a variable, do it this way
        module_obj = __import__(args.module_name)
        # create a global object containging our module
        globals()[args.module_name] = module_obj
    except ImportError:
        sys.stderr.write("ERROR: missing python module: " +
                         args.module_name + "\n")
        sys.exit(1)

    # Create the gym environment - check the kwargs in gym_real_world/gym_environment.py
    env = gym.make(args.env_name, disable_env_checker=True,
                   observation_mode="both", action_mode="joint", render_mode="human")

    ep_dicts = []
    episode_data_index = {"from": [], "to": []}
    ep_fps = []
    id_from = 0
    id_to = 0
    print(f"gym environment created")

    ep_idx = 0
    while ep_idx < num_episodes:
        # bring the follower to the leader and start camera
        env.reset()

        print(f"go {ep_idx}")

        # init buffers
        obs_replay = {k: [] for k in env.observation_space}
        obs_replay["action"] = []

        timestamps = []
        drop_episode = False
        for i in tqdm(range(num_steps)):
            # Apply the next action
            action = env.action_space.sample()
            observation, _, _, _, info = env.step(action=action)

            # store data
            for key in observation:
                obs_replay[key].append(copy.deepcopy(observation[key]))
            obs_replay["action"].append(copy.deepcopy(action))

            # Use simulator time, if available.
            try:
                timestamps.append(info["timestamp"])
            except KeyError:
                timestamps.append(np.float32(i)/args.fps)

        print("stop")

        if not drop_episode:
            print(f"saving episode {ep_idx}")
            ep_dict = {}

            # store images in png and create the video
            for img_key in args.image_keys.split(","):
                save_images_concurrently(
                    obs_replay[img_key],
                    images_dir / f"{img_key}_episode_{ep_idx:06d}",
                    args.num_workers,
                )
                fname = f"{img_key}_episode_{ep_idx:06d}.mp4"

                # store the reference to the video frame
                ep_dict[f"observation.{img_key}"] = [
                    {"path": f"videos/{fname}", "timestamp": tstp} for tstp in timestamps]

            states = []
            for state_name in args.state_keys.split(","):
                states.append(np.array(obs_replay[state_name]))
            state = torch.tensor(np.concatenate(states, axis=1))

            action = torch.tensor(np.array(obs_replay["action"]))
            next_done = torch.zeros(num_steps, dtype=torch.bool)
            next_done[-1] = True

            ep_dict["observation.state"] = state
            ep_dict["action"] = action
            ep_dict["episode_index"] = torch.tensor(
                [ep_idx] * num_steps, dtype=torch.int64)
            ep_dict["frame_index"] = torch.arange(0, num_steps, 1)
            ep_dict["timestamp"] = torch.tensor(timestamps)
            ep_dict["next.done"] = next_done

            print(f"num_steps={num_steps}")
            print(f"timestamps[-1]={timestamps[-1]}")
            ep_fps.append(num_steps / timestamps[-1])
            ep_dicts.append(ep_dict)
            print(f"Episode {ep_idx} done, fps: {ep_fps[-1]:.2f}")

            episode_data_index["from"].append(id_from)
            episode_data_index["to"].append(
                id_from + num_steps if args.keep_last else id_from + num_steps - 1)

            id_to = id_from + num_steps if args.keep_last else id_from + num_steps - 1
            id_from = id_to

            ep_idx += 1

    env.close()

    print("encode video frames")
    for ep_idx in range(num_episodes):
        for img_key in args.image_keys.split(","):
            encode_video_frames(
                imgs_dir=images_dir / f"{img_key}_episode_{ep_idx:06d}",
                video_path=videos_dir / f"{img_key}_episode_{ep_idx:06d}.mp4",
                fps=ep_fps[ep_idx],
            )

    print("concatenate episodes")
    # Since our fps varies we are sometimes off tolerance for the last frame
    data_dict = concatenate_episodes(ep_dicts)

    features = {}

    keys = [key for key in data_dict if "observation.image_" in key]
    for key in keys:
        features[key.replace("observation.image_",
                             "observation.images.")] = VideoFrame()
        data_dict[key.replace("observation.image_",
                              "observation.images.")] = data_dict[key]
        del data_dict[key]

    features["observation.state"] = Sequence(
        length=data_dict["observation.state"].shape[1], feature=Value(
            dtype="float32", id=None)
    )
    features["action"] = Sequence(
        length=data_dict["action"].shape[1], feature=Value(dtype="float32", id=None))
    features["episode_index"] = Value(dtype="int64", id=None)
    features["frame_index"] = Value(dtype="int64", id=None)
    features["timestamp"] = Value(dtype="float32", id=None)
    features["next.done"] = Value(dtype="bool", id=None)
    features["index"] = Value(dtype="int64", id=None)

    hf_dataset = Dataset.from_dict(data_dict, features=Features(features))
    hf_dataset.set_transform(hf_transform_to_torch)

    info = {
        # to have a good tolerance in data processing for the slowest video
        "fps": sum(ep_fps) / len(ep_fps),
        "video": 1,
    }

    print("from preloaded")
    lerobot_dataset = LeRobotDataset.from_preloaded(
        repo_id=repo_id,
        hf_dataset=hf_dataset,
        episode_data_index=episode_data_index,
        info=info,
        videos_dir=videos_dir,
    )

    print("compute stats")
    stats = compute_stats(lerobot_dataset, num_workers=args.num_workers)

    print("save to disk")
    # to remove transforms that cant be saved
    hf_dataset = hf_dataset.with_format(None)
    hf_dataset.save_to_disk(str(out_data / "train"))

    save_meta_data(info, stats, episode_data_index, meta_data_dir)

    if args.push_to_hub:
        print(f"Pushing dataset to '{repo_id}'")
        hf_dataset.push_to_hub(repo_id, token=True, revision="main")
        hf_dataset.push_to_hub(repo_id, token=True, revision=revision)

        push_meta_data_to_hub(repo_id, meta_data_dir, revision="main")
        push_meta_data_to_hub(repo_id, meta_data_dir, revision=revision)

        push_videos_to_hub(repo_id, videos_dir, revision="main")
        push_videos_to_hub(repo_id, videos_dir, revision=revision)
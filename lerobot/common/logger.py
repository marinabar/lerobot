import contextlib
import os
from pathlib import Path

import numpy as np
from omegaconf import OmegaConf
from termcolor import colored


def make_dir(dir_path):
    """Create directory if it does not already exist."""
    with contextlib.suppress(OSError):
        dir_path.mkdir(parents=True, exist_ok=True)
    return dir_path


def print_run(cfg, reward=None):
    """Pretty-printing of run information. Call at start of training."""
    prefix, color, attrs = "  ", "green", ["bold"]

    def limstr(s, maxlen=32):
        return str(s[:maxlen]) + "..." if len(str(s)) > maxlen else s

    def pprint(k, v):
        print(
            prefix + colored(f'{k.capitalize() + ":":<16}', color, attrs=attrs),
            limstr(v),
        )

    kvs = [
        ("task", cfg.env.task),
        ("offline_steps", f"{cfg.offline_steps}"),
        ("online_steps", f"{cfg.online_steps}"),
        ("action_repeat", f"{cfg.env.action_repeat}"),
        # ('observations', 'x'.join([str(s) for s in cfg.obs_shape])),
        # ('actions', cfg.action_dim),
        # ('experiment', cfg.exp_name),
    ]
    if reward is not None:
        kvs.append(("episode reward", colored(str(int(reward)), "white", attrs=["bold"])))
    w = np.max([len(limstr(str(kv[1]))) for kv in kvs]) + 21
    div = "-" * w
    print(div)
    for k, v in kvs:
        pprint(k, v)
    print(div)


def cfg_to_group(cfg, return_list=False):
    """Return a wandb-safe group name for logging. Optionally returns group name as list."""
    # lst = [cfg.task, cfg.modality, re.sub("[^0-9a-zA-Z]+", "-", cfg.exp_name)]
    lst = [
        f"env:{cfg.env.name}",
        f"seed:{cfg.seed}",
    ]
    return lst if return_list else "-".join(lst)


class Logger:
    """Primary logger object. Logs either locally or using wandb."""

    def __init__(self, log_dir, job_name, cfg):
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._job_name = job_name
        self._model_dir = self._log_dir / "models"
        self._buffer_dir = self._log_dir / "buffers"
        self._save_model = cfg.save_model
        self._save_buffer = cfg.save_buffer
        self._group = cfg_to_group(cfg)
        self._seed = cfg.seed
        self._cfg = cfg
        self._eval = []
        print_run(cfg)
        project = cfg.get("wandb", {}).get("project")
        entity = cfg.get("wandb", {}).get("entity")
        enable_wandb = cfg.get("wandb", {}).get("enable", False)
        run_offline = not enable_wandb or not project or not entity
        if run_offline:
            print(colored("Logs will be saved locally.", "yellow", attrs=["bold"]))
            self._wandb = None
        else:
            os.environ["WANDB_SILENT"] = "true"
            import wandb

            wandb.init(
                project=project,
                entity=entity,
                name=job_name,
                notes=cfg.get("wandb", {}).get("notes"),
                # group=self._group,
                tags=cfg_to_group(cfg, return_list=True),
                dir=self._log_dir,
                config=OmegaConf.to_container(cfg, resolve=True),
                # TODO(rcadene): try set to True
                save_code=False,
                # TODO(rcadene): split train and eval, and run async eval with job_type="eval"
                job_type="train_eval",
                # TODO(rcadene): add resume option
                resume=None,
            )
            print(colored("Logs will be synced with wandb.", "blue", attrs=["bold"]))
            self._wandb = wandb

    def save_model(self, policy, identifier):
        if self._save_model:
            self._model_dir.mkdir(parents=True, exist_ok=True)
            fp = self._model_dir / f"{str(identifier)}.pt"
            policy.save(fp)
            if self._wandb:
                artifact = self._wandb.Artifact(
                    self._group + "-" + str(self._seed) + "-" + str(identifier),
                    type="model",
                )
                artifact.add_file(fp)
                self._wandb.log_artifact(artifact)

    def save_buffer(self, buffer, identifier):
        self._buffer_dir.mkdir(parents=True, exist_ok=True)
        fp = self._buffer_dir / f"{str(identifier)}.pkl"
        buffer.save(fp)
        if self._wandb:
            artifact = self._wandb.Artifact(
                self._group + "-" + str(self._seed) + "-" + str(identifier),
                type="buffer",
            )
            artifact.add_file(fp)
            self._wandb.log_artifact(artifact)

    def finish(self, agent, buffer):
        if self._save_model:
            self.save_model(agent, identifier="final")
        if self._save_buffer:
            self.save_buffer(buffer, identifier="buffer")
        if self._wandb:
            self._wandb.finish()
        print_run(self._cfg, self._eval[-1][-1])

    def log_dict(self, d, step, mode="train"):
        assert mode in {"train", "eval"}
        if self._wandb is not None:
            for k, v in d.items():
                self._wandb.log({f"{mode}/{k}": v}, step=step)

    def log_video(self, video, step, mode="train"):
        assert mode in {"train", "eval"}
        wandb_video = self._wandb.Video(video, fps=self.cfg.fps, format="mp4")
        self._wandb.log({f"{mode}/video": wandb_video}, step=step)
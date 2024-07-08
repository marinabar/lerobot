import importlib

import gymnasium as gym


def make_env(cfg: dict, n_envs: int | None = None) -> gym.vector.VectorEnv:
    """Makes a gym vector environment according to the evaluation config.

    n_envs can be used to override eval.batch_size in the configuration. Must be at least 1.
    """
    if n_envs is not None and n_envs < 1:
        raise ValueError("`n_envs must be at least 1")
    name = cfg['env']['name']
    package_name = f"gym_{cfg.env.name}"

    try:
        importlib.import_module(package_name)
    except ModuleNotFoundError as e:
        print(
            f"{package_name} is not installed. Please install it with `pip install 'lerobot[{cfg.env.name}]'`"
        )
        raise e

    gym_handle = f"{package_name}/{cfg.env.task}"
    gym_kwgs = dict(cfg.env.get("gym", {}))

    if cfg.env.get("episode_length"):
        gym_kwgs["max_episode_steps"] = cfg.env.episode_length

    if n_envs == 1 : 
        # Create a single instance of the environment
        env = gym.make(gym_handle, disable_env_checker=True, **gym_kwgs)
    else:     # batched version of the env that returns an observation of shape (b, c)
        env_cls = gym.vector.AsyncVectorEnv if cfg.eval.use_async_envs else gym.vector.SyncVectorEnv
        env = env_cls(
            [
                lambda: gym.make(gym_handle, disable_env_checker=True, **gym_kwgs)
                for _ in range(n_envs if n_envs is not None else cfg.eval.batch_size)
            ]
        )

    return env
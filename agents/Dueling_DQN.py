import numpy as np

import torch

from agents.DQN import Agent as DQN_Agent
from networks.networks import DuelingDQN

class Agent(DQN_Agent):
    def __init__(self, static_policy=False, env=None, config=None, log_dir='/tmp/gym'):
        super(Agent, self).__init__(static_policy, env, config, log_dir=log_dir)

    def declare_networks(self):
        self.model = DuelingDQN(self.env.observation_space.shape, self.env.action_space.n, noisy=self.noisy, sigma_init=self.sigma_init)
        self.target_model = DuelingDQN(self.env.observation_space.shape, self.env.action_space.n, noisy=self.noisy, sigma_init=self.sigma_init)
import torch.nn.functional as F
from torch import nn
from collections import namedtuple
import numpy as np
import torch

# from torch.distributions import Categorical
from torch.distributions import Normal, Independent

import pickle, os, random, torch

from collections import defaultdict
import pandas as pd 
import gymnasium as gym
import matplotlib.pyplot as plt

Batch = namedtuple('Batch', ['state', 'action', 'next_state', 'reward', 'not_done', 'extra', 'returns'])

# Actor-critic agent
class Policy(nn.Module):
    def __init__(self, state_dim, action_dim, max_action):
        super().__init__()
        self.max_action = max_action
        self.actor = nn.Sequential(
            nn.Linear(state_dim, 32), nn.ReLU(),
            nn.Linear(32, 32), nn.ReLU(),
            nn.Linear(32, action_dim)
        )

    def forward(self, state):
        return self.max_action * torch.tanh(self.actor(state))


class Critic(nn.Module):
    def __init__(self, state_dim, action_dim):
        super().__init__()
        self.value = nn.Sequential(
            nn.Linear(state_dim+action_dim, 32), nn.ReLU(),
            nn.Linear(32, 32), nn.ReLU(),
            nn.Linear(32, 1)
        )

    def forward(self, state, action):
        x = torch.cat([state, action], 1)
        return self.value(x)

class ReplayBuffer(object):
    def __init__(self, state_shape:tuple, action_dim: int, max_size=int(1e6)):
        self.max_size = max_size
        self.ptr = 0
        self.size = 0

        dtype = torch.uint8 if len(state_shape) == 3 else torch.float32 # unit8 is used to store images
        self.state = torch.zeros((max_size, *state_shape), dtype=dtype)
        self.action = torch.zeros((max_size, action_dim), dtype=dtype)
        self.next_state = torch.zeros((max_size, *state_shape), dtype=dtype)
        self.reward = torch.zeros((max_size, 1), dtype=dtype)
        self.not_done = torch.zeros((max_size, 1), dtype=dtype)
        self.extra = {}
        
        #keep track of returns for the SIL
        self.returns = torch.zeros((max_size, 1), dtype=torch.float32)  # Buffer to store returns (for SIL)
    
    def _to_tensor(self, data, dtype=torch.float32):   
        if isinstance(data, torch.Tensor):
            return data.to(dtype=dtype)
        return torch.tensor(data, dtype=dtype)

    def add(self, state, action, next_state, reward, done, extra:dict=None, return_val = None):
        self.state[self.ptr] = self._to_tensor(state, dtype=self.state.dtype)
        self.action[self.ptr] = self._to_tensor(action)
        self.next_state[self.ptr] = self._to_tensor(next_state, dtype=self.state.dtype)
        self.reward[self.ptr] = self._to_tensor(reward)
        self.not_done[self.ptr] = self._to_tensor(1. - done)
        
        #if we add a sil value then add it
        if return_val is not None:
            self.returns[self.ptr] = self._to_tensor(return_val)

        if extra is not None:
            for key, value in extra.items():
                if key not in self.extra: # init buffer
                    self.extra[key] = torch.zeros((self.max_size, *value.shape), dtype=torch.float32)
                self.extra[key][self.ptr] = self._to_tensor(value)

        self.ptr = (self.ptr + 1) % self.max_size
        self.size = min(self.size + 1, self.max_size)

    def sample(self, batch_size, device='cpu', sil = False):
        #if we sample with SIL then do this otherwise do the normal sampling
        if sil:
            # SIL sampling: prioritize high-return experiences
            normalized_returns = self.returns[:self.size].squeeze().numpy()
            if np.sum(normalized_returns) > 0:
                probs = normalized_returns / np.sum(normalized_returns)
                ind = np.random.choice(np.arange(self.size), size=batch_size, p=probs)
            else:
                ind = np.random.randint(0, self.size, size=batch_size)
        else:
            # Uniform random sampling
            ind = np.random.randint(0, self.size, size=batch_size)

        if self.extra:
            extra = {key: value[ind].to(device) for key, value in self.extra.items()}
        else:
            extra = {}

        batch = Batch(
            state = self.state[ind].to(device),
            action = self.action[ind].to(device), 
            next_state = self.next_state[ind].to(device), 
            reward = self.reward[ind].to(device), 
            not_done = self.not_done[ind].to(device), 
            extra = extra,
            returns=self.returns[ind].to(device)  # Include returns in the batch

        )
        return batch
    
    def get_all(self, device='cpu'):
        if self.extra:
            extra = {key: value[:self.size].to(device) for key, value in self.extra.items()}
        else:
            extra = {}

        batch = Batch(
            state = self.state[:self.size].to(device),
            action = self.action[:self.size].to(device), 
            next_state = self.next_state[:self.size].to(device), 
            reward = self.reward[:self.size].to(device), 
            not_done = self.not_done[:self.size].to(device), 
            extra = extra,
            returns=self.returns[:self.size].to(device)  # Include returns in the batch

        )
        return batch

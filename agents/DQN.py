import numpy as np

import torch
import torch.optim as optim

from agents.BaseAgent import BaseAgent
from networks.networks import DQN
from networks.network_bodies import AtariBody, SimpleBody
from utils.ReplayMemory import ExperienceReplayMemory, PrioritizedReplayMemory

from timeit import default_timer as timer
from collections import deque

import sys
np.set_printoptions(threshold=sys.maxsize)

from utils import LinearSchedule, PiecewiseSchedule, ExponentialSchedule

class Agent(BaseAgent):
    def __init__(self, static_policy=False, env=None, config=None, log_dir='/tmp/gym', tb_writer=None):
        super(Agent, self).__init__(config=config, env=env, log_dir=log_dir, tb_writer=tb_writer)
        self.config = config
        self.static_policy = static_policy
        self.num_feats = env.observation_space.shape
        self.num_actions = env.action_space.n * len(config.adaptive_repeat)
        self.envs = env

        self.declare_networks()
            
        self.target_model.load_state_dict(self.model.state_dict())
        self.optimizer = optim.Adam(self.model.parameters(), lr=self.config.lr, eps=self.config.adam_eps)
        
        self.loss_fun = torch.nn.SmoothL1Loss(reduction='mean')
        # self.loss_fun = torch.nn.MSELoss(reduction='mean')
        
        #move to correct device
        self.model = self.model.to(self.config.device)
        self.target_model.to(self.config.device)

        if self.static_policy:
            self.model.eval()
            self.target_model.eval()
        else:
            self.model.train()
            self.target_model.train()

        self.declare_memory()
        self.update_count = 0
        # self.nstep_buffer = []

        self.training_priors()

    def declare_networks(self):
        self.model = DQN(self.num_feats, self.num_actions, noisy=self.config.noisy_nets, sigma_init=self.config.sigma_init, body=AtariBody)
        self.target_model = DQN(self.num_feats, self.num_actions, noisy=self.config.noisy_nets, sigma_init=self.config.sigma_init, body=AtariBody)

    def declare_memory(self):
        # self.memory = ExperienceReplayMemory(self.config.exp_replay_size) if not self.config.priority_replay else PrioritizedReplayMemory(self.config.exp_replay_size, self.config.priority_alpha, self.config.priority_beta_start, self.config.priority_beta_tsteps)
        self.memory = ExperienceReplayMemory(self.config.exp_replay_size)

    def training_priors(self):
        self.episode_rewards = np.zeros(self.config.num_envs)
        self.last_100_rewards = deque(maxlen=100)

        if len(self.config.epsilon_final) == 1:
            if self.config.epsilon_decay[0] > 1.0:
                self.anneal_eps = ExponentialSchedule(self.config.epsilon_start, self.config.epsilon_final[0], self.config.epsilon_decay[0], self.config.max_tsteps)
            else:
                self.anneal_eps = LinearSchedule(self.config.epsilon_start, self.config.epsilon_final[0], self.config.epsilon_decay[0], self.config.max_tsteps)
        else:
            self.anneal_eps = PiecewiseSchedule(self.config.epsilon_start, self.config.epsilon_final, self.config.epsilon_decay, self.config.max_tsteps)

        self.prev_observations, self.actions, self.rewards, self.dones = None, None, None, None,
        self.observations = self.envs.reset()

    def append_to_replay(self, s, a, r, s_, t):
        #TODO: Naive. This is implemented like rainbow.
        # However, true nstep q learning requires
        # off-policy correction
        # self.nstep_buffer.append((s, a, r, s_))

        # if(len(self.nstep_buffer)<self.config.N_steps):
        #     return
        
        # R = sum([self.nstep_buffer[i][2]*(self.config.gamma**i) for i in range(self.config.N_steps)])
        # state, action, _, _ = self.nstep_buffer.pop(0)

        # self.memory.push((state, action, R, s_))
        
        # self.memory.push((s, a, r, s_))
        
        # self.memory.push((s, a, r, s_, t))
        for state, action, reward, next_state, terminal in zip(s, a, r, s_, t):
            self.memory.push((state, action, reward, next_state, terminal))

    # NOTE: Made to work with OpenAI Gym's experience replay
    #   probably broken with prioritized replay
    def prep_minibatch(self):
        # random transition batch is taken from experience replay memory
        batch_state, batch_action, batch_reward, batch_next_state, batch_terminal, indices, weights = self.memory.sample(self.config.batch_size)

        batch_state = torch.from_numpy(batch_state).to(self.config.device).to(torch.float)
        batch_state = batch_state if self.config.s_norm is None else batch_state/self.config.s_norm

        batch_action = torch.from_numpy(batch_action).to(self.config.device).to(torch.long).unsqueeze(dim=1)
        
        batch_reward = torch.from_numpy(batch_reward).to(self.config.device).to(torch.float).unsqueeze(dim=1)

        batch_next_state = torch.from_numpy(batch_next_state).to(self.config.device).to(torch.float)
        batch_next_state = batch_next_state if self.config.s_norm is None else batch_next_state/self.config.s_norm

        batch_terminal = torch.from_numpy(batch_terminal).to(self.config.device).to(torch.float).unsqueeze(dim=1)

        return batch_state, batch_action, batch_reward, batch_next_state, batch_terminal, indices, weights

    # def prep_minibatch(self):
    #     # random transition batch is taken from experience replay memory
    #     transitions, indices, weights = self.memory.sample(self.config.batch_size)
        
    #     # batch_state, batch_action, batch_reward, batch_next_state = zip(*transitions)
    #     batch_state, batch_action, batch_reward, batch_next_state, batch_terminal = zip(*transitions)

    #     shape = (-1,)+self.num_feats

    #     #making whole structure a np array and using from_numpy avoids costly copy operation
    #     batch_state = np.stack(batch_state, axis=0)
    #     batch_state = torch.from_numpy(batch_state).to(self.config.device).to(torch.float).view(shape)
    #     batch_state = batch_state if self.config.s_norm is None else batch_state/self.config.s_norm

    #     batch_action = np.stack(batch_action, axis=0)
    #     batch_action = torch.from_numpy(batch_action).to(self.config.device).to(torch.long).view(-1, 1)
    #     batch_reward = np.stack(batch_reward, axis=0)
    #     batch_reward = torch.from_numpy(batch_reward).to(self.config.device).to(torch.float).view(-1, 1)
        
    #     # non_final_mask = torch.tensor(tuple(map(lambda s: s is not None, batch_next_state)), device=self.config.device, dtype=torch.bool)
    #     # try: 
    #     #     non_final_next_states = np.stack([s for s in batch_next_state if s is not None], axis=0)
    #     #     non_final_next_states = torch.from_numpy(non_final_next_states).to(self.config.device).to(torch.float).view(shape)
    #     #     non_final_next_states = non_final_next_states if self.config.s_norm is None else non_final_next_states/self.config.s_norm

    #     #     empty_next_state_values = False
    #     # except:
    #     #     #sometimes all next states are false
    #     #     non_final_next_states = None
    #     #     empty_next_state_values = True

    #     # NOTE: new
    #     batch_next_state = np.stack(batch_next_state, axis=0)
    #     batch_next_state = torch.from_numpy(batch_next_state).to(self.config.device).to(torch.float).view(shape)
    #     batch_next_state = batch_next_state if self.config.s_norm is None else batch_next_state/self.config.s_norm
    #     batch_terminal = np.stack(batch_terminal, axis=0)
    #     batch_terminal = torch.from_numpy(batch_terminal).to(self.config.device).to(torch.float).view(-1, 1)


    #     #return batch_state, batch_action, batch_reward, non_final_next_states, non_final_mask, empty_next_state_values, indices, weights
    #     return batch_state, batch_action, batch_reward, batch_next_state, batch_terminal, indices, weights

    def compute_loss(self, batch_vars, tstep): #faster
        #batch_state, batch_action, batch_reward, non_final_next_states, non_final_mask, empty_next_state_values, indices, weights = batch_vars
        batch_state, batch_action, batch_reward, batch_next_state, batch_terminal, indices, weights = batch_vars

        #estimate
        # self.model.sample_noise()
        current_q_values = self.model(batch_state).gather(1, batch_action)
        
        #target
        with torch.no_grad():
            # max_next_q_values = torch.zeros(self.config.batch_size, device=self.config.device, dtype=torch.float).unsqueeze(dim=1)
            # if not empty_next_state_values:
            #     max_next_action = self.get_max_next_state_action(non_final_next_states)
            #     # self.target_model.sample_noise()
            #     max_next_q_values[non_final_mask] = self.target_model(non_final_next_states).gather(1, max_next_action)
            # # expected_q_values = batch_reward + ((self.config.gamma**self.config.N_steps)*max_next_q_values)
            # expected_q_values = batch_reward + self.config.gamma*max_next_q_values
            
            next_q_values = self.config.gamma * self.target_model(batch_next_state).max(dim=1)[0].view(-1, 1) * (1.0 - batch_terminal)
            # max_next_action = self.model(batch_next_state).max(dim=1)[1].view(-1, 1) # try double learning
            # next_q_values = self.config.gamma * self.target_model(batch_next_state).gather(1, max_next_action) * (1.0 - batch_terminal)
            target = batch_reward + next_q_values

        # diff = (target - current_q_values)
        # if self.config.priority_replay:
        #     self.memory.update_priorities(indices, diff.detach().squeeze().abs().cpu().numpy().tolist())
        #     loss = 0.5*diff.pow(2).squeeze()
        #     loss *= weights
        #     #TODO: clamp loss here?
        # else:
        #     loss = 0.5*diff.pow(2) #squared error in paper
        #     loss = loss.clamp(-1, 1) #they clamp the error term, not gradient
        # loss = diff.clamp(-1, 1) #they clamp the error term, not , why -1?
        # loss = loss.pow(2).mul(0.5)
        # loss = loss.mean()

        loss = self.loss_fun(current_q_values, target)

        #log val estimates
        with torch.no_grad():
            self.tb_writer.add_scalar('Policy/Value Estimate', current_q_values.detach().mean().item(), tstep)
            self.tb_writer.add_scalar('Policy/Next Value Estimate', target.detach().mean().item(), tstep)

        return loss

    def update_(self, s, a, r, s_, terminal, tstep=0):
        if self.static_policy:
            return None

        self.append_to_replay(s, a, r, s_, terminal)

        if tstep < self.config.learn_start:
            return None

        batch_vars = self.prep_minibatch()

        loss = self.compute_loss(batch_vars, tstep)

        # Optimize the model
        self.optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.model.parameters(), self.config.grad_norm_max)
        self.optimizer.step()

        self.update_target_model()

        #more logging
        with torch.no_grad():
            self.tb_writer.add_scalar('Loss/Total Loss', loss.item(), tstep)
            self.tb_writer.add_scalar('Learning/Learning Rate', np.mean([param_group['lr'] for param_group in self.optimizer.param_groups]), tstep)

            #log weight norm
            weight_norm = 0.
            for p in self.model.parameters():
                param_norm = p.data.norm(2)
                weight_norm += param_norm.item() ** 2
            weight_norm = weight_norm ** (1./2.)
            self.tb_writer.add_scalar('Learning/Weight Norm', weight_norm, tstep)

            #log grad_norm
            grad_norm = 0.
            for p in self.model.parameters():
                param_norm = p.grad.data.norm(2)
                grad_norm += param_norm.item() ** 2
            grad_norm = grad_norm ** (1./2.)
            self.tb_writer.add_scalar('Learning/Grad Norm', grad_norm, tstep)
        
            #log sigma param norm
            if self.config.noisy_nets:
                sigma_norm = 0.
                for name, p in self.model.named_parameters():
                    if p.requires_grad and 'sigma' in name:
                        param_norm = p.data.norm(2)
                        sigma_norm += param_norm.item() ** 2
                sigma_norm = sigma_norm ** (1./2.)
                self.tb_writer.add_scalar('Policy/Sigma Norm', sigma_norm, tstep)
        
    def get_action(self, s, eps=0.1):
        with torch.no_grad():
            if np.random.random() > eps or self.static_policy or self.config.noisy_nets:
                X = torch.from_numpy(s).to(self.config.device).to(torch.float).view((-1,)+self.num_feats)
                X = X if self.config.s_norm is None else X/self.config.s_norm

                # self.model.sample_noise()
                return torch.argmax(self.model(X), dim=1).cpu().numpy()
            else:
                return np.random.randint(0, self.num_actions, (s.shape[0]))

    def update_target_model(self):
        self.update_count+=1
        self.update_count = int(self.update_count) % int(self.config.target_net_update_freq)
        if self.update_count == 0:
            self.target_model.load_state_dict(self.model.state_dict())

    def get_max_next_state_action(self, next_states):
        return self.target_model(next_states).max(dim=1)[1].view(-1, 1)

    def finish_nstep(self, idx):
        # while len(self.nstep_buffer) > 0:
        #     R = sum([self.nstep_buffer[i][2]*(self.config.gamma**i) for i in range(len(self.nstep_buffer))])
        #     state, action, _, _ = self.nstep_buffer.pop(0)

        #     self.memory.push((state, action, R, None))
        pass

    def reset_hx(self, idx):
        pass

    def step(self, current_tstep, step=0):
        epsilon = self.anneal_eps(current_tstep)
        self.tb_writer.add_scalar('Policy/Epsilon', epsilon, current_tstep)

        self.actions = self.get_action(self.observations, epsilon)

        self.prev_observations=self.observations
        self.observations, self.rewards, self.dones, self.infos = self.envs.step(self.actions)
        
        self.episode_rewards += self.rewards
        
        for idx, done in enumerate(self.dones):
            if done:
                self.finish_nstep(idx)
                self.reset_hx(idx)

                self.tb_writer.add_scalar('Performance/Agent Reward', self.episode_rewards[idx], current_tstep+idx)
                self.episode_rewards[idx] = 0
        
        for idx, info in enumerate(self.infos):
            if 'episode' in info.keys():
                self.last_100_rewards.append(info['episode']['r'])
                self.tb_writer.add_scalar('Performance/Environment Reward', info['episode']['r'], current_tstep+idx)
                self.tb_writer.add_scalar('Performance/Episode Length', info['episode']['l'], current_tstep+idx)

    def update(self, current_tstep):
        self.update_(self.prev_observations, self.actions, self.rewards, self.observations, self.dones.astype(int), current_tstep)
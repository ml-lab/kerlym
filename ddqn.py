import logging,os,cPickle,time
from random import choice, random, sample
import keras.backend as K
import numpy as np
from keras.layers.embeddings import Embedding
from keras.layers.convolutional import Convolution1D, MaxPooling1D
from keras.layers.core import Dense, Dropout, Activation, Flatten, Reshape
from keras.layers.recurrent import LSTM
from keras.layers.embeddings import Embedding
from keras.models import Model
from keras.layers import Convolution2D, Dense, Flatten, Input, merge, Lambda, TimeDistributed
from keras.optimizers import RMSprop, Adadelta, Adam

# Some example networks which can be used as the Q function approximation ...

def embedding_rnn(agent, env, scalar_range=100, embedding_dim=8, dropout=0, **args):
    S = Input(shape=[agent.input_dim])
    h = Lambda(lambda x: K.cast(x, 'int32'))(S)
    h = Embedding(scalar_range, embedding_dim)(h)
    h = Reshape([agent.nframes, embedding_dim*agent.input_dim/agent.nframes])(h)
    h = TimeDistributed(Dense(16, activation='relu', init='he_normal'))(h)
    h = Dropout(dropout)(h)
    h = LSTM(32, return_sequences=True)(h)
    h = Dropout(dropout)(h)
    h = LSTM(32)(h)
    h = Dropout(dropout)(h)
    #h = Flatten()(h)
    V = Dense(env.action_space.n, activation='linear')(h)
    model = Model(S,V)
    model.compile(loss='mse', optimizer=Adam(lr=0.001) )
    return model

def embedding_nn(agent, env, scalar_range=100, embedding_dim=32, dropout=0, **args):
    S = Input(shape=[agent.input_dim])
    h = Lambda(lambda x: K.cast(x, 'int32'))(S)
    h = Embedding(100, 32)(h)
    h = Flatten()(h)
    h = Dense(16, activation='relu', init='he_normal')(h)
    h = Dropout(dropout)(h)
    h = Dense(16, activation='relu', init='he_normal')(h)
    h = Dropout(dropout)(h)
    V = Dense(env.action_space.n, activation='linear')(h)
    model = Model(S,V)
    model.compile(loss='mse', optimizer=Adam(lr=0.001) )
    return model

def default_model_factory(agent, env, dropout=0.5, **args):
    S = Input(shape=[agent.input_dim])
    h = Dense(256, activation='relu', init='he_normal')(S)
    h = Dropout(dropout)(h)
    h = Dense(256, activation='relu', init='he_normal')(h)
    h = Dropout(dropout)(h)
    V = Dense(env.action_space.n, activation='linear')(h)
    model = Model(S,V)
    model.compile(loss='mse', optimizer=Adam(lr=0.001) )
    return model


class D2QN:
    def __init__(self, env, nframes=1, epsilon=0.1, discount=0.99, train=1, update_nsamp=1000, dropout=0, batch_size=32, nfit_epoch=1, epsilon_schedule=None, modelfactory=default_model_factory, **args):
        self.env = env
        self.nframes = nframes
        self.actions = range(env.action_space.n)
        self.epsilon = epsilon
        self.gamma = discount
        self.train = train
        self.update_nsamp = update_nsamp
        self.observations = []
        self.nfit_epoch = nfit_epoch
        self.epsilon_schedule = epsilon_schedule

        # Neural Network Parameters
        self.batch_size = batch_size
        self.dropout = dropout
        self.input_dim_orig = [nframes]+list(env.observation_space.shape)
        self.input_dim = np.product( self.input_dim_orig )
        print "Input Dim: ", self.input_dim, self.input_dim_orig
        print "Output Actions: ", self.actions

        self.old_state_m1 = None
        self.action_m1 = None
        self.reward_m1 = None

        self.updates = 0
        self.n_obs_train = 5000

        self.model_updates = 0

        self.models = map(lambda x: modelfactory(self, env=env, dropout=dropout, **args), [0,1])
        print self.models[0].summary()


    def act( self, state=None, pstate=None, paction=None, preward=None):
        state = np.asarray(state).reshape(1, self.input_dim)

        if self.train:
            qval = self.get_model(greedy=True).predict(state, batch_size=1)

            if random() < self.epsilon  or pstate is None:
                action = np.random.randint(0, len(self.actions))
            else:
                action = (np.argmax(qval))

        else:
            qval = self.get_model(greedy=True).predict(state, batch_size=1)
            if self.updates == 0 or pstate is None:
                action = np.random.randint(0, len(self.actions))
                self.updates += 1
            else:
                action = (np.argmax(qval))

        return action, qval

    def update_train(self, p_state, action, p_reward, new_state, terminal, update_model=False):
        self.observations.append((p_state, action, p_reward, new_state, terminal))
        self.updates += 1

        # Train Model once enough history
        if(update_model):

            # number of NN updates we have performed (used to index between double Q models)
            self.model_updates += 1

            X_train, y_train = self.process_minibatch(terminal)
            self.get_model(greedy=False).fit(X_train,
                           y_train,
                           batch_size=self.batch_size,
                           nb_epoch=self.nfit_epoch,
                           #nb_epoch=1,
                           verbose=1,
                           shuffle=True)

    def get_model( self, greedy=False ):
        if greedy:
            return self.models[(self.model_updates+1)%2]
        else:
            return self.models[self.model_updates%2]

    def process_minibatch(self, terminal_rewards):
        X_train = []
        y_train = []
        val = 0

        if self.update_nsamp == None:
            samples = self.observations
        else:
            nsamp = min(len(self.observations), self.update_nsamp)
            samples = sample(self.observations, nsamp)

        for memory in samples:
            if val == 0:
                val += 1
                old_state_m1, action_m1, reward_m1, new_state_m1, terminal = memory
            else:
                # Get stored values.
                old_state_m, action_m, reward_m, new_state_m, terminal = memory

                input = old_state_m
                old_state_m = input.reshape(1, self.input_dim)
                old_qval =self.get_model(greedy=True).predict(old_state_m,
                                              batch_size=1,
                                              verbose=0)

                input2 = new_state_m
                new_state_m = input2.reshape(1, self.input_dim)
                newQ = self.get_model(greedy=True).predict(new_state_m,
                                          batch_size=1,
                                          verbose=0)
                maxQ = np.max(newQ)
                y = np.zeros((1, len(self.actions)))
                y[:] = old_qval[:]

                # Check for terminal state.
                if terminal:
                    update = reward_m
                else:
                    update = (reward_m + (self.gamma * maxQ))

                y[0][action_m] = update
                X_train.append(old_state_m.reshape(self.input_dim,))
                y_train.append(y.reshape(len(self.actions),))
                self.old_state_m1, self.action_m1, self.reward_m1, new_state_m1, terminal = memory

        # Generate Numpy Arrays
        X_train = np.array(X_train)
        y_train = np.array(y_train)

        return X_train, y_train


    def learn(self, nhist=2):
        max_pathlength = 200
        timesteps_per_batch = 1000
        max_episodes = 10000000000000
        max_kl = 0.01

        start_time = time.time()
        numeptotal = 0
        i = 0

        for e in xrange(max_episodes):

            if not self.epsilon_schedule == None:
                self.epsilon = self.epsilon_schedule(e, self.epsilon)

            observation = self.env.reset()
            done = False
            total_cost = 0.0
            total_reward = 0.0
            t = 0
            maxv = []
            minv = []

            obs = np.zeros( [self.nframes]+list(self.env.observation_space.shape) )
            new_obs = np.zeros( [self.nframes]+list(self.env.observation_space.shape) )

            obs[0,:] = observation

            while (not done) and (t<max_pathlength):
                t += 1
                self.env.render()
                action, values = self.act(obs)
                maxv.append(max(values.flatten()))
                minv.append(min(values.flatten()))

                new_observation, reward, done, info = self.env.step(action)
                new_obs[1:,:] = obs[-1:,:]
                new_obs[0,:] = new_observation

                do_update = (i%timesteps_per_batch==timesteps_per_batch-1)
                self.update_train( obs, action, reward, new_obs, done, do_update )

                obs[:,:] = new_obs[:,:]
                total_reward += reward
                i += 1

            print " * Episode %08d\tTotal Reward: %d\tEpsilon: %f"%(e, total_reward, self.epsilon)
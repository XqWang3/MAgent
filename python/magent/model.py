""" base models class"""

import multiprocessing


class BaseModel:
    def __init__(self, env, handle, *args, **kwargs):
        """ init

        Parameters
        ----------
        env: Environment
            env
        handle: GroupHandle
            handle of this group, handles are returned by env.get_handles()
        """
        pass

    def infer_action(self, raw_obs, ids, *args, **kwargs):
        """ infer action for a group of agents

        Parameters
        ----------
        raw_obs: tuple
            raw_obs is a tuple of (view, feature)
            view is a numpy array, its shape is n * view_width * view_height * n_channel
                                   it contains the spatial local observation for all the agents
            feature is a numpy array, its shape is n * feature_size
                                   it contains the non-spatial feature for all the agents
        ids: numpy array of int32
            the unique id of every agents
        args:
            additional custom args
        kwargs:
            additional custom args
        """
        pass

    def train(self, sample_buffer, **kwargs):
        """ feed new samples and train

        Parameters
        ----------
        sample_buffer: EpisodesBuffer
            a buffer contains transitions of agents

        Returns
        -------
        loss and estimated mean state value
        """
        return 0, 0    # loss, mean value

    def save(self, *args, **kwargs):
        """ save the model """
        pass

    def load(self, *args, **kwargs):
        """ load the model """
        pass


class ProcessingModel(BaseModel):
    """
    start a sub-processing to host a model,
    use pipe for communication
    """
    def __init__(self, env, handle, name, sample_buffer_capacity=1000,
                 RLModel=None, **kwargs):
        """
        Parameters
        ----------
        env: environment
        handle: group handle
        name: str
            name of the model (be used when store model)
        sample_buffer_capacity: int
            the maximum number of samples (s,r,a,s') to collect in a game round
        RLModel: BaseModel
            the RL algorithm class
        kwargs: dict
            arguments for RLModel
        """
        BaseModel.__init__(self, env, handle)

        assert RLModel is not None

        kwargs['env'] = env
        kwargs['handle'] = handle
        kwargs['name'] = name
        pipe = multiprocessing.Pipe()
        proc = multiprocessing.Process(
            target=model_client,
            args=(pipe[1], sample_buffer_capacity, RLModel, kwargs),
        )

        conn = pipe[0]
        proc.start()

        self.conn = conn

    def sample_step(self, rewards, alives, block=True):
        """record a step (should be followed by check_done)

        Parameters
        ----------
        block: bool
            if it is True, the function call will block
            if it is False, the caller must call check_done() afterward
                            to check/consume the return message
        """
        self.conn.send(["sample", rewards, alives])

        if block:
            self.check_done()

    def infer_action(self, raw_obs, ids, policy='e_greedy', eps=0, block=True):
        """ infer action

        Parameters
        ----------
        policy: str
            can be 'e_greedy' or 'greedy'
        eps: float
            used when policy is 'e_greedy'
        block: bool
            if it is True, the function call will block, and return actions
            if it is False, the function call won't block, the caller
                            must call fetch_action() to get actions

        Returns
        -------
        actions: numpy array (int32)
            see above
        """

        self.conn.send(["act", raw_obs, ids, policy, eps])
        if block:
            return self.conn.recv()
        else:
            return None

    def fetch_action(self):
        """ fetch actions , fetch action after calling infer_action(block=False)

        Returns
        -------
        actions: numpy array (int32)
        """
        return self.conn.recv()

    def train(self, print_every=5000, block=True):
        """ train new data samples according to the model setting

        Parameters
        ----------
        print_every: int
            print training log info every print_every batches

        """
        self.conn.send(['train', print_every])

        if block:
            return self.fetch_train()

    def fetch_train(self):
        """ fetch result of train after calling train(block=False)

        Returns
        -------
        loss: float
            mean loss
        value: float
            mean state value
        """
        return self.conn.recv()

    def save(self, save_dir, epoch, block=True):
        """ save model

        Parameters
        ----------
        block: bool
            if it is True, the function call will block
            if it is False, the caller must call check_done() afterward
                            to check/consume the return message
        """

        self.conn.send(["save", save_dir, epoch])
        if block:
            self.check_done()

    def load(self, save_dir, epoch, name=None, block=True):
        """ load model

        Parameters
        ----------
        name: str
            name of the model (set when stored name is not the same as self.name)
        block: bool
            if it is True, the function call will block
            if it is False, the caller must call check_done() afterward
                            to check/consume the return message
        """
        self.conn.send(["load", save_dir, epoch, name])
        if block:
            self.check_done()

    def check_done(self):
        """ check return message of sub processing """
        assert self.conn.recv() == 'done'

    def quit(self):
        """ quit """
        self.conn.send(["quit"])


def model_client(conn, sample_buffer_capacity, RLModel, model_args):
    """target function for sub-processing to host a model

    Parameters
    ----------
    conn: connection object
    sample_buffer_capacity: int
        the maximum number of samples (s,r,a,s') to collect in a game round
    RLModel: BaseModel
        the RL algorithm class
    args: dict
        arguments to RLModel
    """
    import magent.utility

    model = RLModel(**model_args)
    sample_buffer = magent.utility.EpisodesBuffer(capacity=sample_buffer_capacity)

    while True:
        cmd = conn.recv()
        if cmd[0] == 'act':
            obs = cmd[1]
            ids = cmd[2]
            policy = cmd[3]
            eps = cmd[4]
            acts = model.infer_action(obs, ids, policy=policy, eps=eps)
            conn.send(acts)
        elif cmd[0] == 'train':
            print_every = cmd[1]
            total_loss, value = model.train(sample_buffer, print_every=print_every)
            sample_buffer = magent.utility.EpisodesBuffer(sample_buffer_capacity)
            conn.send((total_loss, value))
        elif cmd[0] == 'sample':
            rewards = cmd[1]
            alives = cmd[2]
            sample_buffer.record_step(ids, obs, acts, rewards, alives)
            conn.send("done")
        elif cmd[0] == 'save':
            savedir = cmd[1]
            n_iter = cmd[2]
            model.save(savedir, n_iter)
            conn.send("done")
        elif cmd[0] == 'load':
            savedir = cmd[1]
            n_iter = cmd[2]
            name = cmd[3]
            model.load(savedir, n_iter, name)
            conn.send("done")
        elif cmd[0] == 'quit':
            break
        else:
            print("Error: Unknown command %s" % cmd[0])
            break

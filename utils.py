import numpy as np
import tensorflow as tf
import random
import copy
from mpi_util import mpi_moments


def fc(x, scope, nh, *, init_scale=1.0, init_bias=0.0):
    with tf.variable_scope(scope):
        nin = x.get_shape()[1].value
        w = tf.get_variable("w", [nin, nh], initializer=ortho_init(init_scale))
        b = tf.get_variable("b", [nh], initializer=tf.constant_initializer(init_bias))
        return tf.matmul(x, w)+b

def conv(x, scope, *, nf, rf, stride, pad='VALID', init_scale=1.0, data_format='NHWC', one_dim_bias=False):
    if data_format == 'NHWC':
        channel_ax = 3
        strides = [1, stride, stride, 1]
        bshape = [1, 1, 1, nf]
    elif data_format == 'NCHW':
        channel_ax = 1
        strides = [1, 1, stride, stride]
        bshape = [1, nf, 1, 1]
    else:
        raise NotImplementedError
    bias_var_shape = [nf] if one_dim_bias else [1, nf, 1, 1]
    nin = x.get_shape()[channel_ax].value
    wshape = [rf, rf, nin, nf]
    with tf.variable_scope(scope):
        w = tf.get_variable("w", wshape, initializer=ortho_init(init_scale))
        b = tf.get_variable("b", bias_var_shape, initializer=tf.constant_initializer(0.0))
        if not one_dim_bias and data_format == 'NHWC':
            b = tf.reshape(b, bshape)
        return b + tf.nn.conv2d(x, w, strides=strides, padding=pad, data_format=data_format)


def deconv(x, scope, *, nf, rf, stride, init_scale=1.0, data_format='NHWC'):
    if data_format == 'NHWC':
        channel_ax = 3
        strides = (stride, stride)
        #strides = [1, stride, stride, 1]
    elif data_format == 'NCHW':
        channel_ax = 1
        strides = (stride, stride)
        #strides = [1, 1, stride, stride]
    else:
        raise NotImplementedError

    with  tf.variable_scope(scope):
        out = tf.contrib.layers.conv2d_transpose(x,
                                                num_outputs=nf,
                                                kernel_size=rf,
                                                stride=strides,
                                                padding='VALID',
                                                weights_initializer=ortho_init(init_scale),
                                                biases_initializer=tf.constant_initializer(0.0),
                                                activation_fn=None,
                                                data_format=data_format)
        return out


def ortho_init(scale=1.0):
    def _ortho_init(shape, dtype, partition_info=None):
        #lasagne ortho init for tf
        shape = tuple(shape)
        if len(shape) == 2:
            flat_shape = shape
        elif len(shape) == 4: # assumes NHWC
            flat_shape = (np.prod(shape[:-1]), shape[-1])
        else:
            raise NotImplementedError
        a = np.random.normal(0.0, 1.0, flat_shape)
        u, _, v = np.linalg.svd(a, full_matrices=False)
        q = u if u.shape == flat_shape else v # pick the one with the correct shape
        q = q.reshape(shape)
        return (scale * q[:shape[0], :shape[1]]).astype(np.float32)
    return _ortho_init

def tile_images(array, n_cols=None, max_images=None, div=1):
    if max_images is not None:
        array = array[:max_images]
    if len(array.shape) == 4 and array.shape[3] == 1:
        array = array[:, :, :, 0]
    assert len(array.shape) in [3, 4], "wrong number of dimensions - shape {}".format(array.shape)
    if len(array.shape) == 4:
        assert array.shape[3] == 3, "wrong number of channels- shape {}".format(array.shape)
    if n_cols is None:
        n_cols = max(int(np.sqrt(array.shape[0])) // div * div, div)
    n_rows = int(np.ceil(float(array.shape[0]) / n_cols))

    def cell(i, j):
        ind = i * n_cols + j
        return array[ind] if ind < array.shape[0] else np.zeros(array[0].shape)

    def row(i):
        return np.concatenate([cell(i, j) for j in range(n_cols)], axis=1)

    return np.concatenate([row(i) for i in range(n_rows)], axis=0)


def set_global_seeds(i):
    try:
        import tensorflow as tf
    except ImportError:
        pass
    else:
        from mpi4py import MPI
        tf.set_random_seed(i)
    np.random.seed(i)
    random.seed(i)


def explained_variance_non_mpi(ypred,y):
    """
    Computes fraction of variance that ypred explains about y.
    Returns 1 - Var[y-ypred] / Var[y]

    interpretation:
        ev=0  =>  might as well have predicted zero
        ev=1  =>  perfect prediction
        ev<0  =>  worse than just predicting zero

    """
    assert y.ndim == 1 and ypred.ndim == 1
    vary = np.var(y)
    return np.nan if vary==0 else 1 - np.var(y-ypred)/vary

def mpi_var(x):
    return mpi_moments(x)[1]**2

def explained_variance(ypred,y):
    """
    Computes fraction of variance that ypred explains about y.
    Returns 1 - Var[y-ypred] / Var[y]

    interpretation:
        ev=0  =>  might as well have predicted zero
        ev=1  =>  perfect prediction
        ev<0  =>  worse than just predicting zero

    """
    assert y.ndim == 1 and ypred.ndim == 1
    vary = mpi_var(y)
    return np.nan if vary==0 else 1 - mpi_var(y-ypred)/vary


def add_noise(img, noise_p, noise_type):
    if noise_type == 'none':
        return img
    # img.shape=(64, 84, 84, 4)  noise_p=0.1 , noise_type='box'
    print("add noise:", img.shape, noise_p, noise_type, ", max:", np.max(img), ", min:", np.min(img))
    noise_mask = np.random.binomial(1, noise_p, size=img.shape[0]).astype(np.bool)
    w = 12
    n = 84//12
    idx_list = np.arange(n*n)
    random.shuffle(idx_list)
    idx_list = idx_list[:np.random.randint(10, 40)]
    for i in range(img.shape[0]):
        if not noise_mask[i]:
            continue
        for idx in idx_list:
            y = (idx // n)*w
            x = (idx % n)*w
            img[i, y:y+w, x:x+w, -1] += np.random.normal(0, 255*0.3, size=(w,w)).astype(np.uint8)

    img = np.clip(img, 0., 255.)
    return img


# if __name__ == '__main__':
#     import gym
#     import numpy as np
#     from functools import partial
#     from atari_wrappers import NoopResetEnv, FrameStack
#     from atari_wrappers import MaxAndSkipEnv, ProcessFrame84, StickyActionEnv
#     import matplotlib.pyplot as plt 

#     def make_env_all_params(rank, add_monitor=True):
#         env = gym.make("BreakoutNoFrameskip-v4")
#         assert 'NoFrameskip' in env.spec.id
#         env._max_episode_steps = 4500 * 4
#         env = StickyActionEnv(env)
#         env = MaxAndSkipEnv(env, skip=4)        # 每个动作连续执行4步
#         env = ProcessFrame84(env, crop=False)   # 处理观测
#         env = FrameStack(env, 4)                # 将连续4帧叠加起来作为输入
#         return env

#     make_env = partial(make_env_all_params, add_monitor=True)
#     env = make_env(0, add_monitor=False)
#     obs = env.reset()
#     print(np.asarray(obs).shape, env.action_space.sample())

#     img = np.expand_dims(np.asarray(obs), axis=0) / 255.
#     print("img:", img.max(), img.min(), img.mean())
#     img_noise = add_noise(img, noise_p=0.1, noise_type='box')
#     print("img_noise.shape =", img_noise.shape)

#     # plt.subplot(121)
#     # plt.imshow(img[0])
#     # plt.subplot(122)
#     plt.imshow(img_noise[0])
#     plt.show()






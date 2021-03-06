#
# Copyright (c) 2017 Idiap Research Institute, http://www.idiap.ch/
# Written by Angelos Katharopoulos <angelos.katharopoulos@idiap.ch>
#

from functools import reduce

from keras import backend as K
from keras.engine import Layer
from keras.metrics import categorical_accuracy, binary_accuracy, \
    get as get_metric, sparse_categorical_accuracy

from ..utils.functional import compose


class MetricLayer(Layer):
    """Create a layer that computes a metric taking into account masks."""
    def __init__(self, metric_func, batch_metrics, **kwargs):
        self.supports_masking = True
        self.metric_func = metric_func
        
        self.repeat = False
        if not isinstance(metric_func, str):
            if metric_func.__name__ in [m if isinstance(m, str) else m.__name__ for m in batch_metrics]:
                self.repeat = True
        
        super(MetricLayer, self).__init__(**kwargs)

    def compute_mask(self, inputs, input_mask):
        return None

    def build(self, input_shape):
        # Special care for accuracy because keras treats it specially
        try:
            if "acc" in self.metric_func:
                self.metric_func = self._generic_accuracy
        except TypeError:
            pass # metric_func is not a string
        self.metric_func = compose(K.expand_dims, get_metric(self.metric_func))
        
        super(MetricLayer, self).build(input_shape)

    def compute_output_shape(self, input_shape):
        # We need two inputs y_true, y_pred
        assert len(input_shape) == 2
        return (input_shape[0][0], 1)

    def call(self, inputs, mask=None):
        # Compute the metric
        metric = self.metric_func(*inputs)
        #
        # In case a metric does not make sense when calculated on a 
        # single observation/sample and it needs to be calculated 
        # on the whole batch like matthews correlation. We will return
        # duplicated value of the metric so later mean will not change
        # the reported value.
        #
        if self.repeat:
            tmp_metric = K.zeros_like(inputs[0], dtype=K.floatx())
            metric = K.map_fn(lambda x: x+metric, tmp_metric)
        
        if K.int_shape(metric)[-1] == 1:
            metric = K.squeeze(metric, axis=-1)
        
        # Apply the mask if needed
        if mask is not None:
            if not isinstance(mask, list):
                mask = [mask]
            mask = [K.cast(m, K.floatx()) for m in mask if m is not None]
            mask = reduce(lambda a, b: a*b, mask)
            metric *= mask
            metric /= K.mean(mask, axis=-1, keepdims=True)
        
        # Make sure that the tensor returned is (None, 1)
        dims = len(K.int_shape(metric))
        if dims > 1:
            metric = K.mean(metric, axis=list(range(1, dims)))
        return K.expand_dims(metric)

    @staticmethod
    def _generic_accuracy(y_true, y_pred):
        if K.int_shape(y_pred)[1] == 1:
            return binary_accuracy(y_true, y_pred)
        if K.int_shape(y_true)[-1] == 1:
            return sparse_categorical_accuracy(y_true, y_pred)
        
        return categorical_accuracy(y_true, y_pred)


class TripletLossLayer(Layer):
    """A bit of an unorthodox layer that implements the triplet loss with L2
    normalization.
    
    It receives 1 vector which is the concatenation of the three
    representations and performs the following operations.
    x = concat(x_a, x_p, x_n)
    N = x.shape[1]/3
    return ||x[:N] - x[2*N:]||_2^2 - ||x[:N] - x[N:2*N]||_2^2
    """
    def __init__(self, **kwargs):
        super(TripletLossLayer, self).__init__(**kwargs)

    def build(self, input_shape):
        assert not isinstance(input_shape, list)
        self.N = input_shape[1] // 3
        self.built = True

    def compute_output_shape(self, input_shape):
        assert not isinstance(input_shape, list)
        return (input_shape[0], 1)

    def call(self, x):
        N = self.N
        
        xa = x[:, :N]
        xp = x[:, N:2*N]
        xn = x[:, 2*N:]
        
        xa = xa / K.sqrt(K.sum(xa**2, axis=1, keepdims=True))
        xp = xp / K.sqrt(K.sum(xp**2, axis=1, keepdims=True))
        xn = xn / K.sqrt(K.sum(xn**2, axis=1, keepdims=True))
        
        dn = K.sum(K.square(xa - xn), axis=1, keepdims=True)
        dp = K.sum(K.square(xa - xp), axis=1, keepdims=True)
        
        return dn - dp

# example of a metric that cannot be calculated on a sample level 
# and then averaged, so to use in validation it needs to be
# included in batch_metrics = []
def matthews_correlation(y_true, y_pred):
    '''Calculates the Matthews correlation coefficient measure for quality
    of binary classification problems.
    '''
    y_pred_pos = K.round(K.clip(y_pred, 0, 1))
    y_pred_neg = 1 - y_pred_pos
    
    y_pos = K.round(K.clip(y_true, 0, 1))
    y_neg = 1 - y_pos
    
    tp = K.sum(y_pos * y_pred_pos)
    tn = K.sum(y_neg * y_pred_neg)
    
    fp = K.sum(y_neg * y_pred_pos)
    fn = K.sum(y_pos * y_pred_neg)
    
    numerator = (tp * tn - fp * fn)
    denominator = K.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
    
    return numerator / (denominator + K.epsilon())

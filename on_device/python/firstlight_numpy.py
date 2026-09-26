"""CPU ndarray tensor operations used by the unchanged V4 live tensorizer.

This does not implement inference or training. ONNX Runtime owns inference.
All model input shapes, data types and field calculations remain in the
reference tensorizer and are checked against the real PyTorch implementation.
"""
import numpy as np
from contextlib import nullcontext

bool = np.dtype("bool")
long = int64 = np.dtype("int64")
int32 = np.dtype("int32")
int8 = np.dtype("int8")
int16 = np.dtype("int16")
float32 = float = np.dtype("float32")
float16 = np.dtype("float16")
bfloat16 = "bfloat16"
uint8 = np.dtype("uint8")
dtype = np.dtype

class Tensor(np.ndarray):
    def __getitem__(self, key):
        value = super().__getitem__(key)
        return np.asarray(value).view(Tensor) if isinstance(value,np.generic) else value
    @property
    def device(self): return "cpu"
    @property
    def is_cuda(self): return False
    def cpu(self): return self
    def detach(self): return self
    def clone(self): return self.copy()
    def numpy(self): return np.asarray(self)
    def long(self): return self.astype(np.int64)
    def bool(self): return self.astype(np.bool_)
    def float(self): return self.astype(np.float32)
    def to(self, device=None, dtype=None, **kwargs):
        if device not in (None, "cpu"):
            dtype = device
        return self.astype(dtype) if dtype is not None else self
    def unsqueeze(self, dim): return np.expand_dims(self, dim).view(Tensor)
    def squeeze(self, dim=None): return np.squeeze(np.asarray(self), axis=dim).view(Tensor)
    def numel(self): return self.size
    def dim(self): return self.ndim
    def new_zeros(self, *shape): return zeros(*shape, dtype=self.dtype)
    def new_full(self, shape, value): return full(shape, value, dtype=self.dtype)
    def fill_(self, value): self.fill(value); return self
    def zero_(self): return self.fill_(0)
    def view(self, *shape):
        if len(shape)==1 and isinstance(shape[0],type) and issubclass(shape[0],np.ndarray):
            return np.ndarray.view(self,shape[0])
        return self.reshape(*shape)
    def expand(self, *shape): return np.broadcast_to(self,tuple(a if b==-1 else b for a,b in zip(self.shape,shape))).view(Tensor)
    def masked_fill(self, mask, value): return np.where(mask, value, self).view(Tensor)
    def masked_fill_(self, mask, value): self[mask] = value; return self
    def index_select(self, dim, index): return np.take(self,index,axis=dim).view(Tensor)
    def ne(self, other): return (self != other).view(Tensor)
    def eq(self, other): return (self == other).view(Tensor)
    def clamp(self, min=None, max=None): return np.clip(self,min,max).view(Tensor)
    def remainder(self, other): return np.remainder(self,other).view(Tensor)
    def flatten(self, start_dim=0, end_dim=-1):
        if end_dim<0: end_dim += self.ndim
        return self.reshape(*self.shape[:start_dim],-1,*self.shape[end_dim+1:])
    def sum(self, dim=None, keepdim=False, **kwargs): return np.asarray(np.asarray(self).sum(axis=dim,keepdims=keepdim,**kwargs)).view(Tensor)
    def any(self, dim=None, keepdim=False, **kwargs): return np.asarray(np.asarray(self).any(axis=dim,keepdims=keepdim,**kwargs)).view(Tensor)
    def all(self, dim=None, keepdim=False, **kwargs): return np.asarray(np.asarray(self).all(axis=dim,keepdims=keepdim,**kwargs)).view(Tensor)
    def is_floating_point(self): return self.dtype.kind == "f"
    def abs(self): return np.abs(self).view(Tensor)
    def gather(self, dim, index): return np.take_along_axis(self,np.asarray(index),axis=dim).view(Tensor)
    def nonzero(self, as_tuple=False):
        return tuple(x.view(Tensor) for x in np.nonzero(np.asarray(self))) if as_tuple else np.argwhere(np.asarray(self)).view(Tensor)
    def mean(self, dim=None, keepdim=False): return np.asarray(np.asarray(self).mean(axis=dim,keepdims=keepdim)).view(Tensor)

def _shape(shape): return shape[0] if len(shape)==1 and isinstance(shape[0],(tuple,list)) else shape
def tensor(data, dtype=None, device=None):
    if dtype is None:
        inferred=np.asarray(data).dtype
        dtype=np.float32 if inferred.kind=="f" else inferred
    return np.array(data,dtype=dtype).view(Tensor)
def from_numpy(data): return data.view(Tensor)
def as_tensor(data, dtype=None, device=None): return np.asarray(data,dtype=dtype).view(Tensor)
def zeros(*shape, dtype=np.float32, device=None): return np.zeros(_shape(shape),dtype=dtype).view(Tensor)
def ones(*shape, dtype=np.float32, device=None): return np.ones(_shape(shape),dtype=dtype).view(Tensor)
def full(shape, value, dtype=None, device=None): return np.full(shape,value,dtype=dtype).view(Tensor)
def zeros_like(value): return np.zeros_like(value).view(Tensor)
def ones_like(value): return np.ones_like(value).view(Tensor)
def full_like(value, fill_value): return np.full_like(value,fill_value).view(Tensor)
def cat(values, dim=0): return np.concatenate(values,axis=dim).view(Tensor)
def stack(values, dim=0): return np.stack(values,axis=dim).view(Tensor)
def arange(*args, dtype=None, device=None): return np.arange(*args,dtype=dtype).view(Tensor)
def where(condition, x, y): return np.where(condition,x,y).view(Tensor)
def isfinite(value): return np.isfinite(value).view(Tensor)
def any(value): return np.any(np.asarray(value))
def all(value): return np.all(np.asarray(value))
def unique(value): return np.unique(value).view(Tensor)
def div(value, other, rounding_mode=None):
    return (np.floor_divide(value,other) if rounding_mode=="floor" else np.divide(value,other)).view(Tensor)
def inference_mode(mode=True): return nullcontext()
def is_inference(value): return False
def equal(first,second): return np.array_equal(first,second)
def device(value):
    if value != "cpu": raise ValueError("Only CPU tensor packing is supported")
    return value

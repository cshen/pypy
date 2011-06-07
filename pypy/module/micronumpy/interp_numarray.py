from pypy.interpreter.baseobjspace import ObjSpace, W_Root, Wrappable
from pypy.interpreter.error import operationerrfmt
from pypy.interpreter.gateway import interp2app, unwrap_spec
from pypy.interpreter.typedef import TypeDef
from pypy.rlib import jit
from pypy.rpython.lltypesystem import lltype
from pypy.tool.sourcetools import func_with_new_name


def dummy1(v):
    assert isinstance(v, float)
    return v

def dummy2(v):
    assert isinstance(v, float)
    return v

TP = lltype.Array(lltype.Float, hints={'nolength': True})

numpy_driver = jit.JitDriver(greens = ['signature'],
                             reds = ['result_size', 'i', 'self', 'result'])

class Signature(object):
    def __init__(self):
        self.transitions = {}

    def transition(self, target):
        if target in self.transitions:
            return self.transitions[target]
        self.transitions[target] = new = Signature()
        return new

def add(v1, v2):
    return v1 + v2
def sub(v1, v2):
    return v1 - v2
def mul(v1, v2):
    return v1 * v2
def div(v1, v2):
    return v1 / v2

class BaseArray(Wrappable):
    def __init__(self):
        self.invalidates = []

    def invalidated(self):
        for arr in self.invalidates:
            arr.force_if_needed()
        self.invalidates = []

    def _binop_impl(function):
        signature = Signature()
        def impl(self, space, w_other):
            new_sig = self.signature.transition(signature)
            if isinstance(w_other, BaseArray):
                res = Call2(
                    function,
                    self,
                    w_other,
                    new_sig.transition(w_other.signature)
                )
                w_other.invalidates.append(res)
            else:
                w_other = FloatWrapper(space.float_w(w_other))
                res = Call2(
                    function,
                    self,
                    w_other,
                    new_sig.transition(w_other.signature)
                )
            self.invalidates.append(res)
            return space.wrap(res)
        return func_with_new_name(impl, "binop_%s_impl" % function.__name__)

    descr_add = _binop_impl(add)
    descr_sub = _binop_impl(sub)
    descr_mul = _binop_impl(mul)
    descr_div = _binop_impl(div)

    def get_concrete(self):
        raise NotImplementedError

    def descr_len(self, space):
        return self.get_concrete().descr_len(space)

    @unwrap_spec(item=int)
    def descr_getitem(self, space, item):
        return self.get_concrete().descr_getitem(space, item)

    @unwrap_spec(item=int, value=float)
    def descr_setitem(self, space, item, value):
        self.invalidated()
        return self.get_concrete().descr_setitem(space, item, value)


class FloatWrapper(BaseArray):
    """
    Intermediate class representing a float literal.
    """
    _immutable_fields_ = ["float_value"]
    signature = Signature()

    def __init__(self, float_value):
        BaseArray.__init__(self)
        self.float_value = float_value

    def find_size(self):
        raise ValueError

    def eval(self, i):
        return self.float_value

class VirtualArray(BaseArray):
    """
    Class for representing virtual arrays, such as binary ops or ufuncs
    """
    def __init__(self, signature):
        BaseArray.__init__(self)
        self.forced_result = None
        self.signature = signature

    def compute(self):
        i = 0
        signature = self.signature
        result_size = self.find_size()
        result = SingleDimArray(result_size)
        while i < result_size:
            numpy_driver.jit_merge_point(signature=signature,
                                         result_size=result_size, i=i,
                                         self=self, result=result)
            result.storage[i] = self.eval(i)
            i += 1
        return result

    def force_if_needed(self):
        if self.forced_result is None:
            self.forced_result = self.compute()

    def get_concrete(self):
        self.force_if_needed()
        return self.forced_result

    def eval(self, i):
        if self.forced_result is not None:
            return self.forced_result.eval(i)
        return self._eval(i)

class Call1(VirtualArray):
    _immutable_fields_ = ["function", "values"]

    def __init__(self, function, values, signature):
        VirtualArray.__init__(self, signature)
        self.function = function
        self.values = values

    def find_size(self):
        return self.values.find_size()

    def _eval(self, i):
        return self.function(self.values.eval(i))

class Call2(VirtualArray):
    """
    Intermediate class for performing binary operations.
    """
    _immutable_fields_ = ["function", "left", "right"]
    def __init__(self, function, left, right, signature):
        VirtualArray.__init__(self, signature)
        self.function = function
        self.left = left
        self.right = right

    def find_size(self):
        try:
            return self.left.find_size()
        except ValueError:
            pass
        return self.right.find_size()

    def _eval(self, i):
        lhs, rhs = self.left.eval(i), self.right.eval(i)
        return self.function(lhs, rhs)


class SingleDimArray(BaseArray):
    signature = Signature()

    def __init__(self, size):
        BaseArray.__init__(self)
        self.size = size
        self.storage = lltype.malloc(TP, size, zero=True,
                                     flavor='raw', track_allocation=False)
        # XXX find out why test_zjit explodes with trackign of allocations

    def get_concrete(self):
        return self

    def find_size(self):
        return self.size

    def eval(self, i):
        return self.storage[i]

    def getindex(self, space, item):
        if item >= self.size:
            raise operationerrfmt(space.w_IndexError,
              '%d above array size', item)
        if item < 0:
            item += self.size
        if item < 0:
            raise operationerrfmt(space.w_IndexError,
              '%d below zero', item)
        return item

    def descr_len(self, space):
        return space.wrap(self.size)

    @unwrap_spec(item=int)
    def descr_getitem(self, space, item):
        item = self.getindex(space, item)
        return space.wrap(self.storage[item])

    @unwrap_spec(item=int, value=float)
    def descr_setitem(self, space, item, value):
        item = self.getindex(space, item)
        self.invalidated()
        self.storage[item] = value

    def __del__(self):
        lltype.free(self.storage, flavor='raw')

def descr_new_numarray(space, w_type, w_size_or_iterable):
    l = space.listview(w_size_or_iterable)
    arr = SingleDimArray(len(l))
    i = 0
    for w_elem in l:
        arr.storage[i] = space.float_w(space.float(w_elem))
        i += 1
    return space.wrap(arr)

@unwrap_spec(ObjSpace, int)
def zeros(space, size):
    return space.wrap(SingleDimArray(size))


BaseArray.typedef = TypeDef(
    'numarray',
    __new__ = interp2app(descr_new_numarray),
    __len__ = interp2app(BaseArray.descr_len),
    __getitem__ = interp2app(BaseArray.descr_getitem),
    __setitem__ = interp2app(BaseArray.descr_setitem),

    __add__ = interp2app(BaseArray.descr_add),
    __sub__ = interp2app(BaseArray.descr_sub),
    __mul__ = interp2app(BaseArray.descr_mul),
    __div__ = interp2app(BaseArray.descr_div),
)
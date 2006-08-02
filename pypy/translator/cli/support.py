# some code has been stolen from genc
def string_literal(s):
    def char_repr(c):
        if c in '\\"': return '\\' + c
        if ' ' <= c < '\x7F': return c
        if c == '\n': return '\\n'
        if c == '\t': return '\\t'
        raise ValueError
    def line_repr(s):
        return ''.join([char_repr(c) for c in s])
    def array_repr(s):
        return ' '.join(['%x 00' % ord(c) for c in s]+['00'])

    try:
        return '"%s"' % line_repr(s)
    except ValueError:
        return "bytearray ( %s )" % array_repr(s)


class Tee(object):
    def __init__(self, *args):
        self.outfiles = args

    def write(self, s):
        for outfile in self.outfiles:
            outfile.write(s)

    def close(self):
        for outfile in self.outfiles:
            if outfile is not sys.stdout:
                outfile.close()

class Counter:
    def __init__(self):
        self.counters = {}

    def inc(self, *label):
        cur = self.counters.get(label, 0)
        self.counters[label] = cur+1

    def dump(self, filename):
        f = file(filename, 'w')
        keys = self.counters.keys()
        keys.sort()
        for key in keys:
            label = ', '.join([str(item) for item in key])
            f.write('%s: %d\n' % (label, self.counters[key]))
        f.close()

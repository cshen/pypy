from pypy.rpython.lltypesystem import lltype, llmemory
from pypy.rpython.memory.gc.stmgc import StmGC
from pypy.rpython.memory.gc.stmgc import GCFLAG_GLOBAL, GCFLAG_WAS_COPIED


S = lltype.GcStruct('S', ('a', lltype.Signed), ('b', lltype.Signed),
                         ('c', lltype.Signed))
ofs_a = llmemory.offsetof(S, 'a')

SR = lltype.GcForwardReference()
SR.become(lltype.GcStruct('SR', ('s1', lltype.Ptr(S)),
                                ('sr2', lltype.Ptr(SR)),
                                ('sr3', lltype.Ptr(SR))))


class FakeStmOperations:
    # The point of this class is to make sure about the distinction between
    # RPython code in the GC versus C code in translator/stm/src_stm.  This
    # class contains a fake implementation of what should be in C.  So almost
    # any use of 'self._gc' is wrong here: it's stmgc.py that should call
    # et.c, and not the other way around.

    threadnum = 0          # 0 = main thread; 1,2,3... = transactional threads

    def set_tls(self, tls):
        assert lltype.typeOf(tls) == llmemory.Address
        assert tls
        if self.threadnum == 0:
            assert not hasattr(self, '_tls_dict')
            self._tls_dict = {0: tls}
            self._tldicts = {0: {}}
            self._tldicts_iterators = {}
            self._transactional_copies = []
        else:
            self._tls_dict[self.threadnum] = tls
            self._tldicts[self.threadnum] = {}

    def get_tls(self):
        return self._tls_dict[self.threadnum]

    def del_tls(self):
        del self._tls_dict[self.threadnum]
        del self._tldicts[self.threadnum]

    def tldict_lookup(self, obj):
        assert lltype.typeOf(obj) == llmemory.Address
        assert obj
        tldict = self._tldicts[self.threadnum]
        return tldict.get(obj, llmemory.NULL)

    def tldict_add(self, obj, localobj):
        assert lltype.typeOf(obj) == llmemory.Address
        assert lltype.typeOf(localobj) == llmemory.Address
        tldict = self._tldicts[self.threadnum]
        assert obj not in tldict
        tldict[obj] = localobj

    def enum_tldict_start(self):
        it = self._tldicts[self.threadnum].iteritems()
        self._tldicts_iterators[self.threadnum] = [it, None, None]

    def enum_tldict_find_next(self):
        state = self._tldicts_iterators[self.threadnum]
        try:
            next_key, next_value = state[0].next()
        except StopIteration:
            state[1] = None
            state[2] = None
            del self._tldicts_iterators[self.threadnum]
            return False
        state[1] = next_key
        state[2] = next_value
        return True

    def enum_tldict_globalobj(self):
        state = self._tldicts_iterators[self.threadnum]
        assert state[1] is not None
        return state[1]

    def enum_tldict_localobj(self):
        state = self._tldicts_iterators[self.threadnum]
        assert state[2] is not None
        return state[2]

    def stm_read_word(self, obj, offset):
        hdr = self._gc.header(obj)
        if hdr.tid & GCFLAG_WAS_COPIED != 0:
            localobj = self.tldict_lookup(obj)
            if localobj:
                assert self._gc.header(localobj).tid & GCFLAG_GLOBAL == 0
                return (localobj + offset).signed[0]
        return 'stm_ll_read_word(%r, %r)' % (obj, offset)

    def stm_copy_transactional_to_raw(self, srcobj, dstobj, size):
        sizehdr = self._gc.gcheaderbuilder.size_gc_header
        srchdr = srcobj - sizehdr
        dsthdr = dstobj - sizehdr
        llmemory.raw_memcopy(srchdr, dsthdr, sizehdr)
        llmemory.raw_memcopy(srcobj, dstobj, size)
        self._transactional_copies.append((srcobj, dstobj))


def fake_get_size(obj):
    TYPE = obj.ptr._TYPE.TO
    if isinstance(TYPE, lltype.GcStruct):
        return llmemory.sizeof(TYPE)
    else:
        assert 0

def fake_trace(obj, callback, arg):
    TYPE = obj.ptr._TYPE.TO
    if TYPE == S:
        ofslist = []     # no pointers in S
    elif TYPE == SR:
        ofslist = [llmemory.offsetof(SR, 's1'),
                   llmemory.offsetof(SR, 'sr2'),
                   llmemory.offsetof(SR, 'sr3')]
    else:
        assert 0
    for ofs in ofslist:
        addr = obj + ofs
        if addr.address[0]:
            callback(addr, arg)


class TestBasic:
    GCClass = StmGC

    def setup_method(self, meth):
        from pypy.config.pypyoption import get_pypy_config
        config = get_pypy_config(translating=True).translation
        self.gc = self.GCClass(config, FakeStmOperations(),
                               translated_to_c=False)
        self.gc.stm_operations._gc = self.gc
        self.gc.DEBUG = True
        self.gc.get_size = fake_get_size
        self.gc.trace = fake_trace
        self.gc.setup()

    def teardown_method(self, meth):
        for key in self.gc.stm_operations._tls_dict.keys():
            if key != 0:
                self.gc.stm_operations.threadnum = key
                self.gc.teardown_thread()

    # ----------
    # test helpers
    def malloc(self, STRUCT):
        gcref = self.gc.malloc_fixedsize_clear(123, llmemory.sizeof(STRUCT))
        realobj = lltype.cast_opaque_ptr(lltype.Ptr(STRUCT), gcref)
        addr = llmemory.cast_ptr_to_adr(realobj)
        return realobj, addr
    def select_thread(self, threadnum):
        self.gc.stm_operations.threadnum = threadnum
        if threadnum not in self.gc.stm_operations._tls_dict:
            self.gc.setup_thread(False)
    def gcsize(self, S):
        return (llmemory.raw_malloc_usage(llmemory.sizeof(self.gc.HDR)) +
                llmemory.raw_malloc_usage(llmemory.sizeof(S)))
    def checkflags(self, obj, must_have_global, must_have_was_copied,
                              must_have_version='?'):
        if lltype.typeOf(obj) != llmemory.Address:
            obj = llmemory.cast_ptr_to_adr(obj)
        hdr = self.gc.header(obj)
        assert (hdr.tid & GCFLAG_GLOBAL != 0) == must_have_global
        assert (hdr.tid & GCFLAG_WAS_COPIED != 0) == must_have_was_copied
        if must_have_version != '?':
            assert hdr.version == must_have_version

    def test_gc_creation_works(self):
        pass

    def test_allocate_bump_pointer(self):
        a3 = self.gc.allocate_bump_pointer(3)
        a4 = self.gc.allocate_bump_pointer(4)
        a5 = self.gc.allocate_bump_pointer(5)
        a6 = self.gc.allocate_bump_pointer(6)
        assert a4 - a3 == 3
        assert a5 - a4 == 4
        assert a6 - a5 == 5

    def test_malloc_fixedsize_clear(self):
        gcref = self.gc.malloc_fixedsize_clear(123, llmemory.sizeof(S))
        s = lltype.cast_opaque_ptr(lltype.Ptr(S), gcref)
        assert s.a == 0
        assert s.b == 0
        gcref2 = self.gc.malloc_fixedsize_clear(123, llmemory.sizeof(S))
        assert gcref2 != gcref

    def test_malloc_main_vs_thread(self):
        gcref = self.gc.malloc_fixedsize_clear(123, llmemory.sizeof(S))
        obj = llmemory.cast_ptr_to_adr(gcref)
        assert self.gc.header(obj).tid & GCFLAG_GLOBAL != 0
        #
        self.select_thread(1)
        gcref = self.gc.malloc_fixedsize_clear(123, llmemory.sizeof(S))
        obj = llmemory.cast_ptr_to_adr(gcref)
        assert self.gc.header(obj).tid & GCFLAG_GLOBAL == 0

    def test_reader_direct(self):
        s, s_adr = self.malloc(S)
        assert self.gc.header(s_adr).tid & GCFLAG_GLOBAL != 0
        s.a = 42
        value = self.gc.read_signed(s_adr, ofs_a)
        assert value == 'stm_ll_read_word(%r, %r)' % (s_adr, ofs_a)
        #
        self.select_thread(1)
        s, s_adr = self.malloc(S)
        assert self.gc.header(s_adr).tid & GCFLAG_GLOBAL == 0
        self.gc.header(s_adr).tid |= GCFLAG_WAS_COPIED   # should be ignored
        s.a = 42
        value = self.gc.read_signed(s_adr, ofs_a)
        assert value == 42

    def test_reader_through_dict(self):
        s, s_adr = self.malloc(S)
        s.a = 42
        #
        self.select_thread(1)
        t, t_adr = self.malloc(S)
        t.a = 84
        #
        self.gc.header(s_adr).tid |= GCFLAG_WAS_COPIED
        self.gc.stm_operations._tldicts[1][s_adr] = t_adr
        #
        value = self.gc.read_signed(s_adr, ofs_a)
        assert value == 84

    def test_write_barrier_exists(self):
        self.select_thread(1)
        t, t_adr = self.malloc(S)
        obj = self.gc.write_barrier(t_adr)     # local object
        assert obj == t_adr
        #
        self.select_thread(0)
        s, s_adr = self.malloc(S)
        #
        self.select_thread(1)
        self.gc.header(s_adr).tid |= GCFLAG_WAS_COPIED
        self.gc.header(t_adr).tid |= GCFLAG_WAS_COPIED
        self.gc.stm_operations._tldicts[1][s_adr] = t_adr
        obj = self.gc.write_barrier(s_adr)     # global copied object
        assert obj == t_adr
        assert self.gc.stm_operations._transactional_copies == []

    def test_write_barrier_new(self):
        self.select_thread(0)
        s, s_adr = self.malloc(S)
        s.a = 12
        s.b = 34
        #
        self.select_thread(1)
        t_adr = self.gc.write_barrier(s_adr) # global object, not copied so far
        assert t_adr != s_adr
        t = t_adr.ptr
        assert t.a == 12
        assert t.b == 34
        assert self.gc.stm_operations._transactional_copies == [(s_adr, t_adr)]
        #
        u_adr = self.gc.write_barrier(s_adr)  # again
        assert u_adr == t_adr
        #
        u_adr = self.gc.write_barrier(u_adr)  # local object
        assert u_adr == t_adr

    def test_commit_transaction_empty(self):
        self.select_thread(1)
        s, s_adr = self.malloc(S)
        t, t_adr = self.malloc(S)
        self.gc.collector.commit_transaction()    # no roots
        main_tls = self.gc.main_thread_tls
        assert main_tls.nursery_free == main_tls.nursery_start   # empty

    def test_commit_transaction_no_references(self):
        s, s_adr = self.malloc(S)
        s.b = 12345
        self.select_thread(1)
        t_adr = self.gc.write_barrier(s_adr)   # make a local copy
        t = llmemory.cast_adr_to_ptr(t_adr, lltype.Ptr(S))
        assert s != t
        assert self.gc.header(t_adr).version == s_adr
        t.b = 67890
        #
        main_tls = self.gc.main_thread_tls
        assert main_tls.nursery_free != main_tls.nursery_start  # contains s
        old_value = main_tls.nursery_free
        #
        self.gc.collector.commit_transaction()
        #
        assert main_tls.nursery_free == old_value    # no new object
        assert s.b == 12345     # not updated by the GC code
        assert t.b == 67890     # still valid

    def test_commit_transaction_with_one_reference(self):
        sr, sr_adr = self.malloc(SR)
        assert sr.s1 == lltype.nullptr(S)
        assert sr.sr2 == lltype.nullptr(SR)
        self.select_thread(1)
        tr_adr = self.gc.write_barrier(sr_adr)   # make a local copy
        tr = llmemory.cast_adr_to_ptr(tr_adr, lltype.Ptr(SR))
        assert sr != tr
        t, t_adr = self.malloc(S)
        t.b = 67890
        assert tr.s1 == lltype.nullptr(S)
        assert tr.sr2 == lltype.nullptr(SR)
        tr.s1 = t
        #
        main_tls = self.gc.main_thread_tls
        old_value = main_tls.nursery_free
        #
        self.gc.collector.commit_transaction()
        #
        assert main_tls.nursery_free - old_value == self.gcsize(S)

    def test_commit_transaction_with_graph(self):
        sr1, sr1_adr = self.malloc(SR)
        sr2, sr2_adr = self.malloc(SR)
        self.select_thread(1)
        tr1_adr = self.gc.write_barrier(sr1_adr)   # make a local copy
        tr2_adr = self.gc.write_barrier(sr2_adr)   # make a local copy
        tr1 = llmemory.cast_adr_to_ptr(tr1_adr, lltype.Ptr(SR))
        tr2 = llmemory.cast_adr_to_ptr(tr2_adr, lltype.Ptr(SR))
        tr3, tr3_adr = self.malloc(SR)
        tr4, tr4_adr = self.malloc(SR)
        t, t_adr = self.malloc(S)
        #
        tr1.sr2 = tr3; tr1.sr3 = tr1
        tr2.sr2 = tr3; tr2.sr3 = tr3
        tr3.sr2 = tr4; tr3.sr3 = tr2
        tr4.sr2 = tr3; tr4.sr3 = tr3; tr4.s1 = t
        #
        for i in range(4):
            self.malloc(S)     # forgotten
        #
        main_tls = self.gc.main_thread_tls
        old_value = main_tls.nursery_free
        #
        self.gc.collector.commit_transaction()
        #
        assert main_tls.nursery_free - old_value == (
            self.gcsize(SR) + self.gcsize(SR) + self.gcsize(S))
        #
        sr3_adr = self.gc.header(tr3_adr).version
        sr4_adr = self.gc.header(tr4_adr).version
        s_adr   = self.gc.header(t_adr  ).version
        assert len(set([sr3_adr, sr4_adr, s_adr])) == 3
        #
        sr3 = llmemory.cast_adr_to_ptr(sr3_adr, lltype.Ptr(SR))
        sr4 = llmemory.cast_adr_to_ptr(sr4_adr, lltype.Ptr(SR))
        s   = llmemory.cast_adr_to_ptr(s_adr,   lltype.Ptr(S))
        assert tr1.sr2 == sr3; assert tr1.sr3 == sr1     # roots: local obj
        assert tr2.sr2 == sr3; assert tr2.sr3 == sr3     #        is modified
        assert sr3.sr2 == sr4; assert sr3.sr3 == sr2     # non-roots: global
        assert sr4.sr2 == sr3; assert sr4.sr3 == sr3     #      obj is modified
        assert sr4.s1 == s
        #
        self.checkflags(sr1, 1, 1)
        self.checkflags(sr2, 1, 1)
        self.checkflags(sr3, 1, 0, llmemory.NULL)
        self.checkflags(sr4, 1, 0, llmemory.NULL)
        self.checkflags(s  , 1, 0, llmemory.NULL)

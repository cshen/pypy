from pypy.conftest import gettestobjspace

class TestShadowTracking(object):
    def setup_class(cls):
        cls.space = gettestobjspace(**{"objspace.std.withshadowtracking": True})

    def test_simple_shadowing(self):
        space = self.space
        w_inst = space.appexec([], """():
            class A(object):
                def f(self):
                    return 42
            a = A()
            return a
        """)
        assert not w_inst.w__dict__.implementation.shadows_anything
        space.appexec([w_inst], """(a):
            a.g = "foo"
        """)
        assert not w_inst.w__dict__.implementation.shadows_anything
        space.appexec([w_inst], """(a):
            a.f = "foo"
        """)
        assert w_inst.w__dict__.implementation.shadows_anything

    def test_shadowing_via__dict__(self):
        space = self.space
        w_inst = space.appexec([], """():
            class A(object):
                def f(self):
                    return 42
            a = A()
            return a
        """)
        assert not w_inst.w__dict__.implementation.shadows_anything
        space.appexec([w_inst], """(a):
            a.__dict__["g"] = "foo"
        """)
        assert not w_inst.w__dict__.implementation.shadows_anything
        space.appexec([w_inst], """(a):
            a.__dict__["f"] = "foo"
        """)
        assert w_inst.w__dict__.implementation.shadows_anything

    def test_changing__dict__(self):
        space = self.space
        w_inst = space.appexec([], """():
            class A(object):
                def f(self):
                    return 42
            a = A()
            return a
        """)
        assert not w_inst.w__dict__.implementation.shadows_anything
        space.appexec([w_inst], """(a):
            a.__dict__ = {}
        """)
        assert w_inst.w__dict__.implementation.shadows_anything

    def test_changing__class__(self):
        space = self.space
        w_inst = space.appexec([], """():
            class A(object):
                def f(self):
                    return 42
            a = A()
            return a
        """)
        assert not w_inst.w__dict__.implementation.shadows_anything
        space.appexec([w_inst], """(a):
            class B(object):
                def g(self):
                    return 42
            a.__class__ = B
        """)
        assert w_inst.w__dict__.implementation.shadows_anything

class AppTestShadowTracking(object):
    def setup_class(cls):
        cls.space = gettestobjspace(**{"objspace.std.withshadowtracking": True})

    def test_shadowtracking_does_not_blow_up(self):
        class A(object):
            def f(self):
                return 42
        a = A()
        assert a.f() == 42
        a.f = lambda : 43
        assert a.f() == 43

class AppTestMethodCaching(AppTestShadowTracking):
    def setup_class(cls):
        cls.space = gettestobjspace(
            **{"objspace.std.withmethodcachecounter": True})

    def test_mix_classes(self):
        import pypymagic
        class A(object):
            def f(self):
                return 42
        class B(object):
            def f(self):
                return 43
        class C(object):
            def f(self):
                return 44
        l = [A(), B(), C()] * 10
        pypymagic.reset_method_cache_counter()
        for i, a in enumerate(l):
            assert a.f() == 42 + i % 3
        cache_counter = pypymagic.method_cache_counter("f")
        print cache_counter
        assert cache_counter[1] >= 3 # should be (27, 3)
        assert sum(cache_counter) == 30

    def test_class_that_cannot_be_cached(self):
        import pypymagic
        class metatype(type):
            pass
        class A(object):
            __metaclass__ = metatype
            def f(self):
                return 42

        class B(object):
            def f(self):
                return 43
        class C(object):
            def f(self):
                return 44
        l = [A(), B(), C()] * 10
        pypymagic.reset_method_cache_counter()
        for i, a in enumerate(l):
            assert a.f() == 42 + i % 3
        cache_counter = pypymagic.method_cache_counter("f")
        print cache_counter
        assert cache_counter[1] >= 2 # should be (18, 2)
        assert sum(cache_counter) == 20
 
    def test_change_methods(self):
        import pypymagic
        class A(object):
            def f(self):
                return 42
        l = [A()] * 10
        pypymagic.reset_method_cache_counter()
        for i, a in enumerate(l):
            assert a.f() == 42 + i
            A.f = eval("lambda self: %s" % (42 + i + 1, ))
        cache_counter = pypymagic.method_cache_counter("f")
        print cache_counter
        assert cache_counter == (0, 10)

    def test_subclasses(self):
        import pypymagic
        class A(object):
            def f(self):
                return 42
        class B(object):
            def f(self):
                return 43
        class C(A):
            pass
        l = [A(), B(), C()] * 10
        pypymagic.reset_method_cache_counter()
        for i, a in enumerate(l):
            assert a.f() == 42 + (i % 3 == 1)
        cache_counter = pypymagic.method_cache_counter("f")
        print cache_counter
        assert cache_counter[1] >= 3 # should be (27, 3)
        assert sum(cache_counter) == 30
  

import unittest
from schedula import Dispatcher
from schedula.ext.dsp_directive import DispatcherDirective, PLOT
from docutils.statemachine import ViewList

try:
    from .util import TestApp, Struct
except SystemError:
    from test.docs.util import TestApp, Struct
app = None

directive = options = None

_warnings = []


def setup_test(**kw):
    global options, directive, _warnings, app
    _warnings = []
    options = Struct(
        des = True,
        opt = PLOT,
        data = True,
        func = True,
        code = True,
        dsp = True,
        inherited_members = False,
        undoc_members = False,
        private_members = False,
        special_members = False,
        imported_members = False,
        show_inheritance = False,
        noindex = False,
        annotation = None,
        synopsis = '',
        platform = '',
        deprecated = False,
        members = [],
        member_order = 'alphabetic',
        exclude_members = set(),
    )

    directive = Struct(
        env = app.builder.env,
        genopt = options,
        result = ViewList(),
        warn = warnfunc,
        filename_set = set(),
        **kw
    )


def warnfunc(msg):
    _warnings.append(msg)


def assert_equal_items(test, items):
    global directive
    items = list(reversed(items))
    it = iter(directive.result)
    while items:
        item = items.pop()
        test.assertEqual(item, next(it), 'item %r not found in result or not in'
                                         ' the correct order' % item)
    del directive.result[:]


class TestDispatcherDirective(unittest.TestCase):
    def setUp(self):
        global app
        app = TestApp()
        app.builder.env.app = app
        app.builder.env.temp_data['docname'] = 'dummy'

    def tearDown(self):
        global app
        app.cleanup()

    def test_format_signature(self):
        setup_test()

        def formatsig(objtype, name, obj, args, retann):
            global directive
            inst = DispatcherDirective._registry[objtype](directive, name)
            inst.fullname = name
            inst.doc_as_attr = False  # for class objtype
            inst.object = obj
            inst.objpath = [name]
            inst.args = args
            inst.retann = retann
            res = inst.format_signature()
            return res

        # no signatures for dispatchers
        dsp = Dispatcher()
        self.assertEqual(formatsig('dispatcher', 'dsp', dsp, None, None), '')

    def test_get_doc(self):
        setup_test()
        def getdocl(objtype, obj, name, encoding=None):
            global directive
            inst = DispatcherDirective._registry[objtype](directive, 'tmp')

            inst.objpath = [name]
            inst.object = obj
            ds = inst.get_doc(encoding)
            # for testing purposes, concat them and strip the empty line at the end
            res = sum(ds, [])[:-1]
            return res

        # objects without docstring
        dsp_local = Dispatcher()
        self.assertEqual(getdocl('dispatcher', dsp_local, 'dsp_local'), [])

        dsp_local = Dispatcher(description='Description')
        res = getdocl('dispatcher', dsp_local, 'dsp_local')
        self.assertEqual(res, ['Description'])

        dsp_local.__doc__ = 'First line\n\nOther\n  lines'
        res = getdocl('dispatcher', dsp_local, 'dsp_local')
        self.assertEqual(res, ['First line', '', 'Other', '  lines'])

    def test_docstring_property_processing(self):
        setup_test()
        def genarate_docstring(objtype, name, **kw):
            global directive
            inst = DispatcherDirective._registry[objtype](directive, name)
            inst.generate(**kw)
            results = list(directive.result)
            del directive.result[:]
            return results

        results = genarate_docstring('dispatcher', __name__ + '.dsp')
        assert '.. py:data:: dsp' in results
        assert '   :module: %s' % __name__ in results
        assert '   :annotation:  = Pippo' in results

    def test_import(self):
        import docutils.statemachine

        setup_test(
            arguments=['dsp'],
            options={'opt': "graph_attr={'ratio': '1'}", 'code': True},
            content=docutils.statemachine.StringList(
                [" >>> s = Dispatcher(name='Dispatcher')",
                 " >>> f = s.add_function('fun', fun2, ['a'], ['b'])"]),
            content_offset=0)

        def assert_result(self, items, objtype, name, **kw):
            global directive
            inst = DispatcherDirective._registry[objtype](directive, name)
            inst.generate(**kw)
            assert len(_warnings) == 0, _warnings
            assert_equal_items(self, items)

        res= ['', '',
              "    >>> s = Dispatcher(name='Dispatcher')",
              "    >>> f = s.add_function('fun', fun2, ['a'], ['b'])",
              '   ', '   ', '   ',
              '   .. graphviz:: _build/_dispatchers/dispatcher-41dfbb6d292c8abba905c15874724d3d9d3a3b0d.gv',
              '   ',
              "   .. csv-table:: **Dispatcher's data**",
              '   ',
              '      ":obj:`a <>`", "Nice e."',
              '      ":obj:`b <>`", "Nice f."',
              '   ',
              "   .. csv-table:: **Dispatcher's functions**",
              '   ',
              '      ":func:`fun <%s.fun2>`", "Fun2"' % __name__,
              '   ']
        directive.env.ref_context['py:module'] = __name__
        assert_result(self, res, 'dispatcher', 's')


    def test_generate(self):
        setup_test()

        def assert_result(self, items, objtype, name, **kw):
            global directive
            inst = DispatcherDirective._registry[objtype](directive, name)
            inst.generate(**kw)
            assert len(_warnings) == 0, _warnings
            assert_equal_items(self, items)

        directive.env.ref_context['py:module'] = __name__
        res = [
            '',
            '.. py:data:: dsp',
            '   :module: %s' % __name__,
            '   :annotation:  = Pippo',
            '',
            '   Docstring 1',
            '   ',
            '   good',
            '   ',
            '   .. graphviz:: _build/_dispatchers/dispatcher-cca2a722432426abafca076a7ccfde40b7bae49b.gv',
            '   ',
            "   .. csv-table:: **Pippo's data**",
            '   ',
            '      ":obj:`a <>`", "Description of a"',
            '      ":obj:`b <>`", "Nice e."',
            '      ":obj:`c <>`", "Nice f."',
            '      ":obj:`d <>`", "Other args."',
            '      ":obj:`e <>`", "Nice arg."',
            '      ":obj:`sink <>`", "Sink node of the dispatcher that '
            'collects all unused outputs."',
            '      ":obj:`start <>`", "Starting node that identifies '
            'initial inputs of the workflow."',
            '   ',
            "   .. csv-table:: **Pippo's functions**",
            '   ',
            '      ":func:`fun1 <>`", "Fun1"',
            '      ":func:`fun2 <%s.fun2>`", "Fun2"' % __name__,
            '      ":func:`fun3 <%s.fun2>`", "Fun3"' % __name__,
            '   ']

        assert_result(self, res, 'dispatcher', 'dsp')
        res[1] = '.. py:data:: dsp_1'
        res[5] = '   Docstring 2'

        assert_result(self, res, 'dispatcher', 'dsp_1')

        res[1] = '.. py:data:: dsp_2'
        res[5] = '   Docstring 3'
        assert_result(self, res, 'dispatcher', 'dsp_2')


def fun2(e, my_args, *args):
    """
    Fun2

    error

    :param None e:
        Nice e.

        error

    :param my_args:
        Nice arg.

        error
    :type my_args: None

    :param args:
        Other args.

        error
    :type args: None

    :returns:
        Nice f.

        error
    :rtype: None
    """
    return

#: Docstring 1
#:
#: good
dsp = Dispatcher(name='Pippo', description='Docstring 2\n\ngood')
dsp.add_data(data_id='a', description='Description of a\n\nerror')
dsp.add_function(function_id='fun1', description='Fun1\n\nerror')
dsp.add_function('fun2', fun2, ['b', 'e', 'd'], ['c'])
dsp.add_function('fun3', fun2, description='Fun3\n\nerror')


dsp_1 = dsp

dsp_2 = dsp
"""
Docstring 3

good
"""

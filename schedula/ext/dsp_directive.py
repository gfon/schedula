import schedula.utils as dsp_utl
import shutil
import os.path as osp
from sphinx.ext.autodoc import *
from sphinx.ext.graphviz import *
from schedula import Dispatcher
from doctest import DocTestParser, DocTestRunner, NORMALIZE_WHITESPACE, ELLIPSIS
from hashlib import sha1


# ------------------------------------------------------------------------------
# Doctest handling
# ------------------------------------------------------------------------------


def contains_doctest(text):
    try:
        # check if it's valid Python as-is
        compile(text, '<string>', 'exec')
        return False
    except SyntaxError:
        pass
    r = re.compile(r'^\s*>>>', re.M)
    m = r.search(text)
    return bool(m)


# ------------------------------------------------------------------------------
# Auto dispatcher content
# ------------------------------------------------------------------------------


def get_grandfather_content(content, level=2):
    if content.parent and level:
        return get_grandfather_content(content.parent, level - 1)
    return content, get_grandfather_offset(content)


def get_grandfather_offset(content):
    if content.parent:
        return get_grandfather_offset(content.parent) + content.parent_offset
    return 0


def _import_docstring(documenter):
    if getattr(documenter.directive, 'content', None):
        # noinspection PyBroadException
        try:
            import textwrap

            content = documenter.directive.content

            def get_code(source, c=''):
                s = "\n%s" % c
                return textwrap.dedent(s.join(map(str, source)))

            is_doctest = contains_doctest(get_code(content))
            offset = documenter.directive.content_offset
            if is_doctest:
                parent, parent_offset = get_grandfather_content(content)
                parent = parent[:offset + len(content) - parent_offset]
                code = get_code(parent)
            else:
                code = get_code(content, '>>> ')

            parser = DocTestParser()
            runner = DocTestRunner(verbose=0,
                                   optionflags=NORMALIZE_WHITESPACE | ELLIPSIS)

            glob = {}
            exec('import %s as mdl\n' % documenter.modname, glob)
            glob = glob['mdl'].__dict__
            tests = parser.get_doctest(code, glob, '', '', 0)
            runner.run(tests, clear_globs=False)

            documenter.object = tests.globs[documenter.name]
            documenter.code = content
            documenter.is_doctest = True
            return True
        except:
            return False


def _description(lines, dsp, documenter):
    docstring = dsp.__doc__

    if documenter.objpath and documenter.analyzer:
        attr_docs = documenter.analyzer.find_attr_docs()
        key = ('.'.join(documenter.objpath[:-1]), documenter.objpath[-1])
        if key in attr_docs:
            docstring = attr_docs[key]

    if isinstance(docstring, str):
        docstring = docstring.split('\n') + ['']

    lines.extend(docstring)


def _code(lines, documenter):
    if documenter.code:
        if documenter.is_doctest:
            lines += [row.rstrip() for row in documenter.code]
        else:
            lines.extend(['.. code-block:: python', ''])
            lines.extend(['    %s' % r.rstrip() for r in documenter.code])

        lines.append('')


def _plot(lines, dsp, dot_view_opt, documenter):
    hashkey = (documenter.modname + str(documenter.code) +
               str(sorted(dot_view_opt.items()))).encode('utf-8')
    fname = 'dispatcher-%s' % sha1(hashkey).hexdigest()
    env = documenter.env

    dspdir = osp.join(env.srcdir, env.config.dispatchers_out_dir)
    fpath = '%s.gv' % osp.join(dspdir, fname)
    if not osp.isfile(fpath):
        smap = dsp.plot(**dot_view_opt)
        folder = next(iter(smap))
        folder._name = folder.sitemap.foldername = fname
        dot = folder.dot(smap.rules(index=False))
        dot.sitemap.render(directory=dspdir, index=False)
        dot.save(fpath, '')

    dsource = osp.dirname(osp.join(env.srcdir, env.docname))
    path = osp.relpath(fpath, dsource).replace('\\', '/')
    lines.extend(['.. graphviz:: %s' % path, ''])


def _table_heather(lines, title, dsp_name):
    q = 's' if dsp_name and dsp_name[-1] != 's' else ''
    lines.extend(['.. csv-table:: **%s\'%s %s**' % (dsp_name, q, title), ''])


def _data(lines, dsp):
    if isinstance(dsp, dsp_utl.SubDispatch):
        dsp = dsp.dsp

    data = sorted(dsp.data_nodes.items())
    if data:
        _table_heather(lines, 'data', dsp.name)
        from schedula.utils.des import get_summary
        for k, v in data:
            des, link = dsp.search_node_description(k)

            link = ':obj:`%s <%s>`' % (_node_name(str(k)), link)
            str_format = u'   "%s", "%s"'
            lines.append(str_format % (link, get_summary(des.split('\n'))))

        lines.append('')


def _functions(lines, dsp, node_type='function'):
    if isinstance(dsp, dsp_utl.SubDispatch):
        dsp = dsp.dsp
    def check_fun(node_attr):
        if node_attr['type'] not in ('function', 'dispatcher'):
            return False

        if 'function' in node_attr:
            func = dsp_utl.parent_func(node_attr['function'])
            c = isinstance(func, (Dispatcher, dsp_utl.SubDispatch))
            return c if node_type == 'dispatcher' else not c
        return node_attr['type'] == node_type

    fun = [v for v in sorted(dsp.nodes.items()) if check_fun(v[1])]

    if fun:
        _table_heather(lines, '%ss' % node_type, dsp.name)

        for k, v in fun:
            des, full_name = dsp.search_node_description(k)
            lines.append(u'   ":func:`%s <%s>`", "%s"' % (k, full_name, des))
        lines.append('')


def _node_name(name):
    return name.replace('<', '\<').replace('>', '\>')


PLOT = object()


def _dsp2dot_option(arg):
    """Used to convert the :dmap: option to auto directives."""

    # noinspection PyUnusedLocal
    def map_args(*args, **kwargs):
        from schedula.utils.base import Base
        a = inspect.signature(Base.plot).bind(None, *args, **kwargs).arguments
        a.popitem(last=False)
        return a

    kw = eval('map_args(%s)' % arg)

    return kw if kw else PLOT


# ------------------------------------------------------------------------------
# Graphviz override
# ------------------------------------------------------------------------------


class img(nodes.General, nodes.Element):
    pass


class _Graphviz(Graphviz):
    img_opt = {
        'height': directives.length_or_unitless,
        'width': directives.length_or_percentage_or_unitless,
    }
    option_spec = Graphviz.option_spec.copy()
    option_spec.update(img_opt)

    def run(self):
        node = super(_Graphviz, self).run()[0]
        node['img_opt'] = dsp_utl.selector(self.img_opt, self.options,
                                           allow_miss=True)
        if self.arguments:
            env = self.state.document.settings.env
            argument = search_image_for_language(self.arguments[0], env)
            dirpath = osp.splitext(env.relfn2path(argument)[1])[0]
            node['dirpath'] = dirpath if osp.isdir(dirpath) else None
        else:
            node['dirpath'] = None
        return [node]


def render_dot_html(self, node, code, options, prefix='dispatcher',
                    imgcls=None, alt=None):
    format = self.builder.config.graphviz_output_format
    try:
        if format not in ('png', 'svg'):
            raise GraphvizError("graphviz_output_format must be one of 'png', "
                                "'svg', but is %r" % format)
        fname, outfn = render_dot(self, code, options, format, prefix)
    except GraphvizError as exc:
        self.builder.warn('dot code %r: ' % code + str(exc))
        raise nodes.SkipNode
    dirpath = node['dirpath']
    if dirpath:
        outd = osp.join(osp.dirname(outfn), osp.split(dirpath)[-1])
        if not osp.isdir(outd):
            shutil.copytree(dirpath, outd)

    extend = []
    if fname is None:
        extend += [self.encode(code)]
    else:
        if alt is None:
            alt = node.get('alt', self.encode(code).strip())

        n = img('', src=fname, alt=alt, **node['img_opt'])
        e = []
        if format != 'svg':
            if imgcls:
                n['class'] = imgcls
            with open(outfn + '.map', 'rb') as mapfile:
                imgmap = mapfile.readlines()
                if len(imgmap) != 2:
                    # has a map: get the name of the map and connect the parts
                    mname = mapname_re.match(imgmap[0].decode('utf-8')).group(1)
                    n['usemap'] = '#%s' % mname
                    e += [item.decode('utf-8') for item in imgmap]
        extend += ['<a href="{}">{}</a>'.format(fname, n)] + e

    self.body.extend(extend)

    raise nodes.SkipNode


def html_visit_graphviz(self, node):
    try:
        warn_for_deprecated_option(self, node)
    except NameError:  # sphinx==1.3.5
        pass
    render_dot_html(self, node, node['code'], node['options'])


# ------------------------------------------------------------------------------
# Registration hook
# ------------------------------------------------------------------------------


class DispatcherDocumenter(DataDocumenter):
    """
    Specialized Documenter subclass for dispatchers.
    """

    objtype = 'dispatcher'
    directivetype = 'data'
    option_spec = dict(DataDocumenter.option_spec)
    option_spec.update(_Graphviz.option_spec)
    option_spec.update({
        'description': bool_option,
        'opt': _dsp2dot_option,
        'code': bool_option,
        'data': bool_option,
        'func': bool_option,
        'dsp': bool_option,
    })
    default_opt = {
        'depth': 0,
        'view': False
    }
    code = None
    is_doctest = False

    def get_real_modname(self):
        return self.modname

    @classmethod
    def can_document_member(cls, member, membername, isattr, parent):
        return (isinstance(parent, ModuleDocumenter)
                and isinstance(member, (Dispatcher, dsp_utl.SubDispatch)))

    def add_directive_header(self, sig):
        if not self.code:
            if not self.options.annotation:
                self.options.annotation = ' = %s' % self.object.name
            super(DispatcherDocumenter, self).add_directive_header(sig)

    def import_object(self):
        if getattr(self.directive, 'arguments', None):
            if _import_docstring(self):
                return True
        self.is_doctest = False
        self.code = None
        return DataDocumenter.import_object(self)

    def format_signature(self):
        return ''

    def add_content(self, more_content, no_docstring=False):
        # noinspection PyUnresolvedReferences
        sourcename = self.get_sourcename()
        dsp = self.object
        opt = self.options

        dot_view_opt = self.default_opt.copy()
        if opt.opt and opt.opt is not PLOT:
            dot_view_opt.update(opt.opt)

        lines = []

        if opt.code:
            _code(lines, self)

        if not opt or opt.des:
            _description(lines, dsp, self)

        _plot(lines, dsp, dot_view_opt, self)

        if not opt or opt.data:
            _data(lines, dsp)

        if not opt or opt.func:
            _functions(lines, dsp)

        if not opt or opt.dsp:
            _functions(lines, dsp, 'dispatcher')

        for line in lines:
            self.add_line(line, sourcename)


class DispatcherDirective(AutoDirective):
    _default_flags = {'des', 'opt', 'data', 'func', 'dsp', 'code', 'annotation'}

    def __init__(self, *args, **kwargs):
        super(DispatcherDirective, self).__init__(*args, **kwargs)
        if args[0] == 'dispatcher':
            self.name = 'autodispatcher'


def add_autodocumenter(app, cls):
    app.debug('[app] adding autodocumenter: %r', cls)

    from sphinx.ext import autodoc

    autodoc.add_documenter(cls)

    app.add_directive('auto' + cls.objtype, DispatcherDirective)


def setup(app):
    app.setup_extension('sphinx.ext.autodoc')
    app.setup_extension('sphinx.ext.graphviz')
    try:
        app.add_node(graphviz, html=(html_visit_graphviz, None), override=True)
    except TypeError:  # sphinx 1.3.5
        app.add_node(graphviz, html=(html_visit_graphviz, None))
    directives._directives.pop('graphviz', None)
    app.add_directive('graphviz', _Graphviz)
    add_autodocumenter(app, DispatcherDocumenter)
    app.add_directive('dispatcher', DispatcherDirective)
    app.add_config_value('dispatchers_out_dir', '_build/_dispatchers', 'html')
